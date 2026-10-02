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

import io

import pytest

from cctv_summary.progress import (
    NullProgress,
    TerminalProgress,
    format_duration,
)


class FakeTerminal(io.StringIO):
    """A stream that claims to be a terminal, as stderr would be."""

    def __init__(self, tty: bool = True) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def render(stream: FakeTerminal) -> str:
    """Only what a viewer would be left looking at: the last redrawn line."""
    return stream.getvalue().split("\r")[-1]


def test_seconds_stay_seconds():
    assert format_duration(0) == "0s"
    assert format_duration(45) == "45s"


def test_minutes_are_split_out():
    assert format_duration(60) == "1m00s"
    assert format_duration(125) == "2m05s"


def test_hours_are_split_out():
    assert format_duration(3600) == "1h00m"
    assert format_duration(3725) == "1h02m"


def test_negative_durations_do_not_render_oddly():
    assert format_duration(-5) == "0s"


def test_null_progress_writes_nothing():
    progress = NullProgress()

    progress.start("analysing", 10)
    progress.advance()
    progress.finish()


def test_progress_draws_as_soon_as_it_starts():
    # The first frame can be slow to arrive, which is when a run looks stuck.
    stream = FakeTerminal()

    TerminalProgress(stream).start("analysing", 100)

    assert "analysing" in stream.getvalue()


def test_progress_reports_a_percentage():
    stream = FakeTerminal()
    progress = TerminalProgress(stream, redraw_seconds=0)

    progress.start("analysing", 100)
    progress.advance(25)

    assert "25%" in render(stream)
    assert "25/100" in render(stream)


def test_the_bar_fills_as_work_completes():
    stream = FakeTerminal()
    progress = TerminalProgress(stream, redraw_seconds=0)

    progress.start("analysing", 10)
    progress.advance(5)
    half = render(stream).count("#")
    progress.advance(5)

    assert render(stream).count("#") > half


def test_progress_never_exceeds_one_hundred_percent():
    stream = FakeTerminal()
    progress = TerminalProgress(stream, redraw_seconds=0)

    progress.start("writing", 10)
    progress.advance(50)

    assert "100%" in render(stream)


def test_an_overshooting_count_is_clamped():
    # probe() frame counts are estimates for some containers, so the real
    # count can run past the total; 50/10 would look broken.
    stream = FakeTerminal()
    progress = TerminalProgress(stream, redraw_seconds=0)

    progress.start("writing", 10)
    progress.advance(50)

    assert "10/10" in render(stream)
    assert "50/10" not in render(stream)


def test_updates_are_throttled():
    stream = FakeTerminal()
    progress = TerminalProgress(stream, redraw_seconds=3600)

    progress.start("analysing", 1000)
    before = len(stream.getvalue())
    for _ in range(500):
        progress.advance()

    assert len(stream.getvalue()) == before


def test_unknown_totals_still_show_liveness():
    # Some containers report no frame count at all.
    stream = FakeTerminal()
    progress = TerminalProgress(stream, redraw_seconds=0)

    progress.start("analysing", 0)
    progress.advance(7)

    line = render(stream)
    assert "7 frames" in line
    assert "%" not in line


def test_finishing_clears_the_line():
    stream = FakeTerminal()
    progress = TerminalProgress(stream, redraw_seconds=0)

    progress.start("analysing", 10)
    progress.advance(5)
    progress.finish()

    assert render(stream).strip() == ""


def test_finishing_without_starting_is_harmless():
    TerminalProgress(FakeTerminal()).finish()


def test_a_shorter_line_overwrites_a_longer_one():
    stream = FakeTerminal()
    progress = TerminalProgress(stream, redraw_seconds=0)

    progress.start("analysing", 1000)
    progress.advance(1000)
    long_line = len(render(stream))

    progress.start("writing", 5)

    # Padding stops leftovers of the previous line showing through.
    assert len(render(stream)) >= long_line


def test_labels_are_padded_so_the_bar_does_not_jump():
    stream = FakeTerminal()
    progress = TerminalProgress(stream, redraw_seconds=0)

    progress.start("analysing", 10)
    analysing = render(stream).index("[")
    progress.finish()
    progress.start("writing", 10)

    assert render(stream).index("[") == analysing


@pytest.mark.parametrize("label", ["analysing", "writing"])
def test_the_stage_is_named(label):
    stream = FakeTerminal()

    TerminalProgress(stream, redraw_seconds=0).start(label, 10)

    assert label in render(stream)
