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

import cv2
import numpy as np
import pytest

from cctv_summary.summarize import (
    DEFAULT_THRESHOLD,
    SOLVER_MIN_THRESHOLD,
    Event,
    RollingBackground,
    SummaryStats,
    change_score,
    comparison_edge_for,
    detect_events,
    downscale_to_gray,
    keep_ratio_for,
    motion_scores,
    smallest_possible_ratio,
    solve_threshold,
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


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (640, 480, 256),
        (1280, 720, 256),
        (1920, 1080, 320),
        (2560, 1440, 480),
        (3840, 2160, 640),
        (1080, 1920, 320),  # portrait sits in the same tier as its landscape twin
    ],
)
def test_comparison_edge_steps_up_with_resolution(width, height, expected):
    assert comparison_edge_for(width, height) == expected


def test_comparison_edge_rejects_empty_frames():
    with pytest.raises(ValueError, match="must be positive"):
        comparison_edge_for(0, 100)


def test_downscale_to_gray_follows_the_resolution_tier():
    # 4K is compared at a larger size than 1080p: squeezed too far, a small
    # distant figure's contrast is averaged away and it stops registering.
    assert max(downscale_to_gray(np.zeros((2160, 3840, 3), np.uint8)).shape) == 640
    assert max(downscale_to_gray(np.zeros((720, 1280, 3), np.uint8)).shape) == 256


def test_downscale_to_gray_honours_an_explicit_edge():
    gray = downscale_to_gray(np.zeros((1080, 1920, 3), np.uint8), edge=96)

    assert gray.shape == (54, 96)


def test_downscale_to_gray_rejects_a_zero_edge():
    with pytest.raises(ValueError, match="at least 1 pixel"):
        downscale_to_gray(np.zeros((40, 50, 3), np.uint8), edge=0)


def test_motion_scores_compare_at_the_requested_edge():
    # A 4-pixel blob survives a 320-wide comparison and is erased by a 16-wide
    # one, which is exactly the detail the tiers trade away.
    frames = []
    for index in range(12):
        img = np.zeros((1080, 1920, 3), np.uint8)
        if index >= 6:
            img[540:544, 960:964] = 255
        frames.append(img)

    detailed = list(motion_scores(frames, window=3, comparison_edge=320))
    coarse = list(motion_scores(frames, window=3, comparison_edge=16))

    assert max(detailed) > 0.0
    assert max(coarse) == 0.0


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


def test_the_running_total_matches_a_plain_mean():
    # The average is accumulated incrementally, so it has to agree with the
    # obvious implementation at every step, not just at the end.
    rng = np.random.default_rng(7)
    background = RollingBackground(5)
    window = []

    for _ in range(40):
        frame = rng.integers(0, 256, (8, 9), dtype=np.uint8)
        background.add(frame)
        window = (window + [frame])[-5:]

        expected = np.stack(window).astype(np.float64).mean(axis=0).astype(np.uint8)
        assert np.array_equal(background.average, expected)


def test_the_running_total_does_not_drift():
    # Adding and subtracting floats can accumulate error. Pixel sums stay far
    # below the range float64 represents exactly, so this must stay bit-exact
    # however long it runs.
    rng = np.random.default_rng(11)
    background = RollingBackground(30)
    window = []

    for _ in range(600):
        frame = rng.integers(0, 256, (6, 6), dtype=np.uint8)
        background.add(frame)
        window = (window + [frame])[-30:]

    expected = np.stack(window).astype(np.float64).mean(axis=0).astype(np.uint8)
    assert np.array_equal(background.average, expected)


def test_a_reused_buffer_cannot_corrupt_the_total():
    # Callers may hand back the same array each time, as OpenCV does with its
    # decode buffers. The total is updated when a frame is added, so without a
    # defensive copy a later mutation would subtract values that were never
    # added and break the average permanently.
    background = RollingBackground(2)
    buffer = np.full((4, 4), 10, np.uint8)

    background.add(buffer)
    buffer[:] = 200
    background.add(buffer)
    background.add(np.full((4, 4), 30, np.uint8))

    expected = np.stack(
        [np.full((4, 4), 200, np.uint8), np.full((4, 4), 30, np.uint8)]
    ).mean(axis=0)
    assert np.array_equal(background.average, expected.astype(np.uint8))


def test_frames_of_a_different_shape_are_rejected():
    background = RollingBackground(3)
    background.add(np.zeros((4, 4), np.uint8))

    with pytest.raises(ValueError, match="must match"):
        background.add(np.zeros((5, 5), np.uint8))


