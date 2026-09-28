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
DEFAULT_THRESHOLD = 0.01

# An event ends only once motion falls below this share of the start threshold,
# which stops a walker pausing mid-frame from splitting one event into three.
STOP_RATIO = 0.5

DEFAULT_WINDOW = 30

# Per-pixel intensity delta (0-255) that counts as "this pixel moved".
DEFAULT_TOLERANCE = 25

# Seconds of footage kept either side of an event, so people are seen entering
# and leaving rather than appearing mid-stride.
DEFAULT_PAD_SECONDS = 2.0

# Events shorter than this are treated as noise rather than something happening.
DEFAULT_MIN_EVENT_SECONDS = 1.0

# How long motion must stay low before an event is considered over.
DEFAULT_COOLDOWN_SECONDS = 1.0

# Frames are compared at this long-edge size: full-resolution diffs are wasteful
# and noisier without changing the decision much.
COMPARISON_EDGE = 320


def downscale_to_gray(frame: MatLike) -> np.ndarray:
    """Downscale and grayscale a frame for cheap, noise-tolerant comparison."""
    height, width = frame.shape[:2]
    if height == 0 or width == 0:
        raise ValueError("Cannot compare an empty frame")

    longest = max(height, width)
    if longest > COMPARISON_EDGE:
        scale = COMPARISON_EDGE / longest
        frame = cv2.resize(
            frame,
            (max(int(width * scale), 1), max(int(height * scale), 1)),
            interpolation=cv2.INTER_AREA,
        )

    if frame.ndim == 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame


def change_score(
    current: np.ndarray,
    reference: np.ndarray,
    *,
    tolerance: int = DEFAULT_TOLERANCE,
) -> float:
    """Fraction of pixels (0.0-1.0) that moved between two prepared frames.

    Both inputs must already be grayscale and the same shape; use
    :func:`downscale_to_gray` first.
    """
    if current.shape != reference.shape:
        raise ValueError(
            f"Frames must match to be compared, got {current.shape} and "
            f"{reference.shape}"
        )

    delta = cv2.absdiff(current.astype(np.uint8), reference.astype(np.uint8))
    moved = np.count_nonzero(delta > tolerance)
    return moved / delta.size


class RollingBackground:
    """Mean of the most recent ``size`` greyscale frames.

    Frames accumulate as float so repeated averaging does not drift the way
    uint8 rounding would.
    """

    def __init__(self, size: int = DEFAULT_WINDOW) -> None:
        if size < 1:
            raise ValueError(f"Window size must be at least 1, got {size}")
        self.size = size
        self._frames: deque[np.ndarray] = deque(maxlen=size)

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def filled(self) -> bool:
        """True once the window holds a full complement of frames."""
        return len(self._frames) == self.size

    @property
    def average(self) -> np.ndarray | None:
        """Mean frame, or ``None`` while the window is still empty."""
        if not self._frames:
            return None
        stacked = np.stack(self._frames).astype(np.float64)
        return stacked.mean(axis=0).astype(np.uint8)

    def add(self, frame: np.ndarray) -> None:
        """Append a greyscale frame, evicting the oldest once full."""
        self._frames.append(frame)


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
) -> Iterator[float]:
    """Yield the fraction of pixels moving in each frame, 0.0-1.0.

    Motion is measured against a rolling background rather than the previous
    frame, so a slow walker registers for as long as they are crossing rather
    than only when they move quickly.
    """
    background = RollingBackground(window)

    for frame in frames:
        gray = downscale_to_gray(frame)
        average = background.average
        score = (
            change_score(gray, average, tolerance=tolerance)
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


def summarize_video(
    source: str | Path,
    destination: str | Path | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    window: int = DEFAULT_WINDOW,
    tolerance: int = DEFAULT_TOLERANCE,
    pad_seconds: float = DEFAULT_PAD_SECONDS,
    min_event_seconds: float = DEFAULT_MIN_EVENT_SECONDS,
    progress: Progress | None = None,
) -> SummaryStats:
    """Write a copy of ``source`` containing only its motion events.

    With ``destination=None`` the footage is analysed but nothing is written,
    which is what ``--dry-run`` uses. Pass a ``progress`` reporter to follow
    long runs; without one the call stays silent.

    The source is decoded twice: once to score motion, once to write. Padding
    reaches backwards from the moment motion is noticed, and buffering an hour
    of frames to look behind is not viable.
    """
    if not 0.0 <= threshold <= 1.0:
        raise VideoError(f"Threshold must be between 0 and 1, got {threshold}")
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

    info = probe(source)
    fps = info.fps if info.fps > 0 else FALLBACK_FPS
    reporter = progress if progress is not None else NullProgress()

    reporter.start("analysing", info.frame_count)
    scores = []
    for score in motion_scores(iter_frames(source), window=window, tolerance=tolerance):
        scores.append(score)
        reporter.advance()
    reporter.finish()

    events = detect_events(
        scores,
        fps=fps,
        threshold=threshold,
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

    return SummaryStats(total=total, kept=kept, fps=fps, events=tuple(events))


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
