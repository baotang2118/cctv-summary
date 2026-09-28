from __future__ import annotations

import cv2
import numpy as np
import pytest

from cctv_summary.summarize import (
    Event,
    RollingBackground,
    SummaryStats,
    change_score,
    detect_events,
    downscale_to_gray,
    motion_scores,
    summarize_video,
)
from cctv_summary.video import VideoError, iter_frames, probe

HEIGHT = 120
WIDTH = 160
FPS = 10.0


def frame(fill: int) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), fill, dtype=np.uint8)


def half_lit(fraction: float) -> np.ndarray:
    """Frame with ``fraction`` of its columns set bright, the rest black."""
    img = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    img[:, : int(WIDTH * fraction)] = 255
    return img


def signal(pattern: str) -> list[float]:
    """Build a motion signal from a sketch: '.' is quiet, '#' is motion."""
    return [1.0 if char == "#" else 0.0 for char in pattern]


def test_change_score_is_zero_for_identical_frames():
    a = downscale_to_gray(frame(10))
    assert change_score(a, a) == 0.0


def test_change_score_is_one_for_opposite_frames():
    assert change_score(
        downscale_to_gray(frame(0)), downscale_to_gray(frame(255))
    ) == pytest.approx(1.0)


def test_change_score_tracks_the_moved_area():
    none = downscale_to_gray(half_lit(0.0))
    quarter = downscale_to_gray(half_lit(0.25))

    assert change_score(none, quarter) == pytest.approx(0.25, abs=0.02)


def test_change_score_respects_tolerance():
    dim = downscale_to_gray(frame(100))
    brighter = downscale_to_gray(frame(120))

    assert change_score(dim, brighter, tolerance=5) > 0.9
    assert change_score(dim, brighter, tolerance=50) == 0.0


def test_change_score_rejects_mismatched_shapes():
    with pytest.raises(ValueError, match="must match"):
        change_score(np.zeros((4, 4), np.uint8), np.zeros((5, 5), np.uint8))


def test_downscale_to_gray_shrinks_and_greyscales():
    gray = downscale_to_gray(np.zeros((1080, 1920, 3), np.uint8))

    assert gray.ndim == 2
    assert max(gray.shape) <= 320


def test_downscale_to_gray_leaves_small_frames_alone():
    assert downscale_to_gray(np.zeros((40, 50, 3), np.uint8)).shape == (40, 50)


def test_downscale_to_gray_rejects_empty_frames():
    with pytest.raises(ValueError, match="empty frame"):
        downscale_to_gray(np.zeros((0, 10, 3), np.uint8))


def test_rolling_background_averages_its_contents():
    background = RollingBackground(2)
    background.add(np.full((4, 4), 0, np.uint8))
    background.add(np.full((4, 4), 100, np.uint8))

    assert background.average.mean() == pytest.approx(50, abs=1)


def test_rolling_background_evicts_the_oldest_frame():
    background = RollingBackground(2)
    for value in (0, 100, 100):
        background.add(np.full((4, 4), value, np.uint8))

    assert len(background) == 2
    assert background.average.mean() == pytest.approx(100, abs=1)


def test_rolling_background_reports_when_filled():
    background = RollingBackground(2)
    assert not background.filled
    assert background.average is None

    background.add(np.zeros((4, 4), np.uint8))
    assert not background.filled

    background.add(np.zeros((4, 4), np.uint8))
    assert background.filled


def test_rolling_background_rejects_an_empty_window():
    with pytest.raises(ValueError, match="at least 1"):
        RollingBackground(0)


def test_still_footage_scores_no_motion():
    scores = list(motion_scores([frame(50) for _ in range(10)]))

    assert scores[0] == 0.0
    assert max(scores) == 0.0


def test_moving_footage_scores_motion():
    frames = [frame(0) if index % 2 else frame(255) for index in range(10)]

    assert max(motion_scores(frames)) > 0.5


def test_motion_is_measured_against_the_background_not_the_last_frame():
    # A subject that creeps forward stays visible against the background even
    # though consecutive frames barely differ.
    frames = [half_lit(step / 100) for step in range(0, 40, 2)]

    assert max(motion_scores(frames, window=10)) > 0.05


def test_no_scores_means_no_events():
    assert detect_events([], fps=FPS) == []


def test_quiet_footage_yields_no_events():
    assert detect_events(signal("." * 50), fps=FPS) == []


