# Copyright (C) 2026 Bao.TangDuc
#
# This file is part of cctv-summary.
#
# cctv-summary is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# cctv-summary is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with cctv-summary. If not, see <https://www.gnu.org/licenses/>.

"""Camera fault detection: is the camera working, never mind what walked past.

Summarization answers "what happened"; this answers "can this camera still see
anything". They are different questions and fail in opposite directions - a
sprayed-over lens produces a beautifully quiet summary - so the checks live
apart from the motion pipeline and get their own subcommand.

Every metric is read from the same downscaled grayscale frame the motion path
uses, so one decode pass covers all five faults and the numbers mean the same
thing across resolutions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike

from cctv_summary.progress import NullProgress, Progress
from cctv_summary.summarize import comparison_edge_for, downscale_to_gray
from cctv_summary.video import FALLBACK_FPS, VideoError, iter_frames, probe

# Fault kinds, in the order a report lists them. Strings rather than an enum so
# they survive into JSON and log lines unchanged.
DARK: str = "dark"
BLURRY: str = "blurry"
OBSTRUCTED: str = "obstructed"
FROZEN: str = "frozen"
SHAKEN: str = "shaken"

FAULT_KINDS: tuple[str, ...] = (DARK, BLURRY, OBSTRUCTED, FROZEN, SHAKEN)

# What each fault's reported value is measuring, for the report to label.
FAULT_METRICS: dict[str, str] = {
    DARK: "luminance",
    BLURRY: "detail",
    OBSTRUCTED: "flat",
    FROZEN: "changed",
    SHAKEN: "changed",
}

# Mean grey level (0-255) below which a frame carries no usable picture. IR
# illuminated night footage still sits well above this; a failed sensor or a
# lens cap does not. Measured: a healthy corridor sat at 109, a blacked-out
# one at 6.
DEFAULT_DARK_LUMINANCE: float = 20.0

# Variance of the Laplacian below which the image has no edges worth the name.
# Measured at the comparison size, so it does not move with source resolution.
# The same corridor scored 126 in focus and 0.9 badly defocused.
DEFAULT_BLUR_DETAIL: float = 15.0

# Share of the frame with no local contrast at all that reads as a covered lens.
# Measured: healthy 0.69, badly blurred 0.82, lens sprayed over 1.00. The gap
# between 0.82 and 1.00 is what keeps a soft-focus camera from being reported
# as a covered one - they need different fixes.
DEFAULT_FLAT_FRACTION: float = 0.9

# Share of pixels changing between consecutive frames that no subject in the
# scene could account for; only the camera itself moving does that. Measured on
# a 320x240 corridor: a person walking peaked at 0.019, while knocking the view
# sideways by 12% of its width reached 0.221. Set from the healthy floor rather
# than the knock size, because displacement does *not* map monotonically onto
# change - a self-similar scene realigns with itself, so a 50% shift scored
# 0.227 where a 22% shift scored 0.369.
DEFAULT_SHAKE_CHANGE: float = 0.2

# A fault has to persist to be worth reporting: single frames flicker.
DEFAULT_MIN_FAULT_SECONDS: float = 1.0

# Freezes need longer confirmation. A couple of repeated frames is ordinary
# codec behaviour; seconds of a byte-identical picture is a dead feed.
DEFAULT_FREEZE_SECONDS: float = 2.0

# Per-pixel delta counting as movement when looking for a knocked camera.
SHAKE_TOLERANCE: int = 25

# Box size for local contrast, and the standard deviation below which a patch
# counts as featureless.
LOCAL_WINDOW: int = 8
FLAT_STD: float = 3.0


@dataclass(frozen=True)
class FrameMetrics:
    """Everything the checks need from a single frame, measured in one pass."""

    luminance: float  # mean grey level, 0-255
    detail: float  # variance of the Laplacian; low means out of focus
    flat_fraction: float  # share of the frame with no local contrast
    changed: float  # share of pixels differing from the previous frame
    identical: bool  # byte-for-byte the same as the previous frame


@dataclass(frozen=True)
class Fault:
    """A stretch of footage where the camera itself was not working."""

    kind: str
    start: int
    end: int  # exclusive
    fps: float = 0.0
    # Worst reading of the metric that defines this fault, for the report. The
    # metric is implied by `kind`; see FAULT_METRICS.
    value: float = 0.0

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"Fault must span at least one frame, got {self!r}")
        if self.kind not in FAULT_KINDS:
            raise ValueError(f"Unknown fault kind: {self.kind!r}")

    @property
    def frames(self) -> int:
        return self.end - self.start

    @property
    def start_seconds(self) -> float:
        return self.start / self.fps if self.fps > 0 else 0.0

    @property
    def end_seconds(self) -> float:
        return self.end / self.fps if self.fps > 0 else 0.0

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.fps if self.fps > 0 else 0.0

    @property
    def metric(self) -> str:
        return FAULT_METRICS[self.kind]


@dataclass(frozen=True)
class Diagnosis:
    """Outcome of a check pass."""

    total: int
    fps: float = 0.0
    faults: tuple[Fault, ...] = ()
    # Medians over the whole clip, so a report can show how close healthy
    # footage sits to the thresholds rather than only naming what tripped.
    luminance_median: float = 0.0
    detail_median: float = 0.0
    # The knobs the run used, so a result can be explained without the caller
    # re-threading arguments.
    dark_luminance: float = DEFAULT_DARK_LUMINANCE
    blur_detail: float = DEFAULT_BLUR_DETAIL
    flat_fraction: float = DEFAULT_FLAT_FRACTION
    shake_change: float = DEFAULT_SHAKE_CHANGE
    min_fault_seconds: float = DEFAULT_MIN_FAULT_SECONDS
    freeze_seconds: float = DEFAULT_FREEZE_SECONDS
    comparison_edge: int = 0

    @property
    def healthy(self) -> bool:
        return not self.faults

    @property
    def source_seconds(self) -> float:
        if self.fps <= 0:
            return 0.0
        return self.total / self.fps

    @property
    def faulty_frames(self) -> int:
        """Frames covered by at least one fault, counting overlaps once."""
        covered: set[int] = set()
        for fault in self.faults:
            covered.update(range(fault.start, fault.end))
        return len(covered)

    def of_kind(self, kind: str) -> tuple[Fault, ...]:
        return tuple(fault for fault in self.faults if fault.kind == kind)


def flat_fraction(
    gray: np.ndarray,
    *,
    window: int = LOCAL_WINDOW,
    flat_std: float = FLAT_STD,
) -> float:
    """Share of a frame with no local contrast, 0.0-1.0.

    Local variance via box filters rather than a per-pixel loop: a covered lens
    is featureless *everywhere*, which a global measure like the Laplacian
    variance cannot separate from a merely plain scene.
    """
    if gray.size == 0:
        raise ValueError("Cannot measure an empty frame")

    values = gray.astype(np.float32)
    mean = cv2.boxFilter(values, -1, (window, window), normalize=True)
    mean_square = cv2.boxFilter(values * values, -1, (window, window), normalize=True)
    # Clipped because the two box filters can disagree in the last bit and
    # leave a variance a hair below zero.
    variance = np.clip(mean_square - mean * mean, 0.0, None)
    return float(np.count_nonzero(variance < flat_std**2) / variance.size)


def detail_score(gray: np.ndarray) -> float:
    """Variance of the Laplacian: high for crisp edges, near zero for a blur."""
    if gray.size == 0:
        raise ValueError("Cannot measure an empty frame")
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def frame_metrics(
    frames: Iterable[MatLike],
    *,
    comparison_edge: int | None = None,
    tolerance: int = SHAKE_TOLERANCE,
) -> Iterator[FrameMetrics]:
    """Measure every frame once, yielding all five checks' raw inputs.

    ``comparison_edge`` caps the size frames are measured at; without one each
    frame's own resolution picks the tier, exactly as motion scoring does.
    """
    previous: np.ndarray | None = None

    for frame in frames:
        gray = downscale_to_gray(frame, edge=comparison_edge)

        if previous is None or previous.shape != gray.shape:
            changed, identical = 0.0, False
        else:
            delta = cv2.absdiff(gray, previous)
            changed = np.count_nonzero(delta > tolerance) / delta.size
            # Exact equality, not "close": live sensors always dither, so a
            # byte-identical frame means the stream repeated rather than that
            # the scene held still.
            identical = not delta.any()

        yield FrameMetrics(
            luminance=float(gray.mean()),
            detail=detail_score(gray),
            flat_fraction=flat_fraction(gray),
            changed=float(changed),
            identical=identical,
        )
        previous = gray


def _runs(
    flags: Sequence[bool], *, fps: float, min_seconds: float
) -> list[tuple[int, int]]:
    """Contiguous true spans lasting at least ``min_seconds``.

    Faults are not padded or merged the way motion events are: a fault's edges
    are evidence about the camera, and widening them would misreport when the
    picture actually came back.
    """
    minimum = max(int(round(min_seconds * fps)), 1) if fps > 0 else 1

    spans: list[tuple[int, int]] = []
    start: int | None = None

    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            if index - start >= minimum:
                spans.append((start, index))
            start = None

    if start is not None and len(flags) - start >= minimum:
        spans.append((start, len(flags)))

    return spans


def _faults_from(
    metrics: Sequence[FrameMetrics],
    *,
    kind: str,
    fps: float,
    min_seconds: float,
    predicate: Callable[[FrameMetrics], bool],
    reading: Callable[[FrameMetrics], float],
    worst: Callable[[Iterable[float]], float],
) -> list[Fault]:
    """Turn one per-frame predicate into reported faults."""
    flags = [predicate(metric) for metric in metrics]
    return [
        Fault(
            kind=kind,
            start=start,
            end=end,
            fps=fps,
            value=worst(reading(metric) for metric in metrics[start:end]),
        )
        for start, end in _runs(flags, fps=fps, min_seconds=min_seconds)
    ]


def detect_faults(
    metrics: Sequence[FrameMetrics] | Iterable[FrameMetrics],
    *,
    fps: float,
    dark_luminance: float = DEFAULT_DARK_LUMINANCE,
    blur_detail: float = DEFAULT_BLUR_DETAIL,
    flat_share: float = DEFAULT_FLAT_FRACTION,
    shake_change: float = DEFAULT_SHAKE_CHANGE,
    min_fault_seconds: float = DEFAULT_MIN_FAULT_SECONDS,
    freeze_seconds: float = DEFAULT_FREEZE_SECONDS,
) -> list[Fault]:
    """Group per-frame measurements into the faults they add up to.

    Pure: takes readings and returns spans, so the logic is testable from
    hand-built metrics instead of synthesised footage.

    Faults are reported independently and may overlap: a capped lens is both
    dark and featureless, and the tool cannot tell a cap from a dead sensor, so
    it says both rather than guessing. The one exception is blur, which is
    suppressed when the picture is too dark or too flat to judge focus at all.
    """
    readings = tuple(metrics)

    found: list[Fault] = []
    found += _faults_from(
        readings,
        kind=DARK,
        fps=fps,
        min_seconds=min_fault_seconds,
        predicate=lambda m: m.luminance < dark_luminance,
        reading=lambda m: m.luminance,
        worst=min,
    )
    found += _faults_from(
        readings,
        kind=BLURRY,
        fps=fps,
        min_seconds=min_fault_seconds,
        # Focus is only diagnosable on a picture that has light and contrast to
        # begin with. A black or covered frame has no edges either, and
        # reporting it as "out of focus" would send someone to adjust a lens
        # that is working fine.
        predicate=lambda m: (
            m.detail < blur_detail
            and m.luminance >= dark_luminance
            and m.flat_fraction < flat_share
        ),
        reading=lambda m: m.detail,
        worst=min,
    )
    found += _faults_from(
        readings,
        kind=OBSTRUCTED,
        fps=fps,
        min_seconds=min_fault_seconds,
        predicate=lambda m: m.flat_fraction >= flat_share,
        reading=lambda m: m.flat_fraction,
        worst=max,
    )
    found += _faults_from(
        readings,
        kind=FROZEN,
        fps=fps,
        min_seconds=freeze_seconds,
        predicate=lambda m: m.identical,
        reading=lambda m: m.changed,
        worst=max,
    )
    found += _faults_from(
        readings,
        kind=SHAKEN,
        fps=fps,
        # A knock lasts a frame or two by nature, so it is exempt from the
        # minimum that keeps the steady-state faults from flickering.
        min_seconds=0.0,
        predicate=lambda m: m.changed >= shake_change,
        reading=lambda m: m.changed,
        worst=max,
    )

    found.sort(key=lambda fault: (fault.start, FAULT_KINDS.index(fault.kind)))
    return found


def diagnose_video(
    source: str | Path,
    *,
    dark_luminance: float = DEFAULT_DARK_LUMINANCE,
    blur_detail: float = DEFAULT_BLUR_DETAIL,
    flat_share: float = DEFAULT_FLAT_FRACTION,
    shake_change: float = DEFAULT_SHAKE_CHANGE,
    min_fault_seconds: float = DEFAULT_MIN_FAULT_SECONDS,
    freeze_seconds: float = DEFAULT_FREEZE_SECONDS,
    comparison_edge: int | None = None,
    progress: Progress | None = None,
) -> Diagnosis:
    """Check ``source`` for camera faults and report what it finds.

    Unlike summarizing, this decodes the source **once**: every check reads the
    same prepared frame, and nothing has to be written back out.

    Pass a ``progress`` reporter to follow long runs; without one the call
    stays silent.
    """
    if dark_luminance < 0 or dark_luminance > 255:
        raise VideoError(
            f"Dark threshold must be between 0 and 255, got {dark_luminance}"
        )
    if blur_detail < 0:
        raise VideoError(f"Blur threshold cannot be negative, got {blur_detail}")
    if not 0.0 < flat_share <= 1.0:
        raise VideoError(
            f"Obstruction share must be above 0 and at most 1, got {flat_share}"
        )
    if not 0.0 < shake_change <= 1.0:
        raise VideoError(
            f"Shake share must be above 0 and at most 1, got {shake_change}"
        )
    if min_fault_seconds < 0:
        raise VideoError(
            f"Minimum fault length cannot be negative, got {min_fault_seconds}"
        )
    if freeze_seconds < 0:
        raise VideoError(f"Freeze length cannot be negative, got {freeze_seconds}")
    if comparison_edge is not None and comparison_edge < 1:
        raise VideoError(
            f"Comparison edge must be at least 1 pixel, got {comparison_edge}"
        )

    info = probe(source)
    fps = info.fps if info.fps > 0 else FALLBACK_FPS
    reporter = progress if progress is not None else NullProgress()

    edge = (
        comparison_edge
        if comparison_edge is not None
        else comparison_edge_for(max(info.width, 1), max(info.height, 1))
    )

    reporter.start("checking", info.frame_count)
    readings: list[FrameMetrics] = []
    for metric in frame_metrics(iter_frames(source), comparison_edge=edge):
        readings.append(metric)
        reporter.advance()
    reporter.finish()

    faults = detect_faults(
        readings,
        fps=fps,
        dark_luminance=dark_luminance,
        blur_detail=blur_detail,
        flat_share=flat_share,
        shake_change=shake_change,
        min_fault_seconds=min_fault_seconds,
        freeze_seconds=freeze_seconds,
    )

    return Diagnosis(
        total=len(readings),
        fps=fps,
        faults=tuple(faults),
        luminance_median=_median(reading.luminance for reading in readings),
        detail_median=_median(reading.detail for reading in readings),
        dark_luminance=dark_luminance,
        blur_detail=blur_detail,
        flat_fraction=flat_share,
        shake_change=shake_change,
        min_fault_seconds=min_fault_seconds,
        freeze_seconds=freeze_seconds,
        comparison_edge=edge,
    )


def _median(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
