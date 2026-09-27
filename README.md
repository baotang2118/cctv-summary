# cctv-summary

Summarization tooling for CCTV footage.

The project is scaffolded with a `src/` layout, managed by [uv](https://docs.astral.sh/uv/),
tested with `pytest`, and linted/formatted with `ruff`.

## Requirements

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

Playback opens a window via OpenCV's highgui, so `cctv-summary play` needs a desktop
session. `cctv-summary info` and `cctv-summary play --headless` work headless.

## Setup

```bash
uv sync
```

This creates `.venv/` and `uv.lock` with the runtime and dev dependencies.

## Run

Print container metadata for a clip:

```bash
uv run cctv-summary info path/to/clip.mp4
```

```
path:        path/to/clip.mp4
resolution:  1920x1080
fps:         25.000
frames:      750
duration:    30.000s
```

Play a clip in a window — press `q` or `Esc` to stop:

```bash
uv run cctv-summary play path/to/clip.mp4
uv run cctv-summary play path/to/clip.mp4 --speed 4
```

`--speed` is a multiplier on the file's own frame rate, so `4` plays four times faster.

Read a clip with no window at all — useful over SSH, in CI, or for timing a decode pass:

```bash
uv run cctv-summary play path/to/clip.mp4 --headless
```

Headless runs decode as fast as the file allows and ignore `--speed`.

## Summarize

Write a shorter copy that keeps only frames where something changed:

```bash
uv run cctv-summary summarize clip.mp4 summary.mp4
```

```
frames in:   1120
frames kept: 185 (16.5%)
dropped:     935
duration:    37.33s -> 6.17s
wrote:       summary.mp4
```

Check the keep/drop ratio without writing anything:

```bash
uv run cctv-summary summarize clip.mp4 --dry-run
```

A frame is dropped only when it looks unchanged against **both** the previous kept frame
and a rolling average of recent frames. The second check catches slow drift, where each
frame barely differs from the last but the scene has clearly moved.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--threshold` | `0.10` | Fraction of pixels that must move to keep a frame (0–1) |
| `--window` | `30` | Frames in the rolling background window |
| `--tolerance` | `25` | Per-pixel intensity change counting as movement (0–255) |

`--threshold` and `--tolerance` are different knobs: `tolerance` decides whether a *pixel*
moved, `threshold` decides whether *enough* pixels moved.

Good values depend heavily on the footage. A static camera watching an empty corridor
tolerates a high threshold; a handheld or moving camera needs a much lower one. Start
with `--dry-run` and tune. The output keeps the source frame rate, so it plays faster
than real time, and **audio is not preserved** — OpenCV does not carry audio.

```bash
uv run cctv-summary --help
uv run python -m cctv_summary
```

## Test

```bash
uv run pytest
```

## Lint and format

```bash
uv run ruff check .
uv run ruff format .
```

## Layout

```
src/cctv_summary/   package source (cli.py, video.py, summarize.py)
tests/              pytest suite
pyproject.toml      project metadata, dependencies, tool config
AGENTS.md           working notes for agents and contributors
```

## Status

Reading, playing, and frame-drop summarization work. There is no object or person
detection, scene segmentation, or text summary yet.

## Agent notes

[AGENTS.md](./AGENTS.md) holds the shared working notes for AI agents and new
contributors: toolchain rules, code conventions, testing patterns, and the
current project status. Read it before making changes, and keep it up to date.
