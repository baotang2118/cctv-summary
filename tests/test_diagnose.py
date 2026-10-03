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

from cctv_summary.diagnose import (
    BLURRY,
    DARK,
    FROZEN,
    OBSTRUCTED,
    SHAKEN,
    Diagnosis,
    Fault,
    FrameMetrics,
    detail_score,
    detect_faults,
    diagnose_video,
    flat_fraction,
    frame_metrics,
)
from cctv_summary.video import VideoError

FPS = 10.0
WIDTH = 160
HEIGHT = 120


def healthy(**overrides) -> FrameMetrics:
    """One frame of a camera that is working properly."""
    values = {
        "luminance": 110.0,
        "detail": 120.0,
        "flat_fraction": 0.5,
        "changed": 0.01,
        "identical": False,
    }
    values.update(overrides)
    return FrameMetrics(**values)


def series(count: int, **overrides) -> list[FrameMetrics]:
    return [healthy(**overrides) for _ in range(count)]


def textured(level: int = 110, shift: int = 0) -> np.ndarray:
    """A frame with real structure, so detail and flatness mean something."""
    frame = np.full((HEIGHT, WIDTH, 3), level, np.uint8)
    frame[: HEIGHT // 3] = max(level - 30, 0)
    cv2.rectangle(frame, (20, 40), (70, 100), (min(level + 50, 255),) * 3, -1)
    cv2.rectangle(frame, (100, 30), (140, 80), (max(level - 45, 0),) * 3, -1)
    for x in range(0, WIDTH, 12):
        cv2.line(frame, (x, 90), (x, HEIGHT), (max(level - 20, 0),) * 3, 1)
    if shift:
        frame = np.roll(frame, shift, axis=1)
    return frame


def write_clip(path, frames) -> None:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (WIDTH, HEIGHT)
    )
    if not writer.isOpened():
        pytest.skip("No MJPG encoder available.")
    try:
        for frame in frames:
            writer.write(frame)
    finally:
        writer.release()


def test_healthy_metrics_raise_no_faults():
    assert detect_faults(series(40), fps=FPS) == []


def test_a_dark_stretch_is_reported():
    readings = series(40)
    readings[10:30] = series(20, luminance=5.0)

    faults = detect_faults(readings, fps=FPS)

    assert [fault.kind for fault in faults] == [DARK]
    assert faults[0].start == 10
    assert faults[0].end == 30


def test_the_reported_value_is_the_worst_reading():
    readings = series(40)
    readings[10:30] = series(20, luminance=9.0)
    readings[15] = healthy(luminance=2.0)

    assert detect_faults(readings, fps=FPS)[0].value == pytest.approx(2.0)


def test_a_blurred_stretch_is_reported():
    readings = series(40)
    readings[5:25] = series(20, detail=1.0)

    faults = detect_faults(readings, fps=FPS)

    assert [fault.kind for fault in faults] == [BLURRY]


def test_blur_is_not_reported_on_a_dark_picture():
    # Focus cannot be judged without light; calling a black frame "out of
    # focus" would send someone to adjust a lens that is working.
    readings = series(40)
    readings[5:25] = series(20, detail=0.2, luminance=4.0)

    assert [fault.kind for fault in detect_faults(readings, fps=FPS)] == [DARK]


def test_blur_is_not_reported_on_a_covered_lens():
    readings = series(40)
    readings[5:25] = series(20, detail=0.2, flat_fraction=1.0)

    assert [fault.kind for fault in detect_faults(readings, fps=FPS)] == [OBSTRUCTED]


def test_an_obstructed_stretch_is_reported():
    readings = series(40)
    readings[5:25] = series(20, flat_fraction=0.97)

    faults = detect_faults(readings, fps=FPS)

    assert [fault.kind for fault in faults] == [OBSTRUCTED]
    assert faults[0].value == pytest.approx(0.97)


def test_a_frozen_stretch_is_reported():
    readings = series(40)
    readings[10:35] = series(25, identical=True, changed=0.0)

    faults = detect_faults(readings, fps=FPS)

    assert [fault.kind for fault in faults] == [FROZEN]
    assert faults[0].frames == 25


def test_a_brief_repeat_is_not_a_freeze():
    # A couple of repeated frames is ordinary codec behaviour.
    readings = series(40)
    readings[10:14] = series(4, identical=True, changed=0.0)

    assert detect_faults(readings, fps=FPS) == []


def test_a_single_frame_knock_is_reported():
    # A knock is instantaneous, so it is exempt from the minimum duration that
    # keeps the steady-state faults from flickering.
    readings = series(40)
    readings[20] = healthy(changed=0.4)

    faults = detect_faults(readings, fps=FPS)

    assert [fault.kind for fault in faults] == [SHAKEN]
    assert faults[0].start == 20
    assert faults[0].frames == 1


def test_ordinary_motion_is_not_a_knock():
    readings = series(40)
    readings[20] = healthy(changed=0.05)

    assert detect_faults(readings, fps=FPS) == []


