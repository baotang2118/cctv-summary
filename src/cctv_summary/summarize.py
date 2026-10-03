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

"""Motion-event summarization: keep what happens, drop the empty hours.

CCTV is mostly nothing. Rather than judging frames one at a time, this scores
every frame for motion, finds the stretches where something is happening, and
keeps each of those as a continuous clip. Selecting whole events instead of
individual frames keeps the action watchable and preserves when it happened.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike

from cctv_summary.mask import Region, build_mask
from cctv_summary.overlay import draw_summarized
from cctv_summary.progress import NullProgress, Progress
from cctv_summary.video import (
    FALLBACK_FPS,
    VideoError,
    iter_frames,
    probe,
    write_frames,
)

# Fraction of pixels that must move for a frame to count as motion. A person
# crossing a corridor covers only about 1% of the frame, so this sits an order
# of magnitude below what frame-to-frame differencing might suggest.
DEFAULT_THRESHOLD: float = 0.01

# An event ends only once motion falls below this share of the start threshold,
# which stops a walker pausing mid-frame from splitting one event into three.
STOP_RATIO: float = 0.5

DEFAULT_WINDOW: int = 30

# Per-pixel intensity delta (0-255) that counts as "this pixel moved".
DEFAULT_TOLERANCE: int = 25

# Seconds of footage kept either side of an event, so people are seen entering
# and leaving rather than appearing mid-stride.
DEFAULT_PAD_SECONDS: float = 2.0

# Events shorter than this are treated as noise rather than something happening.
DEFAULT_MIN_EVENT_SECONDS: float = 1.0

# How long motion must stay low before an event is considered over.
DEFAULT_COOLDOWN_SECONDS: float = 1.0

# Bounds and iteration count for the `--target` threshold search. The range
# spans the useful thresholds for everything from a locked-off camera to
# handheld footage; 18 geometric steps narrow that to well under a percent.
SOLVER_MIN_THRESHOLD: float = 1e-4
SOLVER_MAX_THRESHOLD: float = 0.5
SOLVER_STEPS: int = 18

# Frames are compared at a reduced long-edge size: full-resolution diffs are
# wasteful and noisier without changing the decision much. One fixed size is
# wrong across cameras, though. Scores are a *share* of pixels, so they barely
# shift with scale, but a small distant figure stops registering at all:
# area averaging spreads its contrast across the pixels it is squeezed into
# until nothing clears the per-pixel tolerance. Measured on a 4K frame, a
# 10x22px figure 30 levels brighter than its background scores exactly 0.0 at
# 320 and registers at 480. So the size steps up with the source's vertical
# resolution, in tiers of (vertical resolution, comparison long edge) checked
# in order. Anything above the last tier uses MAX_COMPARISON_EDGE.
COMPARISON_EDGE_TIERS: tuple[tuple[int, int], ...] = (
    (720, 256),
    (1080, 320),
    (1440, 480),
)

MAX_COMPARISON_EDGE: int = 640


def comparison_edge_for(width: int, height: int) -> int:
    """Long-edge size that frames of this resolution should be compared at."""
    if width <= 0 or height <= 0:
        raise ValueError(f"Frame size must be positive, got {width}x{height}")

    # Keyed on the short edge so the tiers mean what their names say: 1080 is
    # the "1080p" tier whether the source is 1920x1080 or an ultrawide.
    vertical = min(width, height)
    for limit, edge in COMPARISON_EDGE_TIERS:
        if vertical <= limit:
            return edge
    return MAX_COMPARISON_EDGE


def comparison_size(width: int, height: int, edge: int) -> tuple[int, int]:
    """Size a frame of ``width`` x ``height`` is compared at, as ``(w, h)``.

    Shared with :func:`downscale_to_gray` so a mask built from these dimensions
    lines up with the frames it will be applied to, pixel for pixel.
    """
    if width < 1 or height < 1:
        raise ValueError(f"Frame size must be positive, got {width}x{height}")
    if edge < 1:
        raise ValueError(f"Comparison edge must be at least 1 pixel, got {edge}")

    longest = max(height, width)
    if longest <= edge:
        return width, height

    scale = edge / longest
    return max(int(width * scale), 1), max(int(height * scale), 1)


def downscale_to_gray(frame: MatLike, *, edge: int | None = None) -> np.ndarray:
    """Downscale and grayscale a frame for cheap, noise-tolerant comparison.

    ``edge`` caps the longest side; without one it is chosen from the frame's
    own resolution by :func:`comparison_edge_for`.
    """
    height, width = frame.shape[:2]
    if height == 0 or width == 0:
        raise ValueError("Cannot compare an empty frame")

    if edge is None:
        edge = comparison_edge_for(width, height)
    elif edge < 1:
        raise ValueError(f"Comparison edge must be at least 1 pixel, got {edge}")

    target = comparison_size(width, height, edge)
    if target != (width, height):
        frame = cv2.resize(frame, target, interpolation=cv2.INTER_AREA)

    if frame.ndim == 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame


def change_score(
    current: np.ndarray,
    reference: np.ndarray,
    *,
    tolerance: int = DEFAULT_TOLERANCE,
    mask: np.ndarray | None = None,
) -> float:
    """Fraction of pixels (0.0-1.0) that moved between two prepared frames.

    Both inputs must already be grayscale and the same shape; use
    :func:`downscale_to_gray` first.

    ``mask`` restricts which pixels get a vote. The share is taken over the
    *masked* area, not the whole frame: dividing by the full area instead would
    scale every score down by however much was excluded, silently invalidating
    a calibrated threshold the moment a region is added.
    """
    if current.shape != reference.shape:
        raise ValueError(
            f"Frames must match to be compared, got {current.shape} and "
            f"{reference.shape}"
        )

    delta = cv2.absdiff(current.astype(np.uint8), reference.astype(np.uint8))
    moved = delta > tolerance

    if mask is None:
        return np.count_nonzero(moved) / delta.size

    if mask.shape != delta.shape:
        raise ValueError(
            f"Mask must match the compared frames, got {mask.shape} and {delta.shape}"
        )

    active = np.count_nonzero(mask)
    if active == 0:
        raise ValueError("Mask leaves no pixels to compare")
    return np.count_nonzero(moved & mask) / active


class RollingBackground:
    """Mean of the most recent ``size`` greyscale frames.

    The mean is maintained incrementally: each new frame is added to a running
    total and the one falling out of the window is subtracted, so producing an
    average costs the same no matter how large the window is. Rebuilding it
    from the whole window every frame made this the slowest part of
    summarizing by a wide margin.

    The total is kept as float64, which represents every integer below 2**53
    exactly. A window of pixel values cannot sum anywhere near that, so the
    running total stays exact and never drifts.
    """

    def __init__(self, size: int = DEFAULT_WINDOW) -> None:
        if size < 1:
            raise ValueError(f"Window size must be at least 1, got {size}")
        self.size = size
        self._frames: deque[np.ndarray] = deque(maxlen=size)
        self._total: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def filled(self) -> bool:
        """True once the window holds a full complement of frames."""
        return len(self._frames) == self.size

    @property
    def average(self) -> np.ndarray | None:
        """Mean frame, or ``None`` while the window is still empty."""
        if self._total is None:
            return None
        return (self._total / len(self._frames)).astype(np.uint8)

    def add(self, frame: np.ndarray) -> None:
        """Append a greyscale frame, evicting the oldest once full."""
        if self._total is None:
            self._total = np.zeros(frame.shape, dtype=np.float64)
        elif frame.shape != self._total.shape:
            raise ValueError(
                f"Frames must match to be averaged, got {frame.shape} and "
                f"{self._total.shape}"
            )

        if len(self._frames) == self.size:
            self._total -= self._frames[0]

        # Stored as a copy because the total is updated now rather than when
        # the average is read. A caller reusing its buffer would otherwise
        # make the eventual subtraction remove different values than were
        # added, corrupting the total permanently.
        self._frames.append(frame.copy())
        self._total += self._frames[-1]


@dataclass(frozen=True)
class Event:
    """A stretch of footage where something was happening."""

    start: int
    end: int  # exclusive
    fps: float = 0.0

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"Event must span at least one frame, got {self!r}")

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

    def overlaps(self, other: Event) -> bool:
        return self.start <= other.end and other.start <= self.end


@dataclass(frozen=True)
class SummaryStats:
    """Outcome of a summarization pass."""

    total: int
    kept: int
    fps: float = 0.0
    events: tuple[Event, ...] = ()
    threshold: float = DEFAULT_THRESHOLD
    # Set when --target solved the threshold, so callers can report which
    # number was actually used and whether the goal was reachable.
    target_ratio: float | None = None
    # The knobs the run actually used, recorded so a report or manifest can
    # say how a result was produced without the caller re-threading arguments.
    # comparison_edge is the resolved tier, never the `auto` that asked for it.
    pad_seconds: float = DEFAULT_PAD_SECONDS
    min_event_seconds: float = DEFAULT_MIN_EVENT_SECONDS
    window: int = DEFAULT_WINDOW
    tolerance: int = DEFAULT_TOLERANCE
    comparison_edge: int = MAX_COMPARISON_EDGE
    watch: tuple[Region, ...] = ()
    ignore: tuple[Region, ...] = ()

    @property
    def dropped(self) -> int:
        return self.total - self.kept

    @property
    def kept_ratio(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.kept / self.total

    @property
    def source_seconds(self) -> float:
        if self.fps <= 0:
            return 0.0
        return self.total / self.fps

    @property
    def summary_seconds(self) -> float:
        if self.fps <= 0:
            return 0.0
        return self.kept / self.fps


def motion_scores(
    frames: Iterable[MatLike],
    *,
    window: int = DEFAULT_WINDOW,
    tolerance: int = DEFAULT_TOLERANCE,
    comparison_edge: int | None = None,
    mask: np.ndarray | None = None,
) -> Iterator[float]:
    """Yield the fraction of pixels moving in each frame, 0.0-1.0.

    Motion is measured against a rolling background rather than the previous
    frame, so a slow walker registers for as long as they are crossing rather
    than only when they move quickly.

    ``comparison_edge`` caps the size frames are compared at; without one each
    frame's own resolution picks the tier.

    ``mask`` restricts which pixels vote. It applies at scoring time only: the
    rolling background still sees whole frames, which keeps its running total
    exact and costs nothing, since masked pixels simply never get counted.
    """
    background = RollingBackground(window)

    for frame in frames:
        gray = downscale_to_gray(frame, edge=comparison_edge)
        average = background.average
        score = (
            change_score(gray, average, tolerance=tolerance, mask=mask)
            if average is not None
            else 0.0
        )
        background.add(gray)
        yield score


def detect_events(
    scores: Sequence[float] | Iterable[float],
    *,
    fps: float,
    threshold: float = DEFAULT_THRESHOLD,
    pad_seconds: float = DEFAULT_PAD_SECONDS,
    min_event_seconds: float = DEFAULT_MIN_EVENT_SECONDS,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
) -> list[Event]:
    """Group a motion signal into padded events.

    Uses hysteresis: an event opens when motion exceeds ``threshold`` and only
    closes once motion has stayed below half that for ``cooldown_seconds``. A
    single threshold would chop one walker into several events every time they
    slowed down.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"Threshold must be between 0 and 1, got {threshold}")
    if fps <= 0:
        raise ValueError(f"fps must be greater than zero, got {fps}")
    if pad_seconds < 0:
        raise ValueError(f"Padding cannot be negative, got {pad_seconds}")

    values = list(scores)
    if not values:
        return []

    stop_threshold = threshold * STOP_RATIO
    cooldown = max(round(cooldown_seconds * fps), 1)
    pad = max(round(pad_seconds * fps), 0)
    min_frames = max(round(min_event_seconds * fps), 1)

    spans: list[tuple[int, int]] = []
    start: int | None = None
    quiet = 0

    for index, score in enumerate(values):
        if start is None:
            if score >= threshold:
                start = index
                quiet = 0
            continue

        if score >= stop_threshold:
            quiet = 0
            continue

        quiet += 1
        if quiet >= cooldown:
            spans.append((start, index - quiet + 1))
            start = None
            quiet = 0

    if start is not None:
        spans.append((start, len(values)))

    # Discard blips before padding, so padding cannot rescue camera noise.
    spans = [span for span in spans if span[1] - span[0] >= min_frames]

    padded = [
        (max(begin - pad, 0), min(finish + pad, len(values))) for begin, finish in spans
    ]

    merged: list[tuple[int, int]] = []
    for span in padded:
        if merged and span[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], span[1]))
        else:
            merged.append(span)

    return [Event(start=begin, end=finish, fps=fps) for begin, finish in merged]


