"""Frame selection that drops redundant footage to shorten a video.

A frame is dropped only when it looks unchanged against *both* the previous kept
frame and a rolling background model. The previous-frame check catches ordinary
stillness; the background check catches slow drift, where each individual frame
barely differs from the last but the scene has clearly moved over time.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from cv2.typing import MatLike

from cctv_summary.overlay import draw_summarized
from cctv_summary.video import (
    FALLBACK_FPS,
    VideoError,
    iter_frames,
    probe,
    write_frames,
)

DEFAULT_THRESHOLD = 0.10

DEFAULT_WINDOW = 30

# Per-pixel intensity delta (0-255) that counts as "this pixel moved".
DEFAULT_TOLERANCE = 25

# Frames are compared at this long-edge size: full-resolution diffs are wasteful
# and noisier without changing the decision much.
COMPARISON_EDGE = 320


def to_comparable(frame: MatLike) -> np.ndarray:
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
    """Fraction of pixels (0.0-1.0) that moved between two comparable frames.

    Both inputs must already be grayscale and the same shape; use
    :func:`to_comparable` first.
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
    """Mean of the most recent ``size`` comparable frames.

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
        """Append a comparable frame, evicting the oldest once full."""
        self._frames.append(frame)


@dataclass(frozen=True)
class SummaryStats:
    """Outcome of a selection pass."""

    total: int
    kept: int
    fps: float = 0.0

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


def select_frames(
    frames: Iterable[MatLike],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    window: int = DEFAULT_WINDOW,
    tolerance: int = DEFAULT_TOLERANCE,
) -> Iterator[MatLike]:
    """Yield only the frames worth keeping.

    The first frame is always kept, since there is nothing to compare it to.
    Every decoded frame feeds the rolling window, including dropped ones, so the
    background reflects what the camera actually sees.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"Threshold must be between 0 and 1, got {threshold}")

    background = RollingBackground(window)
    previous: np.ndarray | None = None

    for frame in frames:
        comparable = to_comparable(frame)

        if previous is None:
            background.add(comparable)
            previous = comparable
            yield frame
            continue

        average = background.average
        against_previous = change_score(comparable, previous, tolerance=tolerance)
        against_background = (
            change_score(comparable, average, tolerance=tolerance)
            if average is not None
            else 1.0
        )

        background.add(comparable)

        if against_previous < threshold and against_background < threshold:
            continue

        previous = comparable
        yield frame


def summarize_video(
    source: str | Path,
    destination: str | Path | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    window: int = DEFAULT_WINDOW,
    tolerance: int = DEFAULT_TOLERANCE,
) -> SummaryStats:
    """Write a shortened copy of ``source`` keeping only changed frames.

    With ``destination=None`` the footage is analysed but nothing is written,
    which is what ``--dry-run`` uses.
    """
    if not 0.0 <= threshold <= 1.0:
        raise VideoError(f"Threshold must be between 0 and 1, got {threshold}")
    if window < 1:
        raise VideoError(f"Window must be at least 1 frame, got {window}")
    if not 0 <= tolerance <= 255:
        raise VideoError(f"Tolerance must be between 0 and 255, got {tolerance}")

    info = probe(source)
    fps = info.fps if info.fps > 0 else FALLBACK_FPS
    total = 0

    def counted(frames: Iterable[MatLike]) -> Iterator[MatLike]:
        nonlocal total
        for frame in frames:
            total += 1
            yield frame

    selected = select_frames(
        counted(iter_frames(source)),
        threshold=threshold,
        window=window,
        tolerance=tolerance,
    )

    if destination is None:
        kept = sum(1 for _ in selected)
    else:
        # Burn the marker in as frames are written, so a summary stays
        # identifiable however it is played later.
        badged = (draw_summarized(frame) for frame in selected)
        kept = write_frames(destination, badged, fps=fps)

    return SummaryStats(total=total, kept=kept, fps=fps)
