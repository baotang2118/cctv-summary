from __future__ import annotations

import pytest

from cctv_summary import __version__
from cctv_summary.cli import main
from cctv_summary.overlay import draw_triangle
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
    assert calls["overlay"] is None
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


def test_play_triangle_flag_passes_the_overlay(monkeypatch, tmp_path):
    captured = {}

    def fake_play(video, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("cctv_summary.cli.play", fake_play)

    assert main(["play", str(tmp_path / "clip.avi"), "--triangle"]) == 0
    assert captured["overlay"] is draw_triangle


def test_play_surfaces_video_errors(monkeypatch, capsys, tmp_path):
    def fake_play(video, **kwargs):
        raise VideoError("boom")

    monkeypatch.setattr("cctv_summary.cli.play", fake_play)

    assert main(["play", str(tmp_path / "clip.avi")]) == 1
    assert "error: boom" in capsys.readouterr().err
