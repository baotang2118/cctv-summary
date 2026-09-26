"""Command line interface for cctv-summary."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from cctv_summary import __version__
from cctv_summary.overlay import draw_triangle
from cctv_summary.summarize import (
    DEFAULT_THRESHOLD,
    DEFAULT_TOLERANCE,
    DEFAULT_WINDOW,
    summarize_video,
)
from cctv_summary.video import VideoError, play, probe


def _handle_info(args: argparse.Namespace) -> int:
    info = probe(args.video)
    print(f"path:        {info.path}")
    print(f"resolution:  {info.width}x{info.height}")
    print(f"fps:         {info.fps:.3f}")
    print(f"frames:      {info.frame_count}")
    print(f"duration:    {info.duration_seconds:.3f}s")
    return 0


def _handle_play(args: argparse.Namespace) -> int:
    overlay = draw_triangle if args.triangle else None
    shown = play(
        args.video,
        speed=args.speed,
        headless=args.headless,
        overlay=overlay,
    )
    verb = "Processed" if args.headless else "Displayed"
    print(f"{verb} {shown} frame(s) from {args.video}")
    return 0


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
        window=args.window,
        tolerance=args.tolerance,
    )

    print(f"frames in:   {stats.total}")
    print(f"frames kept: {stats.kept} ({stats.kept_ratio:.1%})")
    print(f"dropped:     {stats.dropped}")
    print(f"duration:    {stats.source_seconds:.2f}s -> {stats.summary_seconds:.2f}s")
    if destination is None:
        print("dry run: no file written")
    else:
        print(f"wrote:       {destination}")
    return 0


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
    play_parser.add_argument(
        "--triangle",
        action="store_true",
        help="Draw a triangle marker in the top-right corner of every frame.",
    )
    play_parser.set_defaults(handler=_handle_play)

    summarize_parser = subparsers.add_parser(
        "summarize",
        help="Write a shorter copy of a video, keeping only frames that changed.",
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
        help="Fraction of pixels that must move to keep a frame, 0-1 "
        "(default: %(default)s).",
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
    summarize_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be kept without writing a file.",
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