def keep_ratio_for(
    scores: Sequence[float],
    *,
    fps: float,
    threshold: float,
    pad_seconds: float = DEFAULT_PAD_SECONDS,
    min_event_seconds: float = DEFAULT_MIN_EVENT_SECONDS,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
) -> float:
    """Fraction of the clip that ``threshold`` would keep."""
    if not scores:
        return 0.0

    events = detect_events(
        scores,
        fps=fps,
        threshold=threshold,
        pad_seconds=pad_seconds,
        min_event_seconds=min_event_seconds,
        cooldown_seconds=cooldown_seconds,
    )
    return sum(event.frames for event in events) / len(scores)


def smallest_possible_ratio(
    frames: int,
    *,
    fps: float,
    pad_seconds: float = DEFAULT_PAD_SECONDS,
    min_event_seconds: float = DEFAULT_MIN_EVENT_SECONDS,
) -> float:
    """Share of the clip a single shortest event already occupies.

    Padding and the minimum event length quantise what is reachable: on a short
    clip one unavoidable event can exceed a small target, and no threshold will
    do better.
    """
    if frames <= 0 or fps <= 0:
        return 0.0

    shortest = (min_event_seconds + 2 * pad_seconds) * fps
    return min(shortest / frames, 1.0)


def solve_threshold(
    scores: Sequence[float],
    *,
    fps: float,
    target_ratio: float,
    pad_seconds: float = DEFAULT_PAD_SECONDS,
    min_event_seconds: float = DEFAULT_MIN_EVENT_SECONDS,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    steps: int = SOLVER_STEPS,
) -> float:
    """Find the threshold that keeps roughly ``target_ratio`` of the clip.

    Raising the threshold keeps less, so the ratio falls monotonically and can
    be bisected. The search is geometric because useful thresholds span orders
    of magnitude — 0.001 and 0.1 are both reasonable for different cameras, and
    stepping linearly would spend every step in the wrong decade.

    A video cannot say how much of itself is worth keeping, so this asks the
    caller for that judgement and then solves for it.
    """
    if not 0.0 < target_ratio <= 1.0:
        raise ValueError(f"Target must be above 0 and at most 1, got {target_ratio}")
    if fps <= 0:
        raise ValueError(f"fps must be greater than zero, got {fps}")
    if not scores:
        return DEFAULT_THRESHOLD

    # One shortest event already occupies a fixed share of the clip, so a
    # smaller target cannot be met. Aiming below it only drives the threshold
    # up until nothing survives, which is worse than the smallest real summary.
    floor = smallest_possible_ratio(
        len(scores),
        fps=fps,
        pad_seconds=pad_seconds,
        min_event_seconds=min_event_seconds,
    )
    goal = max(target_ratio, floor)

    def ratio(threshold: float) -> float:
        return keep_ratio_for(
            scores,
            fps=fps,
            threshold=threshold,
            pad_seconds=pad_seconds,
            min_event_seconds=min_event_seconds,
            cooldown_seconds=cooldown_seconds,
        )

    low, high = SOLVER_MIN_THRESHOLD, SOLVER_MAX_THRESHOLD

    # Nothing clears even the lowest threshold: the clip is essentially still.
    if ratio(low) <= 0.0:
        return low

    for _ in range(steps):
        middle = (low * high) ** 0.5
        if ratio(middle) > goal:
            low = middle
        else:
            high = middle

    # `low` is the last threshold known to keep at least the goal. `high` may
    # keep nothing at all, so prefer the end that is guaranteed to produce a
    # summary rather than the midpoint between them.
    return low if ratio(high) <= 0.0 else (low * high) ** 0.5


