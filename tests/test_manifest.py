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
import json
from datetime import UTC, datetime

import pytest

from cctv_summary import __version__
from cctv_summary.manifest import (
    MANIFEST_VERSION,
    build_manifest,
    dump_manifest,
    format_timestamp,
    write_manifest,
)
from cctv_summary.summarize import Event, SummaryStats
from cctv_summary.video import VideoError

FPS = 10.0


def stats_with_events() -> SummaryStats:
    events = (
        Event(start=20, end=60, fps=FPS),
        Event(start=100, end=130, fps=FPS),
    )
    return SummaryStats(
        total=200,
        kept=sum(event.frames for event in events),
        fps=FPS,
        events=events,
        threshold=0.02,
    )


def test_format_timestamp_renders_hours_minutes_seconds():
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(75.9) == "00:01:15"
    assert format_timestamp(3725) == "01:02:05"


def test_manifest_identifies_itself():
    manifest = build_manifest(stats_with_events(), source="clip.avi")

    assert manifest["manifest_version"] == MANIFEST_VERSION
    assert manifest["generator"] == "cctv-summary"
    assert manifest["generator_version"] == __version__


def test_manifest_timestamp_is_an_iso_instant():
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    manifest = build_manifest(
        stats_with_events(), source="clip.avi", generated_at=moment
    )

    assert manifest["generated_at"] == "2026-01-02T03:04:05+00:00"


def test_manifest_describes_the_source_and_summary():
    manifest = build_manifest(
        stats_with_events(), source="clip.avi", destination="summary.avi"
    )

    assert manifest["source"]["path"] == "clip.avi"
    assert manifest["source"]["frames"] == 200
    assert manifest["source"]["fps"] == pytest.approx(FPS)
    assert manifest["source"]["duration_seconds"] == pytest.approx(20.0)
    assert manifest["summary"]["path"] == "summary.avi"
    assert manifest["summary"]["frames"] == 70
    assert manifest["summary"]["duration_seconds"] == pytest.approx(7.0)
    assert manifest["summary"]["kept_ratio"] == pytest.approx(0.35)


def test_a_dry_run_still_exports_its_events():
    # Nothing was written, but knowing when things happened is the point.
    manifest = build_manifest(stats_with_events(), source="clip.avi")

    assert manifest["summary"]["path"] is None
    assert len(manifest["events"]) == 2


def test_manifest_records_the_settings_used():
    stats = SummaryStats(
        total=10,
        kept=0,
        fps=FPS,
        threshold=0.03,
        target_ratio=0.1,
        pad_seconds=3.0,
        min_event_seconds=0.5,
        window=60,
        tolerance=40,
        comparison_edge=480,
    )

    settings = build_manifest(stats, source="clip.avi")["settings"]

    assert settings == {
        "threshold": 0.03,
        "target_ratio": 0.1,
        "pad_seconds": 3.0,
        "min_event_seconds": 0.5,
        "window": 60,
        "tolerance": 40,
        "comparison_edge": 480,
    }


def test_events_carry_frames_seconds_and_timestamps():
    manifest = build_manifest(stats_with_events(), source="clip.avi")

    first = manifest["events"][0]
    assert first["index"] == 1
    assert first["start_frame"] == 20
    assert first["end_frame"] == 60
    assert first["frames"] == 40
    assert first["start_seconds"] == pytest.approx(2.0)
    assert first["end_seconds"] == pytest.approx(6.0)
    assert first["duration_seconds"] == pytest.approx(4.0)
    assert first["start_timestamp"] == "00:00:02"
    assert first["end_timestamp"] == "00:00:06"


def test_events_are_numbered_in_order():
    manifest = build_manifest(stats_with_events(), source="clip.avi")

    assert [event["index"] for event in manifest["events"]] == [1, 2]


def test_seconds_are_rounded_rather_than_trailing_float_noise():
    stats = SummaryStats(
        total=100, kept=1, fps=29.97, events=(Event(start=0, end=1, fps=29.97),)
    )

    manifest = build_manifest(stats, source="clip.avi")

    assert manifest["events"][0]["duration_seconds"] == 0.033


def test_manifest_is_json_serialisable():
    manifest = build_manifest(stats_with_events(), source="clip.avi")

    assert json.loads(json.dumps(manifest)) == manifest


def test_write_manifest_produces_readable_json(tmp_path):
    path = tmp_path / "events.json"

    write_manifest(build_manifest(stats_with_events(), source="clip.avi"), path)

    assert json.loads(path.read_text(encoding="utf-8"))["source"]["frames"] == 200


def test_write_manifest_ends_with_a_newline(tmp_path):
    path = tmp_path / "events.json"

    write_manifest(build_manifest(stats_with_events(), source="clip.avi"), path)

    assert path.read_text(encoding="utf-8").endswith("}\n")


def test_write_manifest_reports_an_unwritable_path(tmp_path):
    path = tmp_path / "missing" / "events.json"

    with pytest.raises(VideoError, match="Could not write manifest"):
        write_manifest(build_manifest(stats_with_events(), source="clip.avi"), path)


def test_dump_manifest_writes_to_a_stream():
    stream = io.StringIO()

    dump_manifest(build_manifest(stats_with_events(), source="clip.avi"), stream)

    assert json.loads(stream.getvalue())["manifest_version"] == MANIFEST_VERSION