def test_a_brief_fault_is_ignored():
    readings = series(40)
    readings[10:13] = series(3, luminance=2.0)

    assert detect_faults(readings, fps=FPS) == []


def test_a_fault_running_to_the_end_is_still_reported():
    readings = series(40)
    readings[20:] = series(20, luminance=2.0)

    faults = detect_faults(readings, fps=FPS)

    assert len(faults) == 1
    assert faults[0].end == 40


def test_separate_stretches_are_separate_faults():
    readings = series(60)
    readings[5:20] = series(15, luminance=2.0)
    readings[40:55] = series(15, luminance=2.0)

    assert len(detect_faults(readings, fps=FPS)) == 2


def test_faults_are_not_padded():
    # A fault's edges are evidence about the camera; widening them would
    # misreport when the picture actually came back.
    readings = series(40)
    readings[10:30] = series(20, luminance=2.0)

    fault = detect_faults(readings, fps=FPS)[0]

    assert (fault.start, fault.end) == (10, 30)


def test_overlapping_faults_are_all_reported():
    readings = series(40)
    readings[10:30] = series(20, luminance=4.0, flat_fraction=1.0)

    kinds = {fault.kind for fault in detect_faults(readings, fps=FPS)}

    assert kinds == {DARK, OBSTRUCTED}


def test_faults_are_ordered_by_when_they_start():
    readings = series(60)
    readings[40:55] = series(15, flat_fraction=1.0)
    readings[5:20] = series(15, luminance=2.0)

    faults = detect_faults(readings, fps=FPS)

    assert [fault.start for fault in faults] == [5, 40]


def test_thresholds_are_adjustable():
    readings = series(40)
    readings[10:30] = series(20, luminance=30.0)

    assert detect_faults(readings, fps=FPS) == []
    assert detect_faults(readings, fps=FPS, dark_luminance=40.0)


def test_fault_reports_its_timings():
    fault = Fault(kind=DARK, start=20, end=50, fps=FPS)

    assert fault.frames == 30
    assert fault.start_seconds == pytest.approx(2.0)
    assert fault.end_seconds == pytest.approx(5.0)
    assert fault.duration_seconds == pytest.approx(3.0)


def test_fault_must_span_at_least_one_frame():
    with pytest.raises(ValueError, match="at least one frame"):
        Fault(kind=DARK, start=10, end=10, fps=FPS)


def test_fault_rejects_an_unknown_kind():
    with pytest.raises(ValueError, match="Unknown fault kind"):
        Fault(kind="haunted", start=0, end=5, fps=FPS)


def test_fault_names_the_metric_it_reports():
    assert Fault(kind=DARK, start=0, end=5).metric == "luminance"
    assert Fault(kind=OBSTRUCTED, start=0, end=5).metric == "flat"


def test_diagnosis_counts_overlapping_faults_once():
    diagnosis = Diagnosis(
        total=100,
        fps=FPS,
        faults=(
            Fault(kind=DARK, start=10, end=30, fps=FPS),
            Fault(kind=OBSTRUCTED, start=20, end=40, fps=FPS),
        ),
    )

    assert diagnosis.faulty_frames == 30
    assert not diagnosis.healthy


def test_a_clean_diagnosis_is_healthy():
    diagnosis = Diagnosis(total=100, fps=FPS)

    assert diagnosis.healthy
    assert diagnosis.faulty_frames == 0
    assert diagnosis.source_seconds == pytest.approx(10.0)


def test_diagnosis_can_select_one_kind():
    diagnosis = Diagnosis(
        total=100,
        fps=FPS,
        faults=(
            Fault(kind=DARK, start=0, end=5, fps=FPS),
            Fault(kind=FROZEN, start=10, end=40, fps=FPS),
        ),
    )

    assert len(diagnosis.of_kind(FROZEN)) == 1
    assert diagnosis.of_kind(SHAKEN) == ()


def test_flat_fraction_is_one_for_a_blank_frame():
    assert flat_fraction(np.full((40, 40), 120, np.uint8)) == pytest.approx(1.0)


def test_flat_fraction_falls_with_texture():
    noisy = (np.arange(1600).reshape(40, 40) * 7 % 255).astype(np.uint8)

    assert flat_fraction(noisy) < 0.5


def test_flat_fraction_rejects_an_empty_frame():
    with pytest.raises(ValueError, match="empty frame"):
        flat_fraction(np.zeros((0, 10), np.uint8))


def test_detail_is_near_zero_for_a_blank_frame():
    assert detail_score(np.full((40, 40), 120, np.uint8)) == pytest.approx(0.0)


def test_detail_falls_when_a_frame_is_blurred():
    sharp = cv2.cvtColor(textured(), cv2.COLOR_BGR2GRAY)
    soft = cv2.GaussianBlur(sharp, (31, 31), 12)

    assert detail_score(soft) < detail_score(sharp) / 10


def test_detail_rejects_an_empty_frame():
    with pytest.raises(ValueError, match="empty frame"):
        detail_score(np.zeros((0, 10), np.uint8))


