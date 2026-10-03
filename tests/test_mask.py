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

from __future__ import annotations

import numpy as np
import pytest

from cctv_summary.mask import Region, build_mask, describe, parse_region
from cctv_summary.video import VideoError

WIDTH = 100
HEIGHT = 50


def test_parse_region_reads_four_fractions():
    assert parse_region("0.1,0.2,0.3,0.4") == Region(0.1, 0.2, 0.3, 0.4)


def test_parse_region_tolerates_spaces():
    assert parse_region(" 0, 0 , 1 ,1 ") == Region(0.0, 0.0, 1.0, 1.0)


@pytest.mark.parametrize("text", ["0,0,1", "0,0,1,1,1", ""])
def test_parse_region_needs_exactly_four_values(text):
    with pytest.raises(ValueError, match="four comma-separated"):
        parse_region(text)


def test_parse_region_rejects_non_numbers():
    with pytest.raises(ValueError, match="numbers"):
        parse_region("left,0,1,1")


def test_region_rejects_coordinates_outside_the_frame():
    with pytest.raises(ValueError, match="between 0 and 1"):
        Region(0.0, 0.0, 1.5, 1.0)


def test_region_rejects_an_empty_rectangle():
    with pytest.raises(ValueError, match="positive size"):
        Region(0.0, 0.0, 0.0, 0.5)


def test_region_rejects_a_rectangle_running_off_the_edge():
    with pytest.raises(ValueError, match="inside the frame"):
        Region(0.8, 0.0, 0.5, 1.0)


def test_no_regions_means_no_mask():
    # The common case stays allocation-free rather than carrying an all-true
    # mask through every frame.
    assert build_mask(WIDTH, HEIGHT) is None


def test_watch_allows_only_the_named_region():
    mask = build_mask(WIDTH, HEIGHT, watch=(Region(0.0, 0.0, 0.5, 1.0),))

    assert mask is not None
    assert mask[:, :50].all()
    assert not mask[:, 50:].any()


def test_ignore_subtracts_from_the_whole_frame():
    mask = build_mask(WIDTH, HEIGHT, ignore=(Region(0.0, 0.0, 0.2, 1.0),))

    assert mask is not None
    assert not mask[:, :20].any()
    assert mask[:, 20:].all()


def test_ignore_is_applied_after_watch():
    mask = build_mask(
        WIDTH,
        HEIGHT,
        watch=(Region(0.0, 0.0, 0.5, 1.0),),
        ignore=(Region(0.0, 0.0, 0.2, 1.0),),
    )

    assert mask is not None
    assert not mask[:, :20].any()
    assert mask[:, 20:50].all()
    assert not mask[:, 50:].any()


def test_multiple_watch_regions_are_unioned():
    mask = build_mask(
        WIDTH,
        HEIGHT,
        watch=(Region(0.0, 0.0, 0.2, 1.0), Region(0.8, 0.0, 0.2, 1.0)),
    )

    assert mask is not None
    assert mask[:, :20].all()
    assert not mask[:, 20:80].any()
    assert mask[:, 80:].all()


def test_a_mask_that_watches_nothing_is_refused():
    # Scoring would divide by zero; a clear error beats a NaN.
    with pytest.raises(VideoError, match="no pixels to watch"):
        build_mask(
            WIDTH,
            HEIGHT,
            watch=(Region(0.0, 0.0, 0.5, 1.0),),
            ignore=(Region(0.0, 0.0, 1.0, 1.0),),
        )


def test_mask_is_boolean_and_frame_shaped():
    mask = build_mask(WIDTH, HEIGHT, ignore=(Region(0.0, 0.0, 0.1, 0.1),))

    assert mask is not None
    assert mask.dtype == np.bool_
    assert mask.shape == (HEIGHT, WIDTH)


def test_mask_size_must_be_positive():
    with pytest.raises(VideoError, match="must be positive"):
        build_mask(0, HEIGHT, ignore=(Region(0.0, 0.0, 0.1, 0.1),))


def test_a_thin_region_survives_a_small_frame():
    # Rounding a sliver to nothing would silently stop it masking anything.
    mask = build_mask(16, 16, ignore=(Region(0.0, 0.0, 0.01, 0.01),))

    assert mask is not None
    assert not mask[0, 0]


def test_regions_scale_with_the_frame():
    small = build_mask(50, 25, watch=(Region(0.0, 0.0, 0.5, 1.0),))
    large = build_mask(200, 100, watch=(Region(0.0, 0.0, 0.5, 1.0),))

    assert small is not None
    assert large is not None
    assert small.mean() == pytest.approx(large.mean(), abs=0.01)


def test_describe_renders_regions_as_plain_lists():
    assert describe((Region(0.0, 0.5, 1.0, 0.5),)) == [[0.0, 0.5, 1.0, 0.5]]


def test_describe_renders_nothing_for_no_regions():
    assert describe(()) == []
