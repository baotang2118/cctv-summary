from __future__ import annotations

import numpy as np
import pytest

from cctv_summary.overlay import (
    DEFAULT_COLOR,
    draw_triangle,
    top_right_triangle,
    triangle_size,
)

WIDTH = 200
HEIGHT = 120


@pytest.fixture
def blank_frame():
    return np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)


def test_triangle_sits_in_the_top_right_quadrant():
    points = top_right_triangle(WIDTH, HEIGHT)

    xs = points[:, 0]
    ys = points[:, 1]

    assert xs.min() > WIDTH / 2
    assert ys.max() < HEIGHT / 2


def test_triangle_stays_inside_the_frame():
    points = top_right_triangle(WIDTH, HEIGHT)

    assert points[:, 0].min() >= 0
    assert points[:, 0].max() <= WIDTH
    assert points[:, 1].min() >= 0
    assert points[:, 1].max() <= HEIGHT


def test_triangle_has_three_distinct_vertices():
    points = top_right_triangle(WIDTH, HEIGHT)

    assert points.shape == (3, 2)
    assert len({tuple(point) for point in points}) == 3


def test_triangle_scales_with_frame_size():
    assert triangle_size(1920, 1080) > triangle_size(320, 240)


def test_tiny_frames_still_produce_a_valid_triangle():
    points = top_right_triangle(6, 5)

    assert points[:, 0].max() <= 6
    assert points[:, 1].max() <= 5
    assert len({tuple(point) for point in points}) == 3


def test_zero_sized_frame_is_rejected():
    with pytest.raises(ValueError, match="positive size"):
        top_right_triangle(0, 10)


def test_draw_triangle_marks_the_top_right_corner(blank_frame):
    annotated = draw_triangle(blank_frame)

    top_right = annotated[: HEIGHT // 2, WIDTH // 2 :]
    assert top_right.any()


def test_draw_triangle_leaves_other_corners_untouched(blank_frame):
    annotated = draw_triangle(blank_frame)

    assert not annotated[: HEIGHT // 2, : WIDTH // 2].any()
    assert not annotated[HEIGHT // 2 :, :].any()


def test_draw_triangle_uses_the_requested_colour(blank_frame):
    annotated = draw_triangle(blank_frame, color=(255, 0, 0))

    painted = annotated[np.any(annotated != 0, axis=2)]
    assert (painted == (255, 0, 0)).all()


def test_draw_triangle_defaults_to_red(blank_frame):
    annotated = draw_triangle(blank_frame)

    painted = annotated[np.any(annotated != 0, axis=2)]
    assert (painted == DEFAULT_COLOR).all()


def test_draw_triangle_does_not_mutate_the_source(blank_frame):
    draw_triangle(blank_frame)

    assert not blank_frame.any()


def test_draw_triangle_preserves_frame_shape(blank_frame):
    annotated = draw_triangle(blank_frame)

    assert annotated.shape == blank_frame.shape
    assert annotated.dtype == blank_frame.dtype


def test_outline_paints_less_than_filled():
    # Uses a large explicit size: on tiny triangles a 2px stroke can cover more
    # pixels than the fill it outlines.
    frame = np.zeros((400, 400, 3), dtype=np.uint8)

    filled = np.count_nonzero(
        np.any(draw_triangle(frame, size=120) != 0, axis=2),
    )
    outline = np.count_nonzero(
        np.any(draw_triangle(frame, size=120, filled=False) != 0, axis=2),
    )

    assert 0 < outline < filled
