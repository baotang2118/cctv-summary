"""Command line interface for cctv-summary."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from cctv_summary import __version__
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


def _timestamp(seconds: float) -> str:
    """Render seconds as HH:MM:SS, the way footage is usually referenced."""
    whole = int(seconds)
    return f"{whole // 3600:02d}:{(whole % 3600) // 60:02d}:{whole % 60:02d}"


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
        progress=_progress_for(sys.stderr),
    )

    print(f"source:   {stats.total} frames, {_timestamp(stats.source_seconds)}")
    if stats.target_ratio is not None:
        print(
            f"auto:     --threshold {stats.threshold:.4f}"
            f" for a {stats.target_ratio:.0%} target"
        )
    print(f"events:   {len(stats.events)}")

    for number, event in enumerate(stats.events, start=1):
        print(
            f"  {number:>3}. {_timestamp(event.start_seconds)}"
            f" - {_timestamp(event.end_seconds)}"
            f"  ({event.duration_seconds:.1f}s)"
        )

    print(
        f"summary:  {stats.kept} frames, {_timestamp(stats.summary_seconds)}"
        f" ({stats.kept_ratio:.1%} of source)"
    )

    _warn_if_target_missed(
        stats, pad_seconds=args.pad, min_event_seconds=args.min_event
    )

    if not stats.events:
        print("no motion found: try a lower --threshold")
    elif destination is None:
        print("dry run: no file written")
    else:
        print(f"wrote:    {destination}")
    return 0


def _warn_if_target_missed(
    stats: SummaryStats,
    *,
    pad_seconds: float,
    min_event_seconds: float,
) -> None:
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
        pad_seconds=pad_seconds,
        min_event_seconds=min_event_seconds,
    )
    if floor > stats.target_ratio:
        print(
            f"note:     one shortest event is already {floor:.0%} of this clip,"
            f" so {stats.target_ratio:.0%} is unreachable"
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