def test_a_burst_of_motion_becomes_one_event():
    events = detect_events(
        signal("." * 20 + "#" * 20 + "." * 20),
        fps=FPS,
        pad_seconds=0,
        min_event_seconds=0.5,
    )

    assert len(events) == 1
    assert events[0].start == 20
    assert events[0].end == 40


def test_padding_extends_an_event_both_ways():
    events = detect_events(
        signal("." * 20 + "#" * 20 + "." * 20),
        fps=FPS,
        pad_seconds=1.0,
        min_event_seconds=0.5,
    )

    assert events[0].start == 10
    assert events[0].end == 50


def test_padding_is_clamped_to_the_clip():
    events = detect_events(
        signal("#" * 10), fps=FPS, pad_seconds=5.0, min_event_seconds=0.5
    )

    assert events[0].start == 0
    assert events[0].end == 10


def test_a_brief_pause_does_not_split_an_event():
    # Someone slowing mid-frame should stay a single event.
    events = detect_events(
        signal("." * 10 + "#" * 15 + "." * 3 + "#" * 15 + "." * 10),
        fps=FPS,
        pad_seconds=0,
        min_event_seconds=0.5,
        cooldown_seconds=1.0,
    )

    assert len(events) == 1


def test_a_long_gap_splits_events():
    events = detect_events(
        signal("." * 10 + "#" * 15 + "." * 40 + "#" * 15 + "." * 10),
        fps=FPS,
        pad_seconds=0,
        min_event_seconds=0.5,
    )

    assert len(events) == 2


def test_nearby_events_merge_once_padded():
    events = detect_events(
        signal("." * 10 + "#" * 15 + "." * 20 + "#" * 15 + "." * 10),
        fps=FPS,
        pad_seconds=2.0,
        min_event_seconds=0.5,
    )

    assert len(events) == 1


def test_short_blips_are_ignored():
    events = detect_events(
        signal("." * 20 + "#" * 2 + "." * 20),
        fps=FPS,
        pad_seconds=0,
        min_event_seconds=1.0,
    )

    assert events == []


def test_padding_cannot_rescue_a_blip():
    # Padding is applied after the length filter, so noise stays filtered.
    events = detect_events(
        signal("." * 20 + "#" * 2 + "." * 20),
        fps=FPS,
        pad_seconds=3.0,
        min_event_seconds=1.0,
    )

    assert events == []


def test_motion_running_to_the_end_still_closes():
    events = detect_events(
        signal("." * 10 + "#" * 20), fps=FPS, pad_seconds=0, min_event_seconds=0.5
    )

    assert len(events) == 1
    assert events[0].end == 30


def test_events_never_overlap():
    events = detect_events(
        signal(("#" * 12 + "." * 25) * 4), fps=FPS, min_event_seconds=0.5
    )

    for earlier, later in zip(events[:-1], events[1:], strict=True):
        assert earlier.end < later.start


def test_detect_events_rejects_a_bad_threshold():
    with pytest.raises(ValueError, match="between 0 and 1"):
        detect_events(signal("###"), fps=FPS, threshold=2.0)


def test_detect_events_rejects_a_bad_fps():
    with pytest.raises(ValueError, match="fps"):
        detect_events(signal("###"), fps=0)


def test_detect_events_rejects_negative_padding():
    with pytest.raises(ValueError, match="Padding"):
        detect_events(signal("###"), fps=FPS, pad_seconds=-1)


def test_event_reports_frames_and_timings():
    event = Event(start=100, end=250, fps=25.0)

    assert event.frames == 150
    assert event.start_seconds == pytest.approx(4.0)
    assert event.end_seconds == pytest.approx(10.0)
    assert event.duration_seconds == pytest.approx(6.0)


def test_event_must_span_at_least_one_frame():
    with pytest.raises(ValueError, match="at least one frame"):
        Event(start=10, end=10, fps=FPS)


def test_summary_stats_derives_counts_and_durations():
    stats = SummaryStats(total=100, kept=25, fps=25.0)

    assert stats.dropped == 75
    assert stats.kept_ratio == pytest.approx(0.25)
    assert stats.source_seconds == pytest.approx(4.0)
    assert stats.summary_seconds == pytest.approx(1.0)


