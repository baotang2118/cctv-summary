from __future__ import annotations

import pytest

from cctv_summary.video import (
    QUIT_KEYS,
    VideoError,
    VideoInfo,
    frame_delay_ms,
    iter_frames,
    play,
    probe,
)


class FakeDisplay:
    """Records frames instead of drawing them, and can simulate a key press."""

    def __init__(self, quit_after: int | None = None) -> None:
        self.quit_after = quit_after
        self.frames = 0
        self.delays: list[int] = []
        self.closed = False

    def show(self, frame) -> None:
        self.frames += 1

    def wait(self, delay_ms: int) -> int:
        self.delays.append(delay_ms)
        if self.quit_after is not None and self.frames >= self.quit_after:
            return ord("q")
        return 255

    def close(self) -> None:
        self.closed = True


def test_probe_reports_container_metadata(sample_video):
    info = probe(sample_video.path)

    assert info.width == sample_video.width
    assert info.height == sample_video.height
    assert info.fps == pytest.approx(sample_video.fps, rel=0.05)
    assert info.frame_count == sample_video.frames


def test_duration_uses_fps_and_frame_count():
    info = VideoInfo(path="x.avi", width=1, height=1, fps=25.0, frame_count=50)
    assert info.duration_seconds == pytest.approx(2.0)


def test_duration_is_zero_when_fps_unknown():
    info = VideoInfo(path="x.avi", width=1, height=1, fps=0.0, frame_count=50)
    assert info.duration_seconds == 0.0


def test_iter_frames_yields_every_frame(sample_video):
    frames = list(iter_frames(sample_video.path))

    assert len(frames) == sample_video.frames
    assert frames[0].shape == (sample_video.height, sample_video.width, 3)


def test_missing_file_raises_video_error(tmp_path):
    with pytest.raises(VideoError, match="not found"):
        probe(tmp_path / "nope.avi")


def test_unreadable_file_raises_video_error(tmp_path):
    broken = tmp_path / "broken.avi"
    broken.write_bytes(b"definitely not a video")

    with pytest.raises(VideoError, match="Could not open video"):
        probe(broken)


def test_play_shows_all_frames_and_closes_display(sample_video):
    display = FakeDisplay()

    assert play(sample_video.path, display=display) == sample_video.frames
    assert display.frames == sample_video.frames
    assert display.closed


def test_play_stops_early_on_quit_key(sample_video):
    display = FakeDisplay(quit_after=3)

    assert play(sample_video.path, display=display) == 3
    assert display.closed


def test_play_rejects_non_positive_speed(sample_video):
    with pytest.raises(VideoError, match="greater than zero"):
        play(sample_video.path, speed=0, display=FakeDisplay())


def test_play_reports_missing_file(tmp_path):
    display = FakeDisplay()

    with pytest.raises(VideoError):
        play(tmp_path / "nope.avi", display=display)

    assert display.frames == 0


def test_play_speed_shortens_frame_delay(sample_video):
    slow = FakeDisplay()
    fast = FakeDisplay()

    play(sample_video.path, speed=1.0, display=slow)
    play(sample_video.path, speed=4.0, display=fast)

    assert fast.delays[0] < slow.delays[0]


def test_frame_delay_matches_frame_rate():
    assert frame_delay_ms(25.0) == 40
    assert frame_delay_ms(25.0, speed=2.0) == 20


def test_frame_delay_falls_back_when_fps_unknown():
    assert frame_delay_ms(0.0) == frame_delay_ms(25.0)


def test_frame_delay_never_returns_zero():
    assert frame_delay_ms(1000.0, speed=100.0) >= 1


def test_escape_and_q_are_quit_keys():
    assert ord("q") in QUIT_KEYS
    assert 27 in QUIT_KEYS
