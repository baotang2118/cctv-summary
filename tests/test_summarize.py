from __future__ import annotations

import numpy as np
import pytest

from cctv_summary.summarize import (
    RollingBackground,
    SummaryStats,
    change_score,
    select_frames,
    summarize_video,
    to_comparable,
)
from cctv_summary.video import VideoError, probe

HEIGHT = 120
WIDTH = 160


def frame(fill: int) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), fill, dtype=np.uint8)


def half_lit(fraction: float) -> np.ndarray:
    """Frame with ``fraction`` of its columns set bright, the rest black."""
    img = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    columns = int(WIDTH * fraction)
    img[:, :columns] = 255
    return img


def test_change_score_is_zero_for_identical_frames():
    a = to_comparable(frame(10))
    assert change_score(a, a) == 0.0


def test_change_score_is_one_for_opposite_frames():
    black = to_comparable(frame(0))
    white = to_comparable(frame(255))
    assert change_score(black, white) == pytest.approx(1.0)


def test_change_score_tracks_the_moved_area():
    none = to_comparable(half_lit(0.0))
    quarter = to_comparable(half_lit(0.25))

    assert change_score(none, quarter) == pytest.approx(0.25, abs=0.02)


def test_change_score_respects_tolerance():
    dim = to_comparable(frame(100))
    slightly_brighter = to_comparable(frame(120))

    assert change_score(dim, slightly_brighter, tolerance=5) > 0.9
    assert change_score(dim, slightly_brighter, tolerance=50) == 0.0


def test_change_score_rejects_mismatched_shapes():
    with pytest.raises(ValueError, match="must match"):
        change_score(np.zeros((4, 4), np.uint8), np.zeros((5, 5), np.uint8))


def test_to_comparable_downscales_and_greyscales():
    comparable = to_comparable(np.zeros((1080, 1920, 3), np.uint8))

    assert comparable.ndim == 2
    assert max(comparable.shape) <= 320


def test_to_comparable_leaves_small_frames_alone():
    comparable = to_comparable(np.zeros((40, 50, 3), np.uint8))
    assert comparable.shape == (40, 50)


def test_to_comparable_rejects_empty_frames():
    with pytest.raises(ValueError, match="empty frame"):
        to_comparable(np.zeros((0, 10, 3), np.uint8))


def test_rolling_background_averages_its_contents():
    background = RollingBackground(2)
    background.add(np.full((4, 4), 0, np.uint8))
    background.add(np.full((4, 4), 100, np.uint8))

    assert background.average.mean() == pytest.approx(50, abs=1)


def test_rolling_background_evicts_the_oldest_frame():
    background = RollingBackground(2)
    background.add(np.full((4, 4), 0, np.uint8))
    background.add(np.full((4, 4), 100, np.uint8))
    background.add(np.full((4, 4), 100, np.uint8))

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


def test_rolling_background_rejects_empty_window():
    with pytest.raises(ValueError, match="at least 1"):
        RollingBackground(0)


def test_identical_frames_collapse_to_one():
    frames = [frame(50) for _ in range(30)]

    assert len(list(select_frames(frames))) == 1


def test_alternating_frames_are_all_kept():
    frames = [frame(0) if index % 2 else frame(255) for index in range(10)]

    assert len(list(select_frames(frames))) == 10


def test_first_frame_is_always_kept():
    assert len(list(select_frames([frame(7)]))) == 1


def test_empty_input_yields_nothing():
    assert list(select_frames([])) == []


def test_rolling_window_catches_slow_drift():
    """Each step is tiny, but the scene changes completely over the clip.

    A previous-frame-only check misses this; the background comparison is the
    whole reason the rolling window exists.
    """
    frames = [half_lit(step / 100) for step in range(0, 60, 2)]

    kept = list(select_frames(frames, threshold=0.10, window=10))

    assert len(kept) > 1


def test_drift_would_be_missed_without_the_background():
    """Confirms the drift above really is invisible frame-to-frame."""
    frames = [half_lit(step / 100) for step in range(0, 60, 2)]
    comparable = [to_comparable(item) for item in frames]

    steps = [
        change_score(current, previous)
        for previous, current in zip(comparable[:-1], comparable[1:], strict=True)
    ]

    assert max(steps) < 0.10


def test_threshold_of_zero_keeps_everything():
    frames = [frame(50) for _ in range(5)]

    assert len(list(select_frames(frames, threshold=0.0))) == 5


def test_threshold_of_one_drops_anything_short_of_a_total_change():
    # Scores are compared with "<", so only a literal 100% change survives a
    # threshold of 1.0. These frames change by half, so all but the first go.
    frames = [half_lit(0.0) if index % 2 else half_lit(0.5) for index in range(6)]

    assert len(list(select_frames(frames, threshold=1.0))) == 1


def test_total_change_survives_a_threshold_of_one():
    frames = [frame(0) if index % 2 else frame(255) for index in range(6)]

    assert len(list(select_frames(frames, threshold=1.0))) == 6


def test_threshold_outside_range_is_rejected():
    with pytest.raises(ValueError, match="between 0 and 1"):
        list(select_frames([frame(0)], threshold=1.5))


def test_window_larger_than_the_clip_still_works():
    frames = [frame(50) for _ in range(3)]

    assert len(list(select_frames(frames, window=100))) == 1


def test_window_of_one_still_works():
    frames = [frame(50) for _ in range(5)]

    assert len(list(select_frames(frames, window=1))) == 1


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

    stats = summarize_video(sample_video.path)

    assert stats.total == sample_video.frames
    assert stats.kept >= 1
    assert not destination.exists()


def test_summarize_writes_a_shorter_video(sample_video, tmp_path):
    destination = tmp_path / "summary.avi"

    stats = summarize_video(sample_video.path, destination)

    assert destination.exists()
    assert stats.kept < stats.total

    written = probe(destination)
    assert written.frame_count == stats.kept
    assert written.width == sample_video.width
    assert written.height == sample_video.height


def test_summarize_rejects_a_bad_threshold(sample_video):
    with pytest.raises(VideoError, match="Threshold"):
        summarize_video(sample_video.path, threshold=2.0)


def test_summarize_rejects_a_bad_window(sample_video):
    with pytest.raises(VideoError, match="Window"):
        summarize_video(sample_video.path, window=0)


def test_summarize_rejects_a_bad_tolerance(sample_video):
    with pytest.raises(VideoError, match="Tolerance"):
        summarize_video(sample_video.path, tolerance=300)


def test_summarize_reports_a_missing_source(tmp_path):
    with pytest.raises(VideoError, match="not found"):
        summarize_video(tmp_path / "nope.avi")


def test_summarize_reports_a_missing_output_directory(sample_video, tmp_path):
    with pytest.raises(VideoError, match="directory does not exist"):
        summarize_video(sample_video.path, tmp_path / "missing" / "out.avi")
