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

Play a clip in a window - press `q` or `Esc` to stop:

```bash
uv run cctv-summary play path/to/clip.mp4
uv run cctv-summary play path/to/clip.mp4 --speed 4
```

`--speed` is a multiplier on the file's own frame rate, so `4` plays four times faster.
Above `1` a fast-forward badge (`▶▶ 4x`) appears in the top-right corner, so sped-up
playback is never mistaken for real time.

Read a clip with no window at all - useful over SSH, in CI, or for timing a decode pass:

```bash
uv run cctv-summary play path/to/clip.mp4 --headless
```

Headless runs decode as fast as the file allows and ignore `--speed`, so no badge is
drawn.

## Summarize

Find the stretches where something moves and write them out as continuous clips,
dropping the dead time in between:

```bash
uv run cctv-summary summarize clip.mp4 summary.mp4
```

```
source:   1200 frames, 00:02:00
events:   3
    1. 00:00:18 - 00:00:30  (12.0s)
    2. 00:00:58 - 00:01:08  (10.0s)
    3. 00:01:28 - 00:01:42  (14.0s)
summary:  360 frames, 00:00:36 (30.0% of source)
wrote:    summary.mp4
```

The timestamps refer to the **original** recording, so the listing doubles as an index of
when things happened — useful when the source is an hour long.

See what would be kept without writing anything:

```bash
uv run cctv-summary summarize clip.mp4 --dry-run
```

Every frame of the output carries a scissors mark (`✂`) in the top-right corner, so a
summary is recognisable as one even after being renamed, copied, or shared. The mark is
burned into the pixels — there is no way to strip it from an existing summary, so keep
the original if you need unmarked footage. Playing a summary with `--speed` above 1
stacks the fast-forward badge beneath the scissors rather than on top of it.

### How it decides

Each frame is scored by how much of it moved, measured against a rolling average of
recent frames. An event opens when that score crosses `--threshold` and closes only after
motion has stayed low for about a second, so someone pausing mid-shot does not get split
into several events. Surviving events are padded by `--pad` seconds at each end and
merged where they overlap.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--threshold` | `0.01` | Fraction of pixels that must move to count as motion (0–1) |
| `--pad` | `2.0` | Seconds kept either side of an event |
| `--min-event` | `1.0` | Ignore anything shorter than this, in seconds |
| `--window` | `30` | Frames in the rolling background average |
| `--tolerance` | `25` | Per-pixel intensity change counting as movement (0–255) |

### Tuning

Start with `--dry-run` and adjust `--threshold` alone; it is by far the most sensitive.

**A distant person covers only about 1% of the frame.** On a 640x360 corridor clip a
walking figure scored `0.010`, peaking at `0.027`, while the empty corridor scored
`0.0000`. Thresholds in the tenths are an order of magnitude too high for CCTV and will
silently discard every event — at `0.05` that same clip returned a single frame.

- **Missing events?** Lower `--threshold` (try `0.005`), or lower `--tolerance` if people
  blend into the background.
- **Too many events?** Raise `--threshold`, or raise `--min-event` to ignore brief blips.
- **Events cut short, or one person split in two?** Raise `--pad`.
- **Grainy night footage triggering constantly?** Raise `--tolerance` to `40`+ so sensor
  noise stops counting as movement.

Motion here is raw pixel change, with no idea what a person is — rain, headlights and a
swaying branch all count. Expect to tune per camera rather than globally.

The source is decoded twice, once to measure motion and once to write, because padding
has to reach back before the moment motion was noticed. Expect roughly double the time of
a single pass. The output keeps the source frame rate, and **audio is not preserved** —
OpenCV does not carry audio.

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
src/cctv_summary/   package source (cli.py, video.py, overlay.py, summarize.py)
tests/              pytest suite
pyproject.toml      project metadata, dependencies, tool config
AGENTS.md           working notes for agents and contributors
```

## Status

Reading, playing, and motion-event summarization work. Motion is raw pixel change —
there is no object or person detection, scene segmentation, or text summary yet.

## Agent notes

[AGENTS.md](./AGENTS.md) holds the shared working notes for AI agents and new
contributors: toolchain rules, code conventions, testing patterns, and the
current project status. Read it before making changes, and keep it up to date.