def summarize_video(
    source: str | Path,
    destination: str | Path | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    target_ratio: float | None = None,
    window: int = DEFAULT_WINDOW,
    tolerance: int = DEFAULT_TOLERANCE,
    pad_seconds: float = DEFAULT_PAD_SECONDS,
    min_event_seconds: float = DEFAULT_MIN_EVENT_SECONDS,
    comparison_edge: int | None = None,
    watch: Sequence[Region] = (),
    ignore: Sequence[Region] = (),
    progress: Progress | None = None,
) -> SummaryStats:
    """Write a copy of ``source`` containing only its motion events.

    With ``destination=None`` the footage is analysed but nothing is written,
    which is what ``--dry-run`` uses. Pass a ``progress`` reporter to follow
    long runs; without one the call stays silent.

    ``target_ratio`` replaces ``threshold`` with a solved one that keeps about
    that share of the clip. A video cannot say how much of itself is worth
    keeping, so the caller states the goal and the threshold follows.

    ``comparison_edge`` overrides the resolution tier motion is measured at;
    without one it follows the source, which is what ``auto`` means on the CLI.

    ``watch`` and ``ignore`` narrow *where* motion counts: an allow-list and a
    deny-list of fractional rectangles. They answer the localised noise the
    global knobs cannot, such as a road in the corner or a ticking clock
    overlay. Only the decision is masked; the written clip is untouched.

    The source is decoded twice: once to score motion, once to write. Padding
    reaches backwards from the moment motion is noticed, and buffering an hour
    of frames to look behind is not viable.
    """
    if not 0.0 <= threshold <= 1.0:
        raise VideoError(f"Threshold must be between 0 and 1, got {threshold}")
    if target_ratio is not None and not 0.0 < target_ratio <= 1.0:
        raise VideoError(f"Target must be above 0 and at most 1, got {target_ratio}")
    if window < 1:
        raise VideoError(f"Window must be at least 1 frame, got {window}")
    if not 0 <= tolerance <= 255:
        raise VideoError(f"Tolerance must be between 0 and 255, got {tolerance}")
    if pad_seconds < 0:
        raise VideoError(f"Padding cannot be negative, got {pad_seconds}")
    if min_event_seconds < 0:
        raise VideoError(
            f"Minimum event length cannot be negative, got {min_event_seconds}"
        )
    if comparison_edge is not None and comparison_edge < 1:
        raise VideoError(
            f"Comparison edge must be at least 1 pixel, got {comparison_edge}"
        )

    info = probe(source)
    fps = info.fps if info.fps > 0 else FALLBACK_FPS
    reporter = progress if progress is not None else NullProgress()

    # Resolved once from the container rather than per frame, so every score in
    # the run is measured at the same scale even if a frame arrives oddly sized.
    edge = (
        comparison_edge
        if comparison_edge is not None
        else comparison_edge_for(max(info.width, 1), max(info.height, 1))
    )

    # Built once at the size frames are compared at, so it lines up pixel for
    # pixel without being rescaled per frame.
    mask_width, mask_height = comparison_size(
        max(info.width, 1), max(info.height, 1), edge
    )
    mask = build_mask(mask_width, mask_height, watch=tuple(watch), ignore=tuple(ignore))

    reporter.start("analysing", info.frame_count)
    scores = []
    for score in motion_scores(
        iter_frames(source),
        window=window,
        tolerance=tolerance,
        comparison_edge=edge,
        mask=mask,
    ):
        scores.append(score)
        reporter.advance()
    reporter.finish()

    resolved_threshold = threshold
    if target_ratio is not None:
        # Pass one already scored every frame, so the search runs on the exact
        # distribution and costs no extra decoding.
        resolved_threshold = solve_threshold(
            scores,
            fps=fps,
            target_ratio=target_ratio,
            pad_seconds=pad_seconds,
            min_event_seconds=min_event_seconds,
        )

    events = detect_events(
        scores,
        fps=fps,
        threshold=resolved_threshold,
        pad_seconds=pad_seconds,
        min_event_seconds=min_event_seconds,
    )

    total = len(scores)
    kept = sum(event.frames for event in events)

    if destination is not None and kept:
        reporter.start("writing", kept)
        write_frames(
            destination,
            _reporting(_event_frames(source, events), reporter),
            fps=fps,
        )
        reporter.finish()

    return SummaryStats(
        total=total,
        kept=kept,
        fps=fps,
        events=tuple(events),
        threshold=resolved_threshold,
        target_ratio=target_ratio,
        pad_seconds=pad_seconds,
        min_event_seconds=min_event_seconds,
        window=window,
        tolerance=tolerance,
        comparison_edge=edge,
        watch=tuple(watch),
        ignore=tuple(ignore),
    )


def _reporting(frames: Iterable[MatLike], reporter: Progress) -> Iterator[MatLike]:
    """Count frames as the consumer pulls them, so progress tracks real work."""
    for frame in frames:
        yield frame
        reporter.advance()


def _event_frames(source: str | Path, events: Sequence[Event]) -> Iterator[MatLike]:
    """Re-decode ``source`` and yield only the frames inside ``events``."""
    if not events:
        return

    for index, frame in enumerate(iter_frames(source)):
        if index >= events[-1].end:
            return
        if any(event.start <= index < event.end for event in events):
            # Burn the marker in as frames are written, so a summary stays
            # identifiable however it is played later.
            yield draw_summarized(frame)
