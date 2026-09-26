"""Command line interface for cctv-summary."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from cctv_summary import __version__
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
    shown = play(args.video, speed=args.speed)
    print(f"Displayed {shown} frame(s) from {args.video}")
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
    play_parser.set_defaults(handler=_handle_play)

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
