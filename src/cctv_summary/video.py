"""Video reading and playback helpers built on OpenCV."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
from cv2.typing import MatLike

DEFAULT_WINDOW_NAME = "cctv-summary"

# Used when a container reports no usable frame rate, so playback still has a pace.
FALLBACK_FPS = 25.0

QUIT_KEYS = frozenset({ord("q"), ord("Q"), 27})


class VideoError(RuntimeError):
    """Raised when a video cannot be opened, read, or displayed."""


@dataclass(frozen=True)
class VideoInfo:
    """Metadata reported by the container for a single video file."""

    path: Path
    width: int
    height: int
    fps: float
    frame_count: int

    @property
    def duration_seconds(self) -> float:
        if self.fps <= 0 or self.frame_count <= 0:
            return 0.0
        return self.frame_count / self.fps


class Display(Protocol):
    """Sink for decoded frames, so playback can be driven without a GUI in tests."""

    def show(self, frame: MatLike) -> None: ...

    def wait(self, delay_ms: int) -> int: ...

    def close(self) -> None: ...


class WindowDisplay:
    """Shows frames in an OpenCV highgui window."""

    def __init__(self, window_name: str = DEFAULT_WINDOW_NAME) -> None:
        self.window_name = window_name

    def show(self, frame: MatLike) -> None:
        try:
            cv2.imshow(self.window_name, frame)
        except cv2.error as exc:
            raise VideoError(
                "OpenCV could not open a display window. This build may lack GUI "
                "support (opencv-python-headless) or no display is available."
            ) from exc

    def wait(self, delay_ms: int) -> int:
        return cv2.waitKey(max(delay_ms, 1)) & 0xFF

    def close(self) -> None:
        # The window may already be gone, or was never created.
        with suppress(cv2.error):
            cv2.destroyWindow(self.window_name)


@contextmanager
def open_capture(path: str | Path) -> Iterator[cv2.VideoCapture]:
    """Open ``path`` as a capture, always releasing it on the way out."""
    video_path = Path(path)
    if not video_path.exists():
        raise VideoError(f"Video file not found: {video_path}")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise VideoError(f"Could not open video (unsupported or corrupt): {video_path}")

    try:
        yield capture
    finally:
        capture.release()


def probe(path: str | Path) -> VideoInfo:
    """Read container metadata without decoding the whole stream."""
    with open_capture(path) as capture:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        return VideoInfo(
            path=Path(path),
            width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=fps if fps > 0 else 0.0,
            frame_count=max(frame_count, 0),
        )


def iter_frames(path: str | Path) -> Iterator[MatLike]:
    """Yield decoded frames in order until the stream is exhausted."""
    with open_capture(path) as capture:
        while True:
            ok, frame = capture.read()
            if not ok:
                return
            yield frame


def frame_delay_ms(fps: float, speed: float = 1.0) -> int:
    """Per-frame wait that approximates ``fps`` scaled by ``speed``."""
    effective_fps = (fps if fps > 0 else FALLBACK_FPS) * speed
    return max(round(1000 / effective_fps), 1)


def play(
    path: str | Path,
    *,
    speed: float = 1.0,
    display: Display | None = None,
) -> int:
    """Play ``path`` frame by frame and return how many frames were shown.

    Playback stops early when the viewer presses ``q`` or Escape.
    """
    if speed <= 0:
        raise VideoError(f"Playback speed must be greater than zero, got {speed}")

    info = probe(path)
    delay_ms = frame_delay_ms(info.fps, speed)
    surface = WindowDisplay() if display is None else display

    shown = 0
    try:
        for frame in iter_frames(path):
            surface.show(frame)
            shown += 1
            if surface.wait(delay_ms) in QUIT_KEYS:
                break
    finally:
        surface.close()

    return shown
