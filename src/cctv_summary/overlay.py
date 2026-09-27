"""Annotations drawn on top of decoded video frames."""

from __future__ import annotations

import cv2
import numpy as np
from cv2.typing import MatLike

# OpenCV uses BGR, so this is white.
DEFAULT_COLOR = (255, 255, 255)

# How strongly the backing plate darkens the footage behind the badge.
PLATE_OPACITY = 0.55

MARGIN = 12

# Chevron height as a fraction of the frame's shorter edge, so the marker
# stays readable at any resolution.
SIZE_RATIO = 0.06

MIN_CHEVRON_HEIGHT = 8

FONT = cv2.FONT_HERSHEY_SIMPLEX


def chevron_height(width: int, height: int) -> int:
    """Marker height that keeps the badge proportional to the frame."""
    scaled = round(min(width, height) * SIZE_RATIO)
    return max(scaled, MIN_CHEVRON_HEIGHT)


def format_speed(speed: float) -> str:
    """Render a speed multiplier the way a player would label it."""
    if speed == int(speed):
        return f"{int(speed)}x"
    return f"{speed:g}x"


def _chevron(left: int, top: int, size: int) -> np.ndarray:
    """A single right-pointing triangle anchored at its top-left corner."""
    return np.array(
        [
            [left, top],
            [left + size // 2, top + size // 2],
            [left, top + size],
        ],
        dtype=np.int32,
    )


def draw_fast_forward(
    frame: MatLike,
    speed: float,
    *,
    color: tuple[int, int, int] = DEFAULT_COLOR,
    margin: int = MARGIN,
) -> MatLike:
    """Return a copy of ``frame`` badged with a fast-forward marker.

    The badge is a double chevron followed by the speed, drawn in the
    top-right corner over a translucent dark plate. The plate is what keeps
    the marker readable on bright footage; an outline alone washes out against
    white. The source frame is never mutated: OpenCV reuses decode buffers, so
    drawing in place would corrupt frames a caller still holds.
    """
    height, width = frame.shape[:2]
    size = chevron_height(width, height)
    margin = max(min(margin, width // 4, height // 4), 0)

    label = format_speed(speed)
    font_scale = size / 30
    thickness = max(round(size / 12), 1)
    (text_width, text_height), _ = cv2.getTextSize(label, FONT, font_scale, thickness)

    gap = max(size // 6, 2)
    chevron_span = size + gap  # two chevrons, the second overlapping by half
    badge_width = chevron_span + gap * 2 + text_width

    left = max(width - margin - badge_width, 0)
    top = max(margin, 0)

    annotated = frame.copy()

    pad = max(size // 3, 3)
    plate_left = max(left - pad, 0)
    plate_top = max(top - pad, 0)
    plate_right = min(left + badge_width + pad, width)
    plate_bottom = min(top + size + pad, height)

    plate = annotated[plate_top:plate_bottom, plate_left:plate_right]
    if plate.size:
        annotated[plate_top:plate_bottom, plate_left:plate_right] = cv2.addWeighted(
            plate, 1 - PLATE_OPACITY, np.zeros_like(plate), PLATE_OPACITY, 0
        )

    for index in range(2):
        points = _chevron(left + index * (size // 2 + gap // 2), top, size)
        cv2.fillPoly(annotated, [points], color, lineType=cv2.LINE_AA)

    text_origin = (
        left + chevron_span + gap * 2,
        top + (size + text_height) // 2,
    )
    cv2.putText(
        annotated,
        label,
        text_origin,
        FONT,
        font_scale,
        color,
        thickness,
        cv2.LINE_AA,
    )

    return annotated
