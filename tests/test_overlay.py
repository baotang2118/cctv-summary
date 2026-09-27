from __future__ import annotations

import numpy as np
import pytest

from cctv_summary.overlay import (
    chevron_height,
    draw_fast_forward,
    format_speed,
)

HEIGHT = 240
WIDTH = 320


@pytest.fixture
def blank_frame():
    return np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)


def painted(frame):
    return np.any(frame != 0, axis=2)


def test_badge_marks_the_top_right_corner(blank_frame):
    annotated = draw_fast_forward(blank_frame, 2.0)

    assert painted(annotated)[: HEIGHT // 2, WIDTH // 2 :].any()


def test_badge_leaves_other_regions_untouched(blank_frame):
    annotated = draw_fast_forward(blank_frame, 2.0)
    marks = painted(annotated)

    assert not marks[: HEIGHT // 2, : WIDTH // 3].any()
    assert not marks[HEIGHT // 2 :, :].any()


def test_badge_does_not_mutate_the_source(blank_frame):
    draw_fast_forward(blank_frame, 4.0)

    assert not blank_frame.any()


def test_badge_preserves_frame_shape_and_dtype(blank_frame):
    annotated = draw_fast_forward(blank_frame, 2.0)

    assert annotated.shape == blank_frame.shape
    assert annotated.dtype == blank_frame.dtype


def test_badge_stays_inside_the_frame(blank_frame):
    annotated = draw_fast_forward(blank_frame, 2.0)
    marks = painted(annotated)

    assert marks.any()
    columns = np.flatnonzero(marks.any(axis=0))
    rows = np.flatnonzero(marks.any(axis=1))
    assert columns.max() < WIDTH
    assert rows.max() < HEIGHT


def test_badge_scales_with_frame_size():
    small = draw_fast_forward(np.zeros((240, 320, 3), np.uint8), 2.0)
    large = draw_fast_forward(np.zeros((720, 1280, 3), np.uint8), 2.0)

    assert painted(large).sum() > painted(small).sum()


def test_badge_stays_legible_on_a_white_frame():
    white = np.full((HEIGHT, WIDTH, 3), 255, np.uint8)

    annotated = draw_fast_forward(white, 4.0)

    # A white marker on white footage only reads because of the darkened
    # backing plate, so require real contrast rather than "something changed".
    corner = annotated[: HEIGHT // 2, WIDTH // 2 :]
    assert corner.min() < 160
    assert corner.max() == 255


def test_badge_stays_legible_on_a_black_frame():
    black = np.zeros((HEIGHT, WIDTH, 3), np.uint8)

    annotated = draw_fast_forward(black, 4.0)

    corner = annotated[: HEIGHT // 2, WIDTH // 2 :]
    assert corner.max() > 200


def test_faster_speeds_render_a_different_label(blank_frame):
    two = draw_fast_forward(blank_frame, 2.0)
    twelve = draw_fast_forward(blank_frame, 12.0)

    assert not np.array_equal(two, twelve)


def test_chevron_height_scales_with_the_frame():
    assert chevron_height(1280, 720) > chevron_height(320, 240)


def test_chevron_height_has_a_floor():
    assert chevron_height(10, 10) >= 8


def test_whole_speeds_drop_the_decimal():
    assert format_speed(2.0) == "2x"
    assert format_speed(10.0) == "10x"


def test_fractional_speeds_keep_their_precision():
    assert format_speed(2.5) == "2.5x"
    assert format_speed(1.25) == "1.25x"


def test_tiny_frames_still_render_without_error():
    tiny = np.zeros((24, 32, 3), np.uint8)

    annotated = draw_fast_forward(tiny, 2.0)

    assert annotated.shape == tiny.shape
