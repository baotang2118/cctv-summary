"""Command line interface for cctv-summary."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from cctv_summary import __version__


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
    parser.add_argument(
        "-n",
        "--name",
        default="world",
        help="Name to greet (placeholder until real functionality lands).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"Hello, {args.name}! cctv-summary {__version__} is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