def test_a_window_of_one_holds_only_the_latest_frame():
    background = RollingBackground(1)
    background.add(np.full((3, 3), 10, np.uint8))
    background.add(np.full((3, 3), 250, np.uint8))

    assert background.average.mean() == pytest.approx(250)


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


def test_summarize_rejects_a_bad_comparison_edge(sample_video):
    with pytest.raises(VideoError, match="Comparison edge"):
        summarize_video(sample_video.path, comparison_edge=0)


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


def noisy_signal(length: int, bursts: list[tuple[int, int, float]]) -> list[float]:
    """A quiet signal with motion bursts of a given strength."""
    values = [0.0] * length
    for start, end, level in bursts:
        for index in range(start, end):
            values[index] = level
    return values


def test_solver_hits_a_reachable_target():
    # Long enough that padding does not dominate what is achievable.
    scores = noisy_signal(
        3000,
        [(i, i + 40, 0.02 + (i % 7) * 0.01) for i in range(0, 3000, 300)],
    )

    threshold = solve_threshold(scores, fps=10.0, target_ratio=0.3)
    kept = keep_ratio_for(scores, fps=10.0, threshold=threshold)

    assert 0.2 <= kept <= 0.45


def test_a_lower_target_yields_a_higher_threshold():
    scores = noisy_signal(
        3000,
        [(i, i + 40, 0.01 + (i % 11) * 0.01) for i in range(0, 3000, 200)],
    )

    loose = solve_threshold(scores, fps=10.0, target_ratio=0.5)
    strict = solve_threshold(scores, fps=10.0, target_ratio=0.1)

    assert strict > loose


def test_the_solver_always_leaves_something_to_keep():
    # A target below one shortest event is unreachable, but returning an empty
    # summary is worse than returning the smallest real one.
    scores = noisy_signal(600, [(100, 140, 0.05), (400, 440, 0.05)])

    threshold = solve_threshold(scores, fps=10.0, target_ratio=0.01)

    assert keep_ratio_for(scores, fps=10.0, threshold=threshold) > 0


def test_still_footage_solves_to_the_lowest_threshold():
    threshold = solve_threshold([0.0] * 500, fps=10.0, target_ratio=0.1)

    assert threshold == pytest.approx(SOLVER_MIN_THRESHOLD)


def test_an_empty_signal_falls_back_to_the_default():
    assert solve_threshold([], fps=10.0, target_ratio=0.1) == DEFAULT_THRESHOLD


def test_the_solver_rejects_a_target_outside_the_range():
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="Target"):
            solve_threshold([0.1] * 100, fps=10.0, target_ratio=bad)


def test_the_solver_rejects_a_bad_fps():
    with pytest.raises(ValueError, match="fps"):
        solve_threshold([0.1] * 100, fps=0, target_ratio=0.1)


def test_keep_ratio_rises_as_the_threshold_falls():
    scores = noisy_signal(2000, [(i, i + 50, 0.03) for i in range(0, 2000, 250)])

    ratios = [
        keep_ratio_for(scores, fps=10.0, threshold=t) for t in (0.001, 0.01, 0.05, 0.2)
    ]

    assert ratios == sorted(ratios, reverse=True)


def test_keep_ratio_of_an_empty_signal_is_zero():
    assert keep_ratio_for([], fps=10.0, threshold=0.01) == 0.0


def test_the_smallest_event_scales_with_the_clip():
    short = smallest_possible_ratio(300, fps=10.0)
    long_clip = smallest_possible_ratio(30000, fps=10.0)

    assert short > long_clip
    assert long_clip > 0


def test_the_smallest_event_is_capped_at_the_whole_clip():
    assert smallest_possible_ratio(10, fps=10.0) == 1.0


def test_the_smallest_event_of_an_empty_clip_is_zero():
    assert smallest_possible_ratio(0, fps=10.0) == 0.0


def test_target_overrides_threshold(sample_video):
    explicit = summarize_video(sample_video.path, threshold=0.5)
    targeted = summarize_video(sample_video.path, threshold=0.5, target_ratio=0.5)

    assert targeted.threshold != explicit.threshold
    assert targeted.target_ratio == 0.5


def test_stats_report_the_threshold_actually_used(sample_video):
    stats = summarize_video(sample_video.path, threshold=0.123)

    assert stats.threshold == 0.123
    assert stats.target_ratio is None


def test_summarize_rejects_a_bad_target(sample_video):
    with pytest.raises(VideoError, match="Target"):
        summarize_video(sample_video.path, target_ratio=0.0)
