"""Video reading and playback helpers built on OpenCV."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
from cv2.typing import MatLike

from cctv_summary.overlay import draw_fast_forward

DEFAULT_WINDOW_NAME = "cctv-summary"

# Used when a container reports no usable frame rate, so playback still has a pace.
FALLBACK_FPS = 25.0

QUIT_KEYS = frozenset({ord("q"), ord("Q"), 27})

# What `cv2.waitKey(...) & 0xFF` yields when no key was pressed.
NO_KEY = 255


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


class NullDisplay:
    """Consumes frames without any GUI, for headless runs.

    Never waits, so decoding proceeds as fast as the container allows.
    """

    def show(self, frame: MatLike) -> None:
        pass

    def wait(self, delay_ms: int) -> int:
        return NO_KEY

    def close(self) -> None:
        pass


# Container extension -> fourcc code used when encoding output.
FOURCC_BY_SUFFIX = {
    ".avi": "MJPG",
    ".mkv": "mp4v",
    ".mov": "mp4v",
    ".mp4": "mp4v",
}

DEFAULT_FOURCC = "mp4v"


def _fourcc_for(path: Path) -> str:
    return FOURCC_BY_SUFFIX.get(path.suffix.lower(), DEFAULT_FOURCC)


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


def write_frames(
    path: str | Path,
    frames: Iterable[MatLike],
    *,
    fps: float,
    size: tuple[int, int] | None = None,
) -> int:
    """Encode ``frames`` to ``path`` and return how many were written.

    ``size`` is ``(width, height)``; when omitted it is taken from the first
    frame. The codec is chosen from the file extension.
    """
    if fps <= 0:
        raise VideoError(f"Output fps must be greater than zero, got {fps}")

    out_path = Path(path)
    if out_path.parent and not out_path.parent.exists():
        raise VideoError(f"Output directory does not exist: {out_path.parent}")

    fourcc = cv2.VideoWriter_fourcc(*_fourcc_for(out_path))
    writer: cv2.VideoWriter | None = None
    written = 0

    try:
        for frame in frames:
            if writer is None:
                height, width = frame.shape[:2]
                frame_size = size if size is not None else (width, height)
                writer = cv2.VideoWriter(str(out_path), fourcc, fps, frame_size)
                if not writer.isOpened():
                    # VideoWriter reports failure here rather than raising, and
                    # would otherwise leave behind an empty file.
                    raise VideoError(
                        f"Could not open a video writer for {out_path}. The codec "
                        f"for '{out_path.suffix}' may be unavailable."
                    )
            writer.write(frame)
            written += 1
    finally:
        if writer is not None:
            writer.release()

    if written == 0:
        raise VideoError(f"No frames to write to {out_path}")

    return written


def play(
    path: str | Path,
    *,
    speed: float = 1.0,
    headless: bool = False,
    display: Display | None = None,
) -> int:
    """Play ``path`` frame by frame and return how many frames were shown.

    When ``speed`` is above 1 a fast-forward badge is drawn in the top-right
    corner, so sped-up playback is obvious on screen. With ``headless=True``
    frames are decoded without a window and as fast as possible, so ``speed``
    and the badge have no effect. Playback stops early when the viewer presses
    ``q`` or Escape. An explicit ``display`` overrides ``headless``.
    """
    if speed <= 0:
        raise VideoError(f"Playback speed must be greater than zero, got {speed}")

    info = probe(path)
    delay_ms = frame_delay_ms(info.fps, speed)

    if display is not None:
        surface: Display = display
    elif headless:
        surface = NullDisplay()
    else:
        surface = WindowDisplay()

    badge_speed = speed > 1.0 and not headless

    shown = 0
    try:
        for frame in iter_frames(path):
            surface.show(draw_fast_forward(frame, speed) if badge_speed else frame)
            shown += 1
            if surface.wait(delay_ms) in QUIT_KEYS:
                break
    finally:
        surface.close()

    return shown
