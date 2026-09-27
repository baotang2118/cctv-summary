from __future__ import annotations

import numpy as np
import pytest

from cctv_summary.overlay import (
    chevron_height,
    draw_fast_forward,
    draw_summarized,
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


def test_summarized_badge_marks_the_top_right_corner(blank_frame):
    annotated = draw_summarized(blank_frame)

    assert painted(annotated)[: HEIGHT // 2, WIDTH // 2 :].any()


def test_summarized_badge_leaves_other_regions_untouched(blank_frame):
    marks = painted(draw_summarized(blank_frame))

    assert not marks[: HEIGHT // 2, : WIDTH // 3].any()
    assert not marks[HEIGHT // 2 :, :].any()


def test_summarized_badge_does_not_mutate_the_source(blank_frame):
    draw_summarized(blank_frame)

    assert not blank_frame.any()


def test_summarized_badge_preserves_shape_and_dtype(blank_frame):
    annotated = draw_summarized(blank_frame)

    assert annotated.shape == blank_frame.shape
    assert annotated.dtype == blank_frame.dtype


def test_summarized_badge_stays_inside_the_frame(blank_frame):
    marks = painted(draw_summarized(blank_frame))

    assert marks.any()
    assert np.flatnonzero(marks.any(axis=0)).max() < WIDTH
    assert np.flatnonzero(marks.any(axis=1)).max() < HEIGHT


def test_summarized_badge_stays_legible_on_a_white_frame():
    white = np.full((HEIGHT, WIDTH, 3), 255, np.uint8)

    corner = draw_summarized(white)[: HEIGHT // 2, WIDTH // 2 :]

    assert corner.min() < 160
    assert corner.max() == 255


def test_summarized_badge_stays_legible_on_a_black_frame():
    corner = draw_summarized(np.zeros((HEIGHT, WIDTH, 3), np.uint8))[
        : HEIGHT // 2, WIDTH // 2 :
    ]

    assert corner.max() > 200


def test_summarized_badge_scales_with_frame_size():
    small = draw_summarized(np.zeros((240, 320, 3), np.uint8))
    large = draw_summarized(np.zeros((720, 1280, 3), np.uint8))

    assert painted(large).sum() > painted(small).sum()


def test_summarized_badge_differs_from_the_speed_badge(blank_frame):
    assert not np.array_equal(
        draw_summarized(blank_frame), draw_fast_forward(blank_frame, 2.0)
    )


def test_summarized_badge_renders_on_tiny_frames():
    tiny = np.zeros((24, 32, 3), np.uint8)

    assert draw_summarized(tiny).shape == tiny.shape


@pytest.mark.parametrize(
    "draw",
    [draw_summarized, lambda frame: draw_fast_forward(frame, 4.0)],
    ids=["summarized", "fast_forward"],
)
def test_badges_keep_a_margin_from_the_frame_edge(draw):
    # The plate, not just the glyph, has to stay inside the margin: insetting
    # only the glyph lets the plate bleed into the frame edge.
    white = np.full((HEIGHT, WIDTH, 3), 255, np.uint8)

    touched = np.any(draw(white) != 255, axis=2)

    assert touched.any()
    assert not touched[:, WIDTH - 4 :].any()
    assert not touched[:4, :].any()


def test_the_two_badges_never_overlap(blank_frame):
    # A summarized clip played fast carries both marks; they are drawn by
    # different stages, so neither can see the other's position.
    summary = np.any(draw_summarized(blank_frame) != blank_frame, axis=2)
    speed = np.any(draw_fast_forward(blank_frame, 4.0) != blank_frame, axis=2)

    assert summary.any()
    assert speed.any()
    assert not (summary & speed).any()


def test_the_speed_badge_sits_below_the_summary_badge(blank_frame):
    summary_rows = np.flatnonzero(
        np.any(draw_summarized(blank_frame) != blank_frame, axis=2).any(axis=1)
    )
    speed_rows = np.flatnonzero(
        np.any(draw_fast_forward(blank_frame, 4.0) != blank_frame, axis=2).any(axis=1)
    )

    assert speed_rows.min() > summary_rows.max()


def test_both_badges_stay_readable_together():
    grey = np.full((400, 720, 3), 90, np.uint8)

    both = draw_fast_forward(draw_summarized(grey), 4.0)
    marks = np.any(both != grey, axis=2)

    # Both marks survive: neither is painted over by the other.
    assert marks[:, 360:].any()
    assert both[:, 360:].max() > 200