def test_summary_stats_handles_empty_input():
    stats = SummaryStats(total=0, kept=0, fps=0.0)

    assert stats.kept_ratio == 0.0
    assert stats.source_seconds == 0.0
    assert stats.summary_seconds == 0.0


def test_dry_run_reports_without_writing(sample_video, tmp_path):
    destination = tmp_path / "out.avi"

    stats = summarize_video(sample_video.path, threshold=0.001, min_event_seconds=0)

    assert stats.total == sample_video.frames
    assert not destination.exists()


def test_summarize_writes_only_the_events(sample_video, tmp_path):
    destination = tmp_path / "summary.avi"

    stats = summarize_video(
        sample_video.path, destination, threshold=0.001, min_event_seconds=0
    )

    assert destination.exists()
    assert stats.events

    written = probe(destination)
    assert written.frame_count == stats.kept
    assert written.width == sample_video.width
    assert written.height == sample_video.height


def test_summary_frames_carry_the_summarized_badge(sample_video, tmp_path):
    destination = tmp_path / "badged.avi"

    summarize_video(
        sample_video.path, destination, threshold=0.001, min_event_seconds=0
    )

    source = next(iter_frames(sample_video.path))
    written = next(iter_frames(destination))
    height, width = written.shape[:2]

    changed = np.any(cv2.absdiff(written, source) > 60, axis=2)
    assert changed[: height // 2, width // 2 :].any()


def test_footage_without_motion_writes_nothing(tmp_path):
    still = tmp_path / "still.avi"
    writer = cv2.VideoWriter(
        str(still), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (WIDTH, HEIGHT)
    )
    if not writer.isOpened():
        pytest.skip("No MJPG encoder available.")
    for _ in range(40):
        writer.write(frame(90))
    writer.release()

    stats = summarize_video(still, tmp_path / "out.avi", threshold=0.5)

    assert stats.events == ()
    assert stats.kept == 0
    assert not (tmp_path / "out.avi").exists()


def test_summarize_rejects_a_bad_threshold(sample_video):
    with pytest.raises(VideoError, match="Threshold"):
        summarize_video(sample_video.path, threshold=2.0)


def test_summarize_rejects_a_bad_window(sample_video):
    with pytest.raises(VideoError, match="Window"):
        summarize_video(sample_video.path, window=0)


def test_summarize_rejects_a_bad_tolerance(sample_video):
    with pytest.raises(VideoError, match="Tolerance"):
        summarize_video(sample_video.path, tolerance=300)


def test_summarize_rejects_negative_padding(sample_video):
    with pytest.raises(VideoError, match="Padding"):
        summarize_video(sample_video.path, pad_seconds=-1)


def test_summarize_reports_a_missing_source(tmp_path):
    with pytest.raises(VideoError, match="not found"):
        summarize_video(tmp_path / "nope.avi")


def test_summarize_reports_a_missing_output_directory(sample_video, tmp_path):
    with pytest.raises(VideoError, match="directory does not exist"):
        summarize_video(
            sample_video.path,
            tmp_path / "missing" / "out.avi",
            threshold=0.001,
            min_event_seconds=0,
        )


class RecordingProgress:
    """Captures the calls summarize_video makes, without any terminal."""

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


def test_progress_is_reported_while_analysing(sample_video):
    progress = RecordingProgress()

    summarize_video(sample_video.path, threshold=0.5, progress=progress)

    assert progress.stages[0][0] == "analysing"
    assert progress.advances == sample_video.frames
    assert progress.finished == 1


def test_progress_covers_both_passes(sample_video, tmp_path):
    progress = RecordingProgress()

    stats = summarize_video(
        sample_video.path,
        tmp_path / "out.avi",
        threshold=0.001,
        min_event_seconds=0,
        progress=progress,
    )

    labels = [stage for stage, _ in progress.stages]
    assert labels == ["analysing", "writing"]
    # Every analysed frame plus every written frame.
    assert progress.advances == stats.total + stats.kept
    assert progress.finished == 2


def test_the_writing_pass_is_sized_by_kept_frames(sample_video, tmp_path):
    progress = RecordingProgress()

    stats = summarize_video(
        sample_video.path,
        tmp_path / "out.avi",
        threshold=0.001,
        min_event_seconds=0,
        progress=progress,
    )

    assert dict(progress.stages)["writing"] == stats.kept


def test_summarize_stays_silent_without_a_reporter(sample_video, capsys):
    summarize_video(sample_video.path, threshold=0.5)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
