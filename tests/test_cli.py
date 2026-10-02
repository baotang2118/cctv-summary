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

import json

import cv2
import numpy as np
import pytest

from cctv_summary import __version__
from cctv_summary.cli import _progress_for, build_parser, main
from cctv_summary.progress import NullProgress, TerminalProgress
from cctv_summary.video import VideoError


def test_version_is_exposed():
    assert isinstance(__version__, str)
    assert __version__


def test_version_flag_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 1

    out = capsys.readouterr().out
    assert "info" in out
    assert "play" in out


def test_info_prints_metadata(sample_video):
    assert main(["info", str(sample_video.path)]) == 0


def test_info_output_contains_resolution_and_duration(sample_video, capsys):
    main(["info", str(sample_video.path)])

    out = capsys.readouterr().out
    assert f"{sample_video.width}x{sample_video.height}" in out
    assert f"frames:      {sample_video.frames}" in out
    assert "duration:" in out


def test_info_reports_missing_file(tmp_path, capsys):
    assert main(["info", str(tmp_path / "nope.avi")]) == 1
    assert "error:" in capsys.readouterr().err


def test_play_invokes_playback(monkeypatch, capsys, tmp_path):
    calls = {}

    def fake_play(video, **kwargs):
        calls.update(kwargs)
        calls["video"] = video
        return 7

    monkeypatch.setattr("cctv_summary.cli.play", fake_play)

    assert main(["play", str(tmp_path / "clip.avi"), "--speed", "2.5"]) == 0
    assert calls["speed"] == 2.5
    assert calls["headless"] is False
    assert "Displayed 7 frame(s)" in capsys.readouterr().out


