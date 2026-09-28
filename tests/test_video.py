from __future__ import annotations

import cv2
import numpy as np
import pytest

from cctv_summary.video import (
    NO_KEY,
    QUIT_KEYS,
    NullDisplay,
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
        self.shown: list[object] = []
        self.closed = False

    def show(self, frame) -> None:
        self.frames += 1
        self.shown.append(frame)

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


def test_headless_play_reads_every_frame(sample_video):
    assert play(sample_video.path, headless=True) == sample_video.frames


def test_headless_play_never_opens_a_window(sample_video, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("headless playback must not touch the GUI")

    monkeypatch.setattr(cv2, "imshow", explode)
    monkeypatch.setattr(cv2, "waitKey", explode)
    monkeypatch.setattr(cv2, "destroyWindow", explode)

    assert play(sample_video.path, headless=True) == sample_video.frames


def test_explicit_display_overrides_headless(sample_video):
    display = FakeDisplay()

    play(sample_video.path, headless=True, display=display)

    assert display.frames == sample_video.frames
    assert display.closed


def test_normal_speed_shows_frames_untouched(sample_video):
    display = FakeDisplay()
    source = next(iter_frames(sample_video.path))

    play(sample_video.path, speed=1.0, display=display)

    assert np.array_equal(display.shown[0], source)


def test_fast_playback_badges_the_frames(sample_video):
    display = FakeDisplay()
    source = next(iter_frames(sample_video.path))

    play(sample_video.path, speed=4.0, display=display)

    shown = display.shown[0]
    assert not np.array_equal(shown, source)
    assert shown.shape == source.shape


def test_badge_lands_in_the_top_right_corner(sample_video):
    display = FakeDisplay()
    source = next(iter_frames(sample_video.path))

    play(sample_video.path, speed=4.0, display=display)

    changed = np.any(display.shown[0] != source, axis=2)
    height, width = changed.shape
    # play() decides *whether* to badge; exact placement is overlay.py's job
    # and is asserted there at realistic resolutions. The fixture clip is only
    # 64x48, too small to make strong corner claims about.
    assert changed[: height // 2, width // 2 :].any()
    assert not changed[:, : width // 3].any()


def test_slower_than_real_time_is_not_badged(sample_video):
    display = FakeDisplay()
    source = next(iter_frames(sample_video.path))

    play(sample_video.path, speed=0.5, display=display)

    assert np.array_equal(display.shown[0], source)


def test_headless_playback_is_never_badged(sample_video):
    display = FakeDisplay()
    source = next(iter_frames(sample_video.path))

    play(sample_video.path, speed=8.0, headless=True, display=display)

    assert np.array_equal(display.shown[0], source)


def test_null_display_never_reports_a_quit_key():
    display = NullDisplay()

    assert display.wait(40) == NO_KEY
    assert NO_KEY not in QUIT_KEYS


def test_null_display_ignores_frames_and_closes_cleanly(sample_video):
    display = NullDisplay()

    display.show(next(iter_frames(sample_video.path)))
    display.close()


class RecordingProgress:
    """Captures reporter calls without touching a terminal."""

    def __init__(self) -> None:
        self.stages: list[tuple[str, int]] = []
        self.advances = 0
        self.finished = 0

    def start(self, label: str, total: int) -> None:
        self.stages.append((label, total))

    def advance(self, amount: int = 1) -> None:
        self.advances += amount

    def finish(self) -> None:
        self.finished += 1


def test_headless_playback_reports_progress(sample_video):
    progress = RecordingProgress()

    play(sample_video.path, headless=True, progress=progress)

    assert progress.stages == [("decoding", sample_video.frames)]
    assert progress.advances == sample_video.frames
    assert progress.finished == 1


def test_play_stays_silent_without_a_reporter(sample_video, capsys):
    play(sample_video.path, headless=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_progress_is_finished_even_when_quitting_early(sample_video):
    # Otherwise the half-drawn line is left on the terminal.
    progress = RecordingProgress()

    play(sample_video.path, display=FakeDisplay(quit_after=2), progress=progress)

    assert progress.finished == 1


def test_a_failure_before_decoding_leaves_no_progress_line(tmp_path):
    # probe() rejects the file before anything is drawn, so there is nothing
    # to clear up. What matters is that start and finish stay balanced.
    progress = RecordingProgress()

    with pytest.raises(VideoError):
        play(tmp_path / "nope.avi", headless=True, progress=progress)

    assert progress.stages == []
    assert progress.finished == len(progress.stages)
