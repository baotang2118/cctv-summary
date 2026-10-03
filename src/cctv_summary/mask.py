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

"""Spatial masks: which parts of the frame get a vote on motion.

Raw pixel change has no idea what anything is, so a road, a swaying tree, or
the camera's own ticking clock overlay all register as motion forever. Those
are *localised* problems, and the global knobs cannot fix them: raising
`threshold` or `tolerance` enough to silence a branch also discards the distant
figure that matters. This narrows *where* motion counts instead of how loud it
has to be.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cctv_summary.video import VideoError


# Regions are fractions of the frame rather than pixels, so one description
# survives a resolution change and does not have to be re-authored for the
# comparison tier it is eventually scaled to.
@dataclass(frozen=True)
class Region:
    """A rectangle in fractional frame coordinates, 0.0-1.0 from the top-left."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for name, value in (
            ("x", self.x),
            ("y", self.y),
            ("width", self.width),
            ("height", self.height),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"Region {name} must be between 0 and 1, got {value}")
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"Region must have a positive size, got {self.width}x{self.height}"
            )
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError(f"Region must stay inside the frame, got {self!r}")

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.width, self.height)

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Resolve to ``(left, top, right, bottom)`` pixel bounds.

        Rounded outward to at least one pixel, so a thin region does not vanish
        at a small comparison size and silently stop masking anything.
        """
        left = min(int(self.x * width), max(width - 1, 0))
        top = min(int(self.y * height), max(height - 1, 0))
        right = max(round((self.x + self.width) * width), left + 1)
        bottom = max(round((self.y + self.height) * height), top + 1)
        return left, top, min(right, width), min(bottom, height)


def parse_region(text: str) -> Region:
    """Parse ``X,Y,W,H`` fractional coordinates into a :class:`Region`."""
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 4:
        raise ValueError(f"expected four comma-separated values X,Y,W,H, got {text!r}")

    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        raise ValueError(f"expected numbers in X,Y,W,H, got {text!r}") from None

    return Region(*numbers)


def build_mask(
    width: int,
    height: int,
    *,
    watch: tuple[Region, ...] = (),
    ignore: tuple[Region, ...] = (),
) -> np.ndarray | None:
    """Build the boolean "these pixels count" mask for a comparison-sized frame.

    ``watch`` is an allow-list and ``ignore`` a deny-list; with both, ignored
    regions are subtracted from the watched ones. Returns ``None`` when nothing
    is masked, so the common case stays allocation-free rather than carrying a
    mask of all-true around.
    """
    if width < 1 or height < 1:
        raise VideoError(f"Mask size must be positive, got {width}x{height}")
    if not watch and not ignore:
        return None

    # Without an allow-list every pixel starts eligible and exclusions carve
    # into it; with one, only the named regions do.
    mask = np.zeros((height, width), dtype=bool)
    if watch:
        for region in watch:
            left, top, right, bottom = region.pixels(width, height)
            mask[top:bottom, left:right] = True
    else:
        mask[:] = True

    for region in ignore:
        left, top, right, bottom = region.pixels(width, height)
        mask[top:bottom, left:right] = False

    if not mask.any():
        raise VideoError(
            "The mask leaves no pixels to watch; widen --watch or drop an --ignore"
        )

    return mask


def describe(regions: tuple[Region, ...]) -> list[list[float]]:
    """Render regions as plain lists, for the JSON manifest."""
    return [list(region.as_tuple()) for region in regions]
