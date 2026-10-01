"""Annotations drawn on top of decoded video frames."""

from __future__ import annotations

from typing import NamedTuple

import cv2
import numpy as np
from cv2.typing import MatLike

# OpenCV uses BGR, so this is white.
DEFAULT_COLOR: tuple[int, int, int] = (255, 255, 255)

# How strongly the backing plate darkens the footage behind the badge.
PLATE_OPACITY: float = 0.55

MARGIN: int = 12

# Chevron height as a fraction of the frame's shorter edge, so the marker
# stays readable at any resolution.
SIZE_RATIO: float = 0.06

MIN_CHEVRON_HEIGHT: int = 8

FONT: int = cv2.FONT_HERSHEY_SIMPLEX

# Badges stack downward from the top-right corner. The summary mark is burned
# into the file and the speed mark is drawn during playback, so each needs a
# reserved row to avoid overlapping the other.
SUMMARY_SLOT: int = 0
SPEED_SLOT: int = 1


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


def _darken_plate(frame: MatLike, left: int, top: int, right: int, bottom: int) -> None:
    """Dim a rectangle in place so a light marker stays readable over it."""
    region = frame[top:bottom, left:right]
    if region.size:
        frame[top:bottom, left:right] = cv2.addWeighted(
            region, 1 - PLATE_OPACITY, np.zeros_like(region), PLATE_OPACITY, 0
        )


class _BadgeBox(NamedTuple):
    """Where a top-right badge's plate sits, and where its content starts."""

    left: int
    top: int
    right: int
    bottom: int
    content_left: int
    content_top: int


def _badge_box(
    width: int,
    height: int,
    content_width: int,
    content_height: int,
    margin: int,
    slot: int = 0,
) -> _BadgeBox:
    """Lay out a badge so the *plate* is inset by ``margin``, not the glyph.

    Insetting the glyph instead lets the plate run past it into the frame edge.
    The margin also shrinks on small frames, where a fixed inset would push the
    badge toward the middle.

    ``slot`` stacks badges downward from the corner. The summary mark is burned
    into the file while the speed mark is added during playback, so playback
    cannot tell whether slot 0 is already taken; giving each a fixed slot keeps
    them from landing on top of each other.
    """
    margin = max(min(margin, min(width, height) // 12), 1)
    pad = max(content_height // 3, 3)

    row_height = content_height + 2 * pad
    right = width - margin
    top = margin + slot * (row_height + max(margin // 2, 2))
    left = max(right - content_width - 2 * pad, 0)
    bottom = min(top + row_height, height)

    return _BadgeBox(left, top, right, bottom, left + pad, top + pad)


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

    label = format_speed(speed)
    font_scale = size / 30
    thickness = max(round(size / 12), 1)
    (text_width, text_height), _ = cv2.getTextSize(label, FONT, font_scale, thickness)

    gap = max(size // 6, 2)
    chevron_span = size + gap  # two chevrons, the second overlapping by half
    badge_width = chevron_span + gap * 2 + text_width

    box = _badge_box(width, height, badge_width, size, margin, slot=SPEED_SLOT)
    left, top = box.content_left, box.content_top

    annotated = frame.copy()
    _darken_plate(annotated, box.left, box.top, box.right, box.bottom)

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


def _draw_scissors(
    frame: MatLike,
    left: int,
    top: int,
    size: int,
    color: tuple[int, int, int],
) -> None:
    """Draw a pair of scissors in place, inside a ``size`` square."""
    stroke = max(round(size / 14), 1)
    handle_radius = max(round(size * 0.19), 2)

    def point(x_ratio: float, y_ratio: float) -> tuple[int, int]:
        return (round(left + size * x_ratio), round(top + size * y_ratio))

    upper_handle = point(0.16, 0.22)
    lower_handle = point(0.16, 0.78)
    upper_tip = point(1.0, 0.04)
    lower_tip = point(1.0, 0.96)

    # The blades cross near the middle, which is what reads as "scissors".
    cv2.line(frame, lower_handle, upper_tip, color, stroke, cv2.LINE_AA)
    cv2.line(frame, upper_handle, lower_tip, color, stroke, cv2.LINE_AA)

    for centre in (upper_handle, lower_handle):
        cv2.circle(frame, centre, handle_radius, color, stroke, cv2.LINE_AA)


def draw_summarized(
    frame: MatLike,
    *,
    color: tuple[int, int, int] = DEFAULT_COLOR,
    margin: int = MARGIN,
) -> MatLike:
    """Return a copy of ``frame`` badged as summarized footage.

    Burned into every frame a summary writes, so the mark travels with the
    file rather than depending on how it is later played. The source frame is
    never mutated: OpenCV reuses decode buffers, so drawing in place would
    corrupt frames a caller still holds.
    """
    height, width = frame.shape[:2]
    size = chevron_height(width, height)

    box = _badge_box(width, height, size, size, margin, slot=SUMMARY_SLOT)

    annotated = frame.copy()
    _darken_plate(annotated, box.left, box.top, box.right, box.bottom)
    _draw_scissors(annotated, box.content_left, box.content_top, size, color)

    return annotated
