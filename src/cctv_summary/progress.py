"""Progress reporting for work that takes long enough to look stuck.

Summarizing an hour of footage decodes it twice and prints nothing until it
finishes, which is indistinguishable from a hang. Reporters render to the
terminal; the pure logic in :mod:`cctv_summary.summarize` only ever calls the
protocol, so it stays testable and silent by default.
"""

from __future__ import annotations

import sys
import time
from typing import Protocol, TextIO

BAR_WIDTH: int = 20

# Redraw at most this often. Writing on every frame costs more than the work
# being measured on small clips.
REDRAW_SECONDS: float = 0.1

# Widest stage label, so the bar does not jump when the label changes.
LABEL_WIDTH: int = 9


def format_duration(seconds: float) -> str:
    """Compact duration for progress lines: ``45s``, ``2m05s``, ``1h02m``."""
    whole = max(int(seconds), 0)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{(whole % 3600) // 60:02d}m"


class Progress(Protocol):
    """Sink for progress updates, so callers need no terminal knowledge."""

    def start(self, label: str, total: int) -> None: ...

    def advance(self, amount: int = 1) -> None: ...

    def finish(self) -> None: ...


class NullProgress:
    """Discards updates, for quiet runs, redirected output, and tests."""

    def start(self, label: str, total: int) -> None:
        pass

    def advance(self, amount: int = 1) -> None:
        pass

    def finish(self) -> None:
        pass


class TerminalProgress:
    """Draws a single self-overwriting progress line.

    Writes to stderr by default so that redirecting stdout still captures a
    clean report.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        redraw_seconds: float = REDRAW_SECONDS,
    ) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.redraw_seconds = redraw_seconds
        self._label = ""
        self._total = 0
        self._current = 0
        self._started = 0.0
        self._last_drawn = 0.0
        self._line_width = 0

    def start(self, label: str, total: int) -> None:
        self._label = label
        self._total = max(total, 0)
        self._current = 0
        self._started = time.monotonic()
        # Draw immediately: the first frame can take a while to arrive, and
        # that is exactly when the run looks stuck.
        self._draw()

    def advance(self, amount: int = 1) -> None:
        self._current += amount
        if time.monotonic() - self._last_drawn >= self.redraw_seconds:
            self._draw()

    def finish(self) -> None:
        if self._line_width:
            self.stream.write("\r" + " " * self._line_width + "\r")
            self.stream.flush()
            self._line_width = 0

    def _draw(self) -> None:
        elapsed = time.monotonic() - self._started
        label = self._label[:LABEL_WIDTH].ljust(LABEL_WIDTH)

        if self._total > 0:
            # Containers often report an estimated frame count, so the real
            # count can overshoot. Clamp rather than show 1200/1120.
            done = min(self._current, self._total)
            fraction = done / self._total
            filled = round(fraction * BAR_WIDTH)
            bar = "#" * filled + "-" * (BAR_WIDTH - filled)
            line = (
                f"{label} [{bar}] {fraction:>4.0%}"
                f" {done}/{self._total}"
                f" {self._remaining(fraction, elapsed)}"
            )
        else:
            # Some containers report no frame count; show liveness instead.
            line = f"{label} {self._current} frames {format_duration(elapsed)}"

        padding = max(self._line_width - len(line), 0)
        self.stream.write("\r" + line + " " * padding)
        self.stream.flush()
        self._line_width = len(line)
        # Recorded here rather than in advance(), so every draw resets the
        # throttle and the opening draw is not immediately followed by another.
        self._last_drawn = time.monotonic()

    def _remaining(self, fraction: float, elapsed: float) -> str:
        if fraction <= 0:
            return "estimating"
        if fraction >= 1:
            return f"{format_duration(elapsed)} elapsed"
        return f"{format_duration(elapsed / fraction - elapsed)} left"
