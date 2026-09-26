from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
import pytest


class SampleVideo(NamedTuple):
    path: Path
    width: int
    height: int
    fps: float
    frames: int


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> SampleVideo:
    """Write a small synthetic video so tests never depend on a checked-in asset."""
    sample = SampleVideo(
        path=tmp_path_factory.mktemp("media") / "sample.avi",
        width=64,
        height=48,
        fps=10.0,
        frames=12,
    )

    writer = cv2.VideoWriter(
        str(sample.path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        sample.fps,
        (sample.width, sample.height),
    )
    if not writer.isOpened():
        pytest.skip("No MJPG encoder available to build the sample video.")

    try:
        for index in range(sample.frames):
            frame = np.full(
                (sample.height, sample.width, 3),
                index * 20 % 256,
                dtype=np.uint8,
            )
            writer.write(frame)
    finally:
        writer.release()

    return sample