def test_play_defaults_to_normal_speed(monkeypatch, tmp_path):
    captured = {}

    def fake_play(video, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("cctv_summary.cli.play", fake_play)

    assert main(["play", str(tmp_path / "clip.avi")]) == 0
    assert captured["speed"] == 1.0
    assert captured["headless"] is False


def test_play_headless_flag_is_forwarded(monkeypatch, capsys, tmp_path):
    captured = {}

    def fake_play(video, **kwargs):
        captured.update(kwargs)
        return 5

    monkeypatch.setattr("cctv_summary.cli.play", fake_play)

    assert main(["play", str(tmp_path / "clip.avi"), "--headless"]) == 0
    assert captured["headless"] is True
    assert "Processed 5 frame(s)" in capsys.readouterr().out


def test_play_surfaces_video_errors(monkeypatch, capsys, tmp_path):
    def fake_play(video, **kwargs):
        raise VideoError("boom")

    monkeypatch.setattr("cctv_summary.cli.play", fake_play)

    assert main(["play", str(tmp_path / "clip.avi")]) == 1
    assert "error: boom" in capsys.readouterr().err


def test_summarize_requires_a_destination(sample_video, capsys):
    assert main(["summarize", str(sample_video.path)]) == 1
    assert "output path is required" in capsys.readouterr().err


def test_summarize_dry_run_needs_no_destination(sample_video, capsys):
    assert main(["summarize", str(sample_video.path), "--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out


def test_summarize_reports_events_with_timestamps(sample_video, capsys):
    assert (
        main(
            [
                "summarize",
                str(sample_video.path),
                "--dry-run",
                "--threshold",
                "0.001",
                "--min-event",
                "0",
            ]
        )
        == 0
    )

    out = capsys.readouterr().out
    assert "events:" in out
    assert "00:00:" in out


def test_summarize_says_so_when_nothing_moves(tmp_path, capsys):
    # The sample_video fixture ramps brightness every frame, so it always has
    # motion. This needs footage that genuinely sits still.
    still = tmp_path / "still.avi"
    writer = cv2.VideoWriter(
        str(still), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48)
    )
    if not writer.isOpened():
        pytest.skip("No MJPG encoder available.")
    for _ in range(30):
        writer.write(np.full((48, 64, 3), 90, np.uint8))
    writer.release()

    assert main(["summarize", str(still), "--dry-run"]) == 0
    assert "no motion found" in capsys.readouterr().out


def test_summarize_writes_a_file(sample_video, tmp_path, capsys):
    destination = tmp_path / "summary.avi"

    assert (
        main(
            [
                "summarize",
                str(sample_video.path),
                str(destination),
                "--threshold",
                "0.001",
                "--min-event",
                "0",
            ]
        )
        == 0
    )

    assert destination.exists()
    assert "wrote:" in capsys.readouterr().out


def test_summarize_surfaces_video_errors(tmp_path, capsys):
    assert main(["summarize", str(tmp_path / "nope.avi"), "--dry-run"]) == 1
    assert "error:" in capsys.readouterr().err


def test_summarize_writes_a_manifest(sample_video, tmp_path, capsys):
    manifest_path = tmp_path / "events.json"

    assert (
        main(
            [
                "summarize",
                str(sample_video.path),
                "--dry-run",
                "--threshold",
                "0.001",
                "--min-event",
                "0",
                "--manifest",
                str(manifest_path),
            ]
        )
        == 0
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["frames"] == sample_video.frames
    assert manifest["events"]
    assert "manifest:" in capsys.readouterr().out


def test_manifest_records_where_the_summary_was_written(sample_video, tmp_path, capsys):
    destination = tmp_path / "summary.avi"
    manifest_path = tmp_path / "events.json"

    assert (
        main(
            [
                "summarize",
                str(sample_video.path),
                str(destination),
                "--threshold",
                "0.001",
                "--min-event",
                "0",
                "--manifest",
                str(manifest_path),
            ]
        )
        == 0
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["summary"]["path"] == str(destination)


def test_manifest_records_the_settings_the_run_used(sample_video, tmp_path):
    manifest_path = tmp_path / "events.json"

    main(
        [
            "summarize",
            str(sample_video.path),
            "--dry-run",
            "--threshold",
            "0.02",
            "--window",
            "12",
            "--tolerance",
            "30",
            "--pad",
            "0.5",
            "--min-event",
            "0",
            "--comparison-edge",
            "64",
            "--manifest",
            str(manifest_path),
        ]
    )

    settings = json.loads(manifest_path.read_text(encoding="utf-8"))["settings"]
    assert settings["threshold"] == 0.02
    assert settings["window"] == 12
    assert settings["tolerance"] == 30
    assert settings["pad_seconds"] == 0.5
    assert settings["comparison_edge"] == 64


def test_manifest_to_stdout_is_parseable_on_its_own(sample_video, capsys):
    # The readable report has to step aside, or piping stdout into a parser
    # fails on the table.
    assert (
        main(
            [
                "summarize",
                str(sample_video.path),
                "--dry-run",
                "--threshold",
                "0.001",
                "--min-event",
                "0",
                "--manifest",
                "-",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert json.loads(captured.out)["events"]
    assert "events:" in captured.err


def test_summarize_writes_no_manifest_unless_asked(sample_video, tmp_path, capsys):
    main(["summarize", str(sample_video.path), "--dry-run"])

    assert list(tmp_path.iterdir()) == []
    assert "manifest:" not in capsys.readouterr().out


def test_summarize_reports_an_unwritable_manifest_path(sample_video, tmp_path, capsys):
    assert (
        main(
            [
                "summarize",
                str(sample_video.path),
                "--dry-run",
                "--manifest",
                str(tmp_path / "missing" / "events.json"),
            ]
        )
        == 1
    )
    assert "error:" in capsys.readouterr().err


def test_comparison_edge_defaults_to_the_resolution_tier():
    args = build_parser().parse_args(["summarize", "clip.avi", "--dry-run"])

    assert args.comparison_edge is None


def test_comparison_edge_accepts_auto():
    args = build_parser().parse_args(
        ["summarize", "clip.avi", "--dry-run", "--comparison-edge", "auto"]
    )

    assert args.comparison_edge is None


def test_comparison_edge_accepts_a_pixel_count():
    args = build_parser().parse_args(
        ["summarize", "clip.avi", "--dry-run", "--comparison-edge", "480"]
    )

    assert args.comparison_edge == 480


@pytest.mark.parametrize("value", ["0", "-5", "wide"])
def test_comparison_edge_rejects_nonsense(value, capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(
            ["summarize", "clip.avi", "--dry-run", "--comparison-edge", value]
        )

    assert excinfo.value.code == 2
    assert "--comparison-edge" in capsys.readouterr().err


def test_summarize_forwards_the_comparison_edge(sample_video, capsys):
    assert (
        main(
            [
                "summarize",
                str(sample_video.path),
                "--dry-run",
                "--comparison-edge",
                "64",
            ]
        )
        == 0
    )
    assert "events:" in capsys.readouterr().out


class FakeStream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_a_terminal_gets_a_progress_reporter():
    assert isinstance(_progress_for(FakeStream(tty=True)), TerminalProgress)


def test_redirected_output_gets_no_progress():
    # Carriage returns would otherwise fill a log file or pipe.
    assert isinstance(_progress_for(FakeStream(tty=False)), NullProgress)


def test_streams_without_isatty_get_no_progress():
    assert isinstance(_progress_for(object()), NullProgress)


def test_summarize_keeps_progress_off_stdout(sample_video, capsys):
    main(["summarize", str(sample_video.path), "--dry-run"])

    out = capsys.readouterr().out
    assert "\r" not in out
    assert "analysing" not in out


def test_headless_play_gets_progress(sample_video, monkeypatch):
    captured = {}

    def fake_play(video, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("cctv_summary.cli.play", fake_play)
    main(["play", str(sample_video.path), "--headless"])

    assert captured["progress"] is not None


def test_windowed_play_gets_no_progress(sample_video, monkeypatch):
    # The window itself shows the run is alive; a progress line would only
    # compete with it.
    captured = {}

    def fake_play(video, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("cctv_summary.cli.play", fake_play)
    main(["play", str(sample_video.path)])

    assert captured["progress"] is None


def test_target_accepts_a_percentage():
    args = build_parser().parse_args(["summarize", "clip.mp4", "--target", "10%"])
    assert args.target == pytest.approx(0.1)


def test_target_accepts_a_fraction():
    args = build_parser().parse_args(["summarize", "clip.mp4", "--target", "0.25"])
    assert args.target == pytest.approx(0.25)


def test_target_defaults_to_off():
    args = build_parser().parse_args(["summarize", "clip.mp4"])
    assert args.target is None


@pytest.mark.parametrize("bad", ["0%", "120%", "-5%", "abc", ""])
def test_target_rejects_nonsense(bad, capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["summarize", "clip.mp4", "--target", bad])

    assert "--target" in capsys.readouterr().err


def test_target_reports_the_threshold_it_chose(sample_video, capsys):
    main(["summarize", str(sample_video.path), "--dry-run", "--target", "50%"])

    out = capsys.readouterr().out
    assert "auto:" in out
    assert "--threshold" in out


def test_without_target_no_auto_line_is_printed(sample_video, capsys):
    main(["summarize", str(sample_video.path), "--dry-run"])

    assert "auto:" not in capsys.readouterr().out


def test_an_unreachable_target_is_explained(sample_video, capsys):
    # The fixture clip is far shorter than one padded event, so any small
    # target overshoots and the reason should be stated.
    main(["summarize", str(sample_video.path), "--dry-run", "--target", "1%"])

    assert "unreachable" in capsys.readouterr().out
