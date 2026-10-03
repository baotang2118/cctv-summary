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

"""Command line interface for cctv-summary."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from cctv_summary import __version__
from cctv_summary.diagnose import (
    DEFAULT_BLUR_DETAIL,
    DEFAULT_DARK_LUMINANCE,
    DEFAULT_FLAT_FRACTION,
    DEFAULT_FREEZE_SECONDS,
    DEFAULT_MIN_FAULT_SECONDS,
    DEFAULT_SHAKE_CHANGE,
    diagnose_video,
)
from cctv_summary.manifest import (
    build_manifest,
    dump_manifest,
    format_timestamp,
    write_manifest,
)
from cctv_summary.mask import Region, parse_region
from cctv_summary.progress import NullProgress, Progress, TerminalProgress
from cctv_summary.summarize import (
    COMPARISON_EDGE_TIERS,
    DEFAULT_MIN_EVENT_SECONDS,
    DEFAULT_PAD_SECONDS,
    DEFAULT_THRESHOLD,
    DEFAULT_TOLERANCE,
    DEFAULT_WINDOW,
    MAX_COMPARISON_EDGE,
    SummaryStats,
    smallest_possible_ratio,
    summarize_video,
)
from cctv_summary.video import VideoError, play, probe

# How far past the target the result may land before it is worth explaining.
TARGET_OVERSHOOT = 1.5

# --manifest value that means "write the JSON to stdout instead of a file".
STDOUT_TARGET = Path("-")

# `check` reports faults by exiting non-zero, so a cron job needs no parsing.
# Errors return the same code: both mean "this camera needs attention".
FAULTS_FOUND = 1


def _handle_info(args: argparse.Namespace) -> int:
    info = probe(args.video)
    print(f"path:        {info.path}")
    print(f"resolution:  {info.width}x{info.height}")
    print(f"fps:         {info.fps:.3f}")
    print(f"frames:      {info.frame_count}")
    print(f"duration:    {info.duration_seconds:.3f}s")
    return 0


def _handle_play(args: argparse.Namespace) -> int:
    shown = play(
        args.video,
        speed=args.speed,
        headless=args.headless,
        # A window is its own sign of life; a headless run shows nothing.
        progress=_progress_for(sys.stderr) if args.headless else None,
    )
    verb = "Processed" if args.headless else "Displayed"
    print(f"{verb} {shown} frame(s) from {args.video}")
    return 0


def _handle_check(args: argparse.Namespace) -> int:
    diagnosis = diagnose_video(
        args.video,
        dark_luminance=args.dark,
        blur_detail=args.blur,
        flat_share=args.obstruction,
        shake_change=args.shake,
        min_fault_seconds=args.min_fault,
        freeze_seconds=args.freeze,
        comparison_edge=args.comparison_edge,
        progress=_progress_for(sys.stderr),
    )

    print(
        f"source:   {diagnosis.total} frames,"
        f" {format_timestamp(diagnosis.source_seconds)}"
    )
    # Healthy medians say how much headroom the thresholds have, which is what
    # a borderline camera needs before anything has actually tripped.
    print(
        f"typical:  luminance {diagnosis.luminance_median:.1f},"
        f" detail {diagnosis.detail_median:.1f}"
    )
    print(f"faults:   {len(diagnosis.faults)}")

    for number, fault in enumerate(diagnosis.faults, start=1):
        print(
            f"  {number:>3}. {fault.kind:<11}"
            f" {format_timestamp(fault.start_seconds)}"
            f" - {format_timestamp(fault.end_seconds)}"
            f"  ({fault.duration_seconds:.1f}s)"
            f"  {fault.metric} {fault.value:.3g}"
        )

    if diagnosis.healthy:
        print("ok:       no camera faults found")
        return 0

    share = diagnosis.faulty_frames / diagnosis.total if diagnosis.total else 0.0
    plural = "" if diagnosis.faulty_frames == 1 else "s"
    print(f"affected: {diagnosis.faulty_frames} frame{plural} ({share:.1%} of source)")
    return FAULTS_FOUND


def _progress_for(stream: TextIO) -> Progress:
    """Report progress only to a real terminal.

    Redirected or piped output would otherwise fill with carriage returns.
    """
    if hasattr(stream, "isatty") and stream.isatty():
        return TerminalProgress(stream)
    return NullProgress()


def _comparison_edge(value: str) -> int | None:
    """Parse --comparison-edge: ``auto`` follows the source resolution."""
    if value.strip().lower() == "auto":
        return None
    try:
        edge = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a pixel count or 'auto', got {value!r}"
        ) from None
    if edge < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1 pixel, got {edge}")
    return edge


def _region(value: str) -> Region:
    """Parse a --watch/--ignore rectangle given as fractions of the frame."""
    try:
        return parse_region(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _keep_ratio(value: str) -> float:
    """Parse --target, accepting either ``10%`` or ``0.1``."""
    text = value.strip()
    try:
        number = float(text.removesuffix("%"))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a share such as '10%' or '0.1', got {value!r}"
        ) from None

    ratio = number / 100 if text.endswith("%") else number
    if not 0.0 < ratio <= 1.0:
        raise argparse.ArgumentTypeError(
            f"must be above 0% and at most 100%, got {value!r}"
        )
    return ratio


def _handle_summarize(args: argparse.Namespace) -> int:
    if args.destination is None and not args.dry_run:
        print(
            "error: an output path is required unless --dry-run is given",
            file=sys.stderr,
        )
        return 1

    destination = None if args.dry_run else args.destination
    stats = summarize_video(
        args.source,
        destination,
        threshold=args.threshold,
        target_ratio=args.target,
        window=args.window,
        tolerance=args.tolerance,
        pad_seconds=args.pad,
        min_event_seconds=args.min_event,
        comparison_edge=args.comparison_edge,
        watch=tuple(args.watch or ()),
        ignore=tuple(args.ignore or ()),
        progress=_progress_for(sys.stderr),
    )

    # JSON on stdout has to be the only thing there, or piping it into a parser
    # fails; the human report steps aside to stderr in that case.
    to_stdout = args.manifest == STDOUT_TARGET
    report = sys.stderr if to_stdout else sys.stdout

    print(
        f"source:   {stats.total} frames, {format_timestamp(stats.source_seconds)}",
        file=report,
    )
    if stats.watch or stats.ignore:
        # A mask silently changes what counts as motion, so say it ran rather
        # than leaving a surprising event count unexplained.
        parts = []
        if stats.watch:
            parts.append(f"{len(stats.watch)} watched")
        if stats.ignore:
            parts.append(f"{len(stats.ignore)} ignored")
        print(f"mask:     {', '.join(parts)}", file=report)
    if stats.target_ratio is not None:
        print(
            f"auto:     --threshold {stats.threshold:.4f}"
            f" for a {stats.target_ratio:.0%} target",
            file=report,
        )
    print(f"events:   {len(stats.events)}", file=report)

    for number, event in enumerate(stats.events, start=1):
        print(
            f"  {number:>3}. {format_timestamp(event.start_seconds)}"
            f" - {format_timestamp(event.end_seconds)}"
            f"  ({event.duration_seconds:.1f}s)",
            file=report,
        )

    print(
        f"summary:  {stats.kept} frames, {format_timestamp(stats.summary_seconds)}"
        f" ({stats.kept_ratio:.1%} of source)",
        file=report,
    )

    _warn_if_target_missed(stats, stream=report)

    if not stats.events:
        print("no motion found: try a lower --threshold", file=report)
    elif destination is None:
        print("dry run: no file written", file=report)
    else:
        print(f"wrote:    {destination}", file=report)

    if args.manifest is not None:
        manifest = build_manifest(stats, source=args.source, destination=destination)
        if to_stdout:
            dump_manifest(manifest, sys.stdout)
        else:
            write_manifest(manifest, args.manifest)
            print(f"manifest: {args.manifest}", file=report)
    return 0


def _warn_if_target_missed(stats: SummaryStats, *, stream: TextIO) -> None:
    """Explain an overshoot rather than quietly returning the wrong amount.

    Padding and the minimum event length quantise what is reachable, so a small
    target is simply impossible on a short clip.
    """
    if stats.target_ratio is None or not stats.events:
        return
    if stats.kept_ratio <= stats.target_ratio * TARGET_OVERSHOOT:
        return

    floor = smallest_possible_ratio(
        stats.total,
        fps=stats.fps,
        pad_seconds=stats.pad_seconds,
        min_event_seconds=stats.min_event_seconds,
    )
    if floor > stats.target_ratio:
        print(
            f"note:     one shortest event is already {floor:.0%} of this clip,"
            f" so {stats.target_ratio:.0%} is unreachable",
            file=stream,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cctv-summary",
        description="Summarization tooling for CCTV footage.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    info_parser = subparsers.add_parser(
        "info",
        help="Print container metadata for a video file.",
    )
    info_parser.add_argument("video", type=Path, help="Path to the video file.")
    info_parser.set_defaults(handler=_handle_info)

    play_parser = subparsers.add_parser(
        "play",
        help="Play a video file in a window (press q or Esc to stop).",
    )
    play_parser.add_argument("video", type=Path, help="Path to the video file.")
    play_parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Playback speed multiplier; 2.0 is twice as fast (default: %(default)s).",
    )
    play_parser.add_argument(
        "--headless",
        action="store_true",
        help="Read frames in the background without opening a window, as fast as "
        "possible. --speed is ignored in this mode.",
    )
    play_parser.set_defaults(handler=_handle_play)

    check_parser = subparsers.add_parser(
        "check",
        help="Look for camera faults: a dark, blurred, covered, frozen, or "
        "knocked camera. Exits non-zero when any are found.",
    )
    check_parser.add_argument("video", type=Path, help="Video file to check.")
    check_parser.add_argument(
        "--dark",
        type=float,
        default=DEFAULT_DARK_LUMINANCE,
        metavar="LEVEL",
        help="Mean grey level below which the picture is too dark to use, "
        "0-255 (default: %(default)s).",
    )
    check_parser.add_argument(
        "--blur",
        type=float,
        default=DEFAULT_BLUR_DETAIL,
        metavar="DETAIL",
        help="Laplacian variance below which the image is out of focus "
        "(default: %(default)s).",
    )
    check_parser.add_argument(
        "--obstruction",
        type=float,
        default=DEFAULT_FLAT_FRACTION,
        metavar="SHARE",
        help="Share of the frame with no local contrast that reads as a "
        "covered lens, 0-1 (default: %(default)s).",
    )
    check_parser.add_argument(
        "--shake",
        type=float,
        default=DEFAULT_SHAKE_CHANGE,
        metavar="SHARE",
        help="Share of pixels changing at once that means the camera moved "
        "rather than the scene, 0-1 (default: %(default)s).",
    )
    check_parser.add_argument(
        "--min-fault",
        type=float,
        default=DEFAULT_MIN_FAULT_SECONDS,
        metavar="SECONDS",
        help="Ignore faults shorter than this (default: %(default)s).",
    )
    check_parser.add_argument(
        "--freeze",
        type=float,
        default=DEFAULT_FREEZE_SECONDS,
        metavar="SECONDS",
        help="Seconds of a byte-identical picture before the feed counts as "
        "frozen (default: %(default)s).",
    )
    check_parser.add_argument(
        "--comparison-edge",
        type=_comparison_edge,
        default=None,
        metavar="PIXELS",
        help="Longest edge the checks measure at, or 'auto' to follow the "
        "source resolution (default: auto).",
    )
    check_parser.set_defaults(handler=_handle_check)

    summarize_parser = subparsers.add_parser(
        "summarize",
        help="Write a copy of a video containing only its motion events.",
    )
    summarize_parser.add_argument("source", type=Path, help="Video file to summarize.")
    summarize_parser.add_argument(
        "destination",
        type=Path,
        nargs="?",
        help="Where to write the summary (omit only with --dry-run).",
    )
    summarize_parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="Fraction of pixels that must move to count as motion, 0-1. A "
        "distant person covers only about 1%% of the frame (default: %(default)s).",
    )
    summarize_parser.add_argument(
        "--target",
        type=_keep_ratio,
        default=None,
        metavar="SHARE",
        help="Tune --threshold automatically to keep about this much of the "
        "clip, given as 10%% or 0.1. Overrides --threshold.",
    )
    summarize_parser.add_argument(
        "--pad",
        type=float,
        default=DEFAULT_PAD_SECONDS,
        help="Seconds kept either side of an event (default: %(default)s).",
    )
    summarize_parser.add_argument(
        "--min-event",
        type=float,
        default=DEFAULT_MIN_EVENT_SECONDS,
        help="Ignore events shorter than this many seconds (default: %(default)s).",
    )
    summarize_parser.add_argument(
        "--window",
        type=int,
        default=DEFAULT_WINDOW,
        help="Frames in the rolling background window (default: %(default)s).",
    )
    summarize_parser.add_argument(
        "--tolerance",
        type=int,
        default=DEFAULT_TOLERANCE,
        help="Per-pixel intensity change that counts as movement, 0-255 "
        "(default: %(default)s).",
    )
    tiers = ", ".join(f"up to {limit}p {edge}" for limit, edge in COMPARISON_EDGE_TIERS)
    summarize_parser.add_argument(
        "--comparison-edge",
        type=_comparison_edge,
        default=None,
        metavar="PIXELS",
        help="Longest edge motion is measured at, or 'auto' to step it up with "
        f"the source resolution ({tiers}, above that {MAX_COMPARISON_EDGE}) "
        "(default: auto).",
    )
    summarize_parser.add_argument(
        "--watch",
        type=_region,
        action="append",
        default=None,
        metavar="X,Y,W,H",
        help="Only count motion inside this rectangle, as fractions of the "
        "frame (e.g. 0,0.5,1,0.5 for the bottom half). Repeatable.",
    )
    summarize_parser.add_argument(
        "--ignore",
        type=_region,
        action="append",
        default=None,
        metavar="X,Y,W,H",
        help="Ignore motion inside this rectangle, as fractions of the frame "
        "(e.g. a road or a burned-in clock). Repeatable, and applied after "
        "--watch.",
    )
    summarize_parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        metavar="PATH",
        help="Also write the events as a JSON manifest; '-' sends it to "
        "stdout and moves the readable report to stderr.",
    )
    summarize_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the events found without writing a file.",
    )
    summarize_parser.set_defaults(handler=_handle_summarize)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1

    try:
        return handler(args)
    except VideoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
