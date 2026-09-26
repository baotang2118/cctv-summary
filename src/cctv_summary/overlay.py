"""Annotations drawn on top of decoded video frames."""

from __future__ import annotations

import cv2
import numpy as np
from cv2.typing import MatLike

# OpenCV uses BGR, so this is red.
DEFAULT_COLOR = (0, 0, 255)

DEFAULT_MARGIN = 12

# Triangle side as a fraction of the frame's shorter edge, so it scales with resolution.
DEFAULT_SIZE_RATIO = 0.12

MIN_SIZE = 4


def triangle_size(width: int, height: int) -> int:
    """Side length that keeps the marker proportional to the frame."""
    scaled = round(min(width, height) * DEFAULT_SIZE_RATIO)
    return max(scaled, MIN_SIZE)


def top_right_triangle(
    width: int,
    height: int,
    *,
    size: int | None = None,
    margin: int = DEFAULT_MARGIN,
) -> np.ndarray:
    """Vertices of a right triangle tucked into the top-right corner.

    Margin and size are clamped so the marker always stays inside the frame,
    even on very small resolutions.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Frame must have a positive size, got {width}x{height}")

    side = triangle_size(width, height) if size is None else size
    side = max(min(side, width, height), 1)
    margin = max(min(margin, (width - side) // 2, (height - side) // 2), 0)

    right = width - margin
    top = margin

    return np.array(
        [
            [right - side, top],
            [right, top],
            [right, top + side],
        ],
        dtype=np.int32,
    )


def draw_triangle(
    frame: MatLike,
    *,
    size: int | None = None,
    margin: int = DEFAULT_MARGIN,
    color: tuple[int, int, int] = DEFAULT_COLOR,
    filled: bool = True,
    thickness: int = 2,
) -> MatLike:
    """Return a copy of ``frame`` with a triangle in the top-right corner.

    The source frame is never mutated: OpenCV reuses decode buffers, so drawing
    in place would corrupt frames a caller still holds.
    """
    height, width = frame.shape[:2]
    points = top_right_triangle(width, height, size=size, margin=margin)

    annotated = frame.copy()
    if filled:
        cv2.fillPoly(annotated, [points], color)
    else:
        cv2.polylines(
            annotated, [points], isClosed=True, color=color, thickness=thickness
        )

    return annotated
