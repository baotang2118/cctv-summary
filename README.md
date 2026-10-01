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
drawn. They report progress instead, since nothing else shows the run is alive:

```
decoding  [###########---------]  56% 626/1120 1s left
```

## Record a LAN camera

On Linux, [`scripts/record-camera.sh`](./scripts/record-camera.sh) records a 20-minute
MPEG-TS clip from an RTSP or other VLC-compatible camera URL. It requires `flock`,
`timeout`, and `cvlc`. The lock is blocking, so an overlapping cron invocation waits for
the active recording to finish instead of starting a second VLC process.

Run it every 20 minutes with:

```cron
*/20 * * * * /usr/bin/env bash /path/to/cctv-summary/scripts/record-camera.sh 'rtsp://camera/stream' '/path/to/recordings'
```

The output directory defaults to `$HOME/cctv-recordings`. `CAMERA_URL`, `OUTPUT_DIR`,
and `LOCK_FILE` can also be supplied as environment variables. At the time limit,
`timeout` first sends `SIGINT` so VLC can close the output cleanly, then forces it to
stop after a 30-second grace period if necessary.

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
when things happened - useful when the source is an hour long.

See what would be kept without writing anything:

```bash
uv run cctv-summary summarize clip.mp4 --dry-run
```

### Progress

Long recordings take a while, so both passes report progress on stderr:

```
analysing [#########-----------]  45% 505/1120 5s left
writing   [##############------]  68% 569/834 2s left
```

The line rewrites itself in place and disappears when the run finishes. Because it goes
to stderr, redirecting the report still gives you a clean file:

```bash
uv run cctv-summary summarize clip.mp4 summary.mp4 > events.txt
```

Progress is drawn only when stderr is a terminal, so pipes, logs, and CI output are not
filled with carriage returns. Redirect stderr to silence it entirely. `play --headless`
reports the same way; windowed playback does not, since the window already shows it is
running.

Every frame of the output carries a scissors mark (`✂`) in the top-right corner, so a
summary is recognisable as one even after being renamed, copied, or shared. The mark is
burned into the pixels - there is no way to strip it from an existing summary, so keep
the original if you need unmarked footage. Playing a summary with `--speed` above 1
stacks the fast-forward badge beneath the scissors rather than on top of it.

### How it works

Summarizing CCTV is mostly a problem of *not* losing the few seconds that matter. The
technique has three stages, and the guiding principle is **eager to start, reluctant to
stop**.

**1. Score each frame against the recent background, not the frame before it.**

This is the part that makes everything else work. Comparing consecutive frames measures
how *fast* something is moving, so a person walking slowly barely differs from the frame
before and disappears. Instead each frame is compared against a rolling average of the
last `--window` frames - effectively "what this scene looks like when nothing is
happening". A person is then different from an empty corridor for as long as they are in
shot, however slowly they move.

The score is simply the fraction of pixels that changed, from `0.0` to `1.0`.

**2. Group the score into events, using two thresholds rather than one.**

An event *opens* when the score reaches `--threshold`, but only *closes* once the score
has dropped to half that **and stayed there for about a second**. A single threshold
would flicker on and off, chopping one person into several events every time they slowed
down or paused.

**3. Pad each event, then merge any that now overlap.**

`--pad` seconds are added to both ends, so people are seen walking in rather than
appearing mid-stride. Two events close together become one continuous clip instead of two
with a jarring cut between them.

Here is one person walking through, pausing halfway, alongside a 2-frame flicker of
camera noise - and what each stage keeps (`#` = motion detected, `K` = frame kept):

```
raw >= threshold  ..............#####...####............##............
                                      ^pause            ^noise
1. threshold only ..............KKKKKK..KKKKK...........KK............  3 events
2. + cooldown     ..............KKKKKKKKKKKKK...........KK............  2 events
3. + --min-event  ..............KKKKKKKKKKKKK.........................  1 event
4. + --pad        KKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKK.....  1 event
```

