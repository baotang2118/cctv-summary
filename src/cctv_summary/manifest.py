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

"""JSON event manifest: the summary as data rather than a printed table.

The timestamps a run finds are useful well beyond the clip it writes - feeding
an alerting script, an index, or another tool. This renders a `SummaryStats`
into a plain, stable JSON document so those consumers never have to parse the
human-readable report.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from cctv_summary import __version__
from cctv_summary.mask import describe
from cctv_summary.summarize import SummaryStats
from cctv_summary.video import VideoError

# Bumped only when the shape changes in a way that could break a consumer, so
# a script can refuse a manifest it does not understand.
MANIFEST_VERSION: int = 1

# Seconds are rounded before serialising: frame counts divided by a fractional
# fps produce long tails that are noise, not precision.
SECONDS_PRECISION: int = 3


def format_timestamp(seconds: float) -> str:
    """Render seconds as HH:MM:SS, the way footage is usually referenced.

    Shared with the CLI table so both renderings of a run agree.
    """
    whole = int(seconds)
    return f"{whole // 3600:02d}:{(whole % 3600) // 60:02d}:{whole % 60:02d}"


def build_manifest(
    stats: SummaryStats,
    *,
    source: str | Path,
    destination: str | Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Describe a summarization run as a JSON-serialisable document.

    Pure: it takes values and returns a dict, so the shape can be tested
    without touching the filesystem. ``destination`` is ``None`` for a dry run,
    where the events are still worth exporting even though no clip exists.
    """
    moment = generated_at if generated_at is not None else datetime.now(UTC)

    return {
        "manifest_version": MANIFEST_VERSION,
        "generator": "cctv-summary",
        "generator_version": __version__,
        "generated_at": moment.isoformat(),
        "source": {
            "path": str(source),
            "frames": stats.total,
            "fps": stats.fps,
            "duration_seconds": _seconds(stats.source_seconds),
        },
        "summary": {
            "path": None if destination is None else str(destination),
            "frames": stats.kept,
            "duration_seconds": _seconds(stats.summary_seconds),
            "kept_ratio": round(stats.kept_ratio, 6),
        },
        "settings": {
            "threshold": stats.threshold,
            "target_ratio": stats.target_ratio,
            "pad_seconds": stats.pad_seconds,
            "min_event_seconds": stats.min_event_seconds,
            "window": stats.window,
            "tolerance": stats.tolerance,
            "comparison_edge": stats.comparison_edge,
            "watch": describe(stats.watch),
            "ignore": describe(stats.ignore),
        },
        "events": [
            {
                "index": number,
                "start_frame": event.start,
                "end_frame": event.end,
                "frames": event.frames,
                "start_seconds": _seconds(event.start_seconds),
                "end_seconds": _seconds(event.end_seconds),
                "duration_seconds": _seconds(event.duration_seconds),
                "start_timestamp": format_timestamp(event.start_seconds),
                "end_timestamp": format_timestamp(event.end_seconds),
            }
            for number, event in enumerate(stats.events, start=1)
        ],
    }


def dump_manifest(manifest: dict[str, Any], stream: TextIO) -> None:
    """Write ``manifest`` to an open stream, newline-terminated."""
    json.dump(manifest, stream, indent=2)
    stream.write("\n")


def write_manifest(manifest: dict[str, Any], path: str | Path) -> None:
    """Write ``manifest`` to ``path`` as UTF-8 JSON."""
    try:
        with Path(path).open("w", encoding="utf-8") as stream:
            dump_manifest(manifest, stream)
    except OSError as exc:
        raise VideoError(f"Could not write manifest to {path}: {exc}") from exc


def _seconds(value: float) -> float:
    return round(value, SECONDS_PRECISION)
