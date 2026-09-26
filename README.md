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

Draw a marker on every frame:

```bash
uv run cctv-summary play path/to/clip.mp4 --triangle
```

`--triangle` overlays a red triangle in the top-right corner, scaled to the frame size.

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
src/cctv_summary/   package source (cli.py, video.py, overlay.py)
tests/              pytest suite
pyproject.toml      project metadata, dependencies, tool config
AGENTS.md           working notes for agents and contributors
```

## Status

Reading, playing, and annotating video with OpenCV works. Summarization itself is not
implemented yet — no detection, tracking, or event extraction. `video.probe()`,
`video.iter_frames()`, and the `play(overlay=...)` hook are the intended building blocks
for it.

## Agent notes

[AGENTS.md](./AGENTS.md) holds the shared working notes for AI agents and new
contributors: toolchain rules, code conventions, testing patterns, and the
current project status. Read it before making changes, and keep it up to date.