def test_frame_metrics_sees_the_first_frame_as_unchanged():
    first = next(iter(frame_metrics([textured()])))

    assert first.changed == 0.0
    assert not first.identical


def test_frame_metrics_spots_an_identical_repeat():
    frame = textured()
    readings = list(frame_metrics([frame, frame.copy()]))

    assert readings[1].identical
    assert readings[1].changed == 0.0


def test_a_live_scene_is_not_identical():
    rng = np.random.default_rng(3)
    frames = [
        np.clip(textured() + rng.normal(0, 4, (HEIGHT, WIDTH, 3)), 0, 255).astype(
            np.uint8
        )
        for _ in range(3)
    ]

    assert not any(reading.identical for reading in frame_metrics(frames))


def test_frame_metrics_measures_brightness():
    readings = list(frame_metrics([np.full((HEIGHT, WIDTH, 3), 30, np.uint8)]))

    assert readings[0].luminance == pytest.approx(30.0, abs=1.0)


def test_a_knocked_camera_changes_far_more_than_a_subject():
    knocked = list(frame_metrics([textured(), textured(shift=40)]))

    moving_subject = textured()
    moving_subject[60:100, 20:50] = 240
    subject = list(frame_metrics([textured(), moving_subject]))

    assert knocked[1].changed > subject[1].changed * 3


def test_diagnose_finds_a_dark_clip(tmp_path):
    clip = tmp_path / "dark.avi"
    write_clip(clip, [textured(level=4) for _ in range(40)])

    diagnosis = diagnose_video(clip)

    assert not diagnosis.healthy
    assert diagnosis.of_kind(DARK)


def test_diagnose_finds_a_frozen_clip(tmp_path):
    clip = tmp_path / "frozen.avi"
    frame = textured()
    write_clip(clip, [frame.copy() for _ in range(40)])

    diagnosis = diagnose_video(clip)

    assert diagnosis.of_kind(FROZEN)


def test_diagnose_passes_a_healthy_clip(tmp_path):
    rng = np.random.default_rng(11)
    clip = tmp_path / "healthy.avi"
    frames = []
    for index in range(40):
        frame = np.clip(
            textured() + rng.normal(0, 3, (HEIGHT, WIDTH, 3)), 0, 255
        ).astype(np.uint8)
        if 15 <= index < 25:
            frame[70:100, (index - 15) * 10 : (index - 15) * 10 + 18] = 235
        frames.append(frame)
    write_clip(clip, frames)

    diagnosis = diagnose_video(clip)

    assert diagnosis.healthy
    assert diagnosis.total == 40


def test_diagnose_records_the_settings_it_ran_with(tmp_path):
    clip = tmp_path / "clip.avi"
    write_clip(clip, [textured() for _ in range(10)])

    diagnosis = diagnose_video(
        clip,
        dark_luminance=30.0,
        blur_detail=5.0,
        flat_share=0.8,
        shake_change=0.3,
        min_fault_seconds=0.5,
        freeze_seconds=3.0,
        comparison_edge=64,
    )

    assert diagnosis.dark_luminance == 30.0
    assert diagnosis.blur_detail == 5.0
    assert diagnosis.flat_fraction == 0.8
    assert diagnosis.shake_change == 0.3
    assert diagnosis.min_fault_seconds == 0.5
    assert diagnosis.freeze_seconds == 3.0
    assert diagnosis.comparison_edge == 64


def test_diagnose_reports_typical_readings(tmp_path):
    clip = tmp_path / "clip.avi"
    write_clip(clip, [textured() for _ in range(10)])

    diagnosis = diagnose_video(clip)

    assert diagnosis.luminance_median > 50
    assert diagnosis.detail_median > 0


def test_diagnose_stays_silent_without_a_reporter(tmp_path, capsys):
    clip = tmp_path / "clip.avi"
    write_clip(clip, [textured() for _ in range(5)])

    diagnose_video(clip)

    assert capsys.readouterr().err == ""


def test_diagnose_reports_a_missing_source(tmp_path):
    with pytest.raises(VideoError, match="not found"):
        diagnose_video(tmp_path / "nope.avi")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"dark_luminance": -1}, "between 0 and 255"),
        ({"dark_luminance": 300}, "between 0 and 255"),
        ({"blur_detail": -1}, "cannot be negative"),
        ({"flat_share": 0}, "above 0 and at most 1"),
        ({"flat_share": 1.5}, "above 0 and at most 1"),
        ({"shake_change": 0}, "above 0 and at most 1"),
        ({"min_fault_seconds": -1}, "cannot be negative"),
        ({"freeze_seconds": -1}, "cannot be negative"),
        ({"comparison_edge": 0}, "at least 1 pixel"),
    ],
)
def test_diagnose_rejects_bad_settings(tmp_path, kwargs, message):
    clip = tmp_path / "clip.avi"
    write_clip(clip, [textured() for _ in range(3)])

    with pytest.raises(VideoError, match=message):
        diagnose_video(clip, **kwargs)