Row 1 is what a naive threshold gives you: the walker is torn into three fragments and
the noise is kept. Each stage then fixes one failure - the cooldown bridges the pause,
`--min-event` discards the flicker, and `--pad` restores the lead-in and lead-out.

Note that `--min-event` is applied *before* `--pad`. That ordering matters: padding a
2-frame noise blip by 2 seconds either side would otherwise manufacture a 4-second
"event" out of nothing.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--threshold` | `0.01` | Fraction of pixels that must move to count as motion (0–1) |
| `--pad` | `2.0` | Seconds kept either side of an event |
| `--min-event` | `1.0` | Ignore anything shorter than this, in seconds |
| `--window` | `30` | Frames in the rolling background average |
| `--tolerance` | `25` | Per-pixel intensity change counting as movement (0–255) |

`--threshold` and `--tolerance` are easy to confuse. `--tolerance` decides whether a
single *pixel* changed enough to count as movement; `--threshold` decides whether *enough
pixels* moved for the frame to count as motion.

### Tuning

Start with `--dry-run` and adjust `--threshold` alone; it is by far the most sensitive.

**A distant person covers only about 1% of the frame.** On a 640x360 corridor clip a
walking figure scored `0.010`, peaking at `0.027`, while the empty corridor scored
`0.0000`. Thresholds in the tenths are an order of magnitude too high for CCTV and will
silently discard every event - at `0.05` that same clip returned a single frame.

- **Missing events?** Lower `--threshold` (try `0.005`), or lower `--tolerance` if people
  blend into the background.
- **Too many events?** Raise `--threshold`, or raise `--min-event` to ignore brief blips.
- **Events cut short, or one person split in two?** Raise `--pad`.
- **Grainy night footage triggering constantly?** Raise `--tolerance` to `40`+ so sensor
  noise stops counting as movement.
- **Someone stands still for a long time?** They gradually blend into the rolling
  background and the event closes. With the default `--window 30` (3 seconds of memory at
  10fps) a motionless figure stops registering after about 2.4 seconds; at `--window 120`
  they kept registering indefinitely in the same test. Raise `--window` so the background
  takes longer to absorb them - it costs no extra time, however large you make it.

Motion here is raw pixel change, with no idea what a person is - rain, headlights and a
swaying branch all count. Expect to tune per camera rather than globally.

The source is decoded twice, once to measure motion and once to write, because padding
has to reach back before the moment motion was noticed. Expect roughly double the time of
a single pass. The output keeps the source frame rate, and **audio is not preserved** -
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
scripts/            Linux camera recording and cron helpers
tests/              pytest suite
pyproject.toml      project metadata, dependencies, tool config
AGENTS.md           working notes for agents and contributors
```

## Ideas

These are possible directions, not committed features. Roughly in priority order:

1. Export a JSON or CSV event manifest for scripts and integrations.
2. Generate an HTML contact sheet with one thumbnail and the original timestamp for
   each event.
3. Support regions of interest and exclusion masks to ignore roads, trees, timestamps,
   and other noisy parts of a camera view.
4. Optionally write each event as a separate, timestamped clip.
5. Burn the original source timestamp into summarized frames.
6. Recommend a motion threshold by sampling the video's quiet baseline.
7. Generate motion heatmaps that show frequently active parts of the scene.
8. Detect camera problems such as obstruction, sudden movement, frozen frames, severe
   blur, or an unexpectedly dark image.

Potential performance work includes walking the ordered event list with a cursor rather
than checking every event against every decoded frame.

## Status

Reading, playing, and motion-event summarization work. Motion is raw pixel change -
there is no object or person detection, scene segmentation, or text summary yet.

## License

Copyright (C) 2026 Bao.TangDuc.

This project is licensed under the [GNU General Public License, version 3
only](./LICENSE). Commercial use is permitted under the GPL; when distributing
covered modified versions, you must follow its source-sharing terms. Private
internal use does not require publishing modifications.

## Agent notes

[AGENTS.md](./AGENTS.md) holds the shared working notes for AI agents and new
contributors: toolchain rules, code conventions, testing patterns, and the
current project status. Read it before making changes, and keep it up to date.
