# AGENTS.md

Working notes for AI agents (and humans) picking up this repository. Keep this file
short, factual, and current: it is shared memory, not documentation. Update it when a
convention, command, or the project status actually changes.

## Rules

Non-negotiable. Apply these to **every** code change, however small.

1. **Lint and format with the tool, never by hand.** Formatting and import order are
   owned by `ruff`. Do not hand-align, hand-sort imports, or argue with its output —
   run the tool and accept the result.
2. **Lint and format immediately after generating or editing code.** Before reporting
   work as finished, run, in this order, and get a clean result from each:

   ```bash
   uv run ruff format .   # format first
   uv run ruff check .    # then lint (use --fix for autofixable findings)
   uv run pytest          # then confirm nothing broke
   ```

   A change is not done while any of these fails. Fix the cause; do not silence rules
   with `# noqa` or loosen `pyproject.toml` to make an error disappear.
3. **Evaluate and update this memory after generating code.** When a change lands, ask
   whether it invalidated anything written here — status, commands, layout, conventions,
   dependencies, testing patterns, roadmap — and edit `AGENTS.md` in the *same* change.
   Record new pitfalls in Troubleshooting. Deciding no update is needed is a valid
   outcome, but the check itself is mandatory. Keep edits terse and delete anything that
   has gone stale.

## Project

`cctv-summary` is intended to become summarization tooling for CCTV footage.

**Status: video I/O, motion-event summarization, and camera fault checks work.** The app
reads and plays video
with OpenCV. `cctv-summary info` prints container metadata; `cctv-summary play` shows a
file in a window (badging the corner when `--speed` is above 1) or decodes headless
(`--headless`, which reports progress since nothing else shows it is alive);
`cctv-summary check` looks for a dark, blurred, covered, frozen, or knocked camera and
exits non-zero when it finds one;
`cctv-summary summarize` finds the stretches where something moves and
writes them out as continuous clips, reporting progress as it goes, listing their
timestamps, burning a scissors mark into every output frame, optionally restricting where
motion counts (`--watch` / `--ignore`), and optionally exporting the
events as a JSON manifest (`--manifest`). Motion is raw pixel
change — there is no object or person detection, so a swaying tree counts as an event.

## Toolchain

The project is managed by [uv](https://docs.astral.sh/uv/). Build backend is `hatchling`,
tests run on `pytest`, linting and formatting are `ruff`.

**Always run project commands through `uv run`.** Do not call bare `python`, `pip`, or
`pytest` — they resolve outside the managed environment and will not see the package.

```bash
uv sync                        # create/refresh .venv from pyproject.toml + uv.lock
uv run pytest                  # test suite
uv run ruff check .            # lint
uv run ruff format .           # format
uv run cctv-summary --help     # console script entry point
uv run cctv-summary info FILE  # container metadata
uv run cctv-summary play FILE  # playback window (q or Esc to quit)
uv run cctv-summary play FILE --speed 4    # faster, badges the top-right corner
uv run cctv-summary play FILE --headless   # no window, progress on stderr
uv run cctv-summary check FILE             # camera faults; non-zero exit if any found
uv run cctv-summary check FILE --dark 30   # per-camera thresholds
uv run cctv-summary summarize SRC DST      # motion events only, progress on stderr
uv run cctv-summary summarize SRC --dry-run  # report keep/drop without writing
uv run cctv-summary summarize SRC --target 10%  # solve --threshold for a keep share
uv run cctv-summary summarize SRC DST --comparison-edge 640  # override the resolution tier
uv run cctv-summary summarize SRC DST --manifest events.json  # JSON event manifest
uv run cctv-summary summarize SRC --dry-run --manifest -      # manifest on stdout
uv run cctv-summary summarize SRC --dry-run --ignore 0.55,0,0.45,0.18  # skip a noisy corner
uv run cctv-summary summarize SRC --dry-run --watch 0,0.4,1,0.35       # score only a band
uv run python -m cctv_summary  # module entry point
```

`requires-python` is `>=3.11`; `.python-version` currently pins `3.14` for local work.
Code must stay compatible with 3.11 — do not use syntax or stdlib APIs newer than that.

## Layout

```
src/cctv_summary/__init__.py   package docstring + __version__
src/cctv_summary/__main__.py   enables `python -m cctv_summary`
src/cctv_summary/cli.py        build_parser() + main(argv) -> int, subcommand handlers
src/cctv_summary/video.py      OpenCV layer: probe/iter_frames/play, VideoError, Display
src/cctv_summary/overlay.py    corner badges (draw_fast_forward, draw_summarized)
src/cctv_summary/summarize.py  motion scoring, event detection, threshold solver
src/cctv_summary/mask.py       Region, build_mask: where motion is allowed to count
src/cctv_summary/diagnose.py   camera faults: dark/blurry/obstructed/frozen/shaken
src/cctv_summary/manifest.py   JSON event manifest (build/dump/write, format_timestamp)
src/cctv_summary/progress.py   Progress protocol, TerminalProgress, NullProgress
scripts/record-camera.sh       Linux cron helper: locked 20-minute VLC recordings
tests/conftest.py              sample_video fixture (synthesises a small MJPG clip)
tests/                         pytest suite (test_cli/_video/_overlay/_summarize/_progress/_manifest/_mask/_diagnose.py)
pyproject.toml                 single source of truth for metadata, deps, ruff, pytest
LICENSE                        GNU GPL version 3 (GPL-3.0-only)
uv.lock                        committed lockfile — regenerate with uv, never hand-edit
```

This is a **src layout**: the package is only importable via the installed environment,
so imports are always absolute (`from cctv_summary.cli import main`), never relative to
the repo root.

## How summarization works

`summarize.py` keeps **motion events** and drops the dead time between them. It does not
select frames individually: an earlier frame-dropping design produced scattered stills
that were unwatchable and lost any sense of when things happened.

Three stages:

1. **Score** every frame — `motion_scores()` gives `moved_pixels / total_pixels`
   (0.0–1.0) from an absdiff against the **rolling background** (mean of the last
   `window` frames), with each pixel thresholded by `tolerance`.
2. **Group** the signal into events — `detect_events()`, described below.
3. **Write** the kept ranges, badged with the scissors mark.

### Why events survive intact

The guiding principle is **eager to start, reluctant to stop**. Four mechanisms combine,
and each one exists to fix a specific failure:

| # | Mechanism | Without it |
| --- | --- | --- |
| 1 | Score against the **rolling background**, not the previous frame | A slow walker barely differs from the frame before and vanishes. Frame differencing measures *speed*; background differencing measures *presence*. |
| 2 | **Hysteresis** — open at `threshold`, close at `threshold * STOP_RATIO` | A single threshold flickers on and off around the boundary. |
| 3 | **Cooldown** — motion must stay low for `cooldown_seconds` to close | A walker pausing mid-shot splits into several events. |
| 4 | **Padding** `pad_seconds` either side, then merge overlaps | People appear mid-stride with no lead-in, and two arrivals seconds apart become separate clips with a jarring cut. |

Traced on one walker who pauses mid-shot, plus a 2-frame noise blip:

```
raw >= threshold  ..............#####...####............##............
                                      ^pause            ^blip
1. threshold only ..............KKKKKK..KKKKK...........KK............  3 events -- walker split
2. + cooldown     ..............KKKKKKKKKKKKK...........KK............  2 events -- pause bridged
3. + min-event    ..............KKKKKKKKKKKKK.........................  1 event  -- blip rejected
4. + padding      KKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKK.....  1 event  -- lead-in/out
```

Two details in `detect_events()` are load-bearing and easy to break:

- **Order is filter → pad → merge.** Dropping events shorter than `min_event_seconds`
  happens *before* padding. Reverse it and `--pad 3` inflates a 2-frame noise blip into a
  6-second "event" that then passes the length filter. `test_padding_cannot_rescue_a_blip`
  pins this.
- **The close point rewinds** to `index - quiet + 1`, so the silent frames that proved the
  event was over are not themselves counted as part of it.

**The source is decoded twice.** Padding reaches backwards from the moment motion is
noticed, and buffering an hour of frames to look behind is not viable, so pass one scores
and pass two writes. Do not "optimise" this into a single pass without solving the
look-behind problem.

Output is written at the source fps, so it is shorter but no longer wall-clock accurate,
and OpenCV carries no audio. `SummaryStats.events` carries the timings, which the CLI
prints as an `HH:MM:SS` table and `manifest.py` exports as JSON — for a 60-minute
recording, knowing *when* something happened matters as much as the clip itself.
`SummaryStats` also records the knobs the run used (including the *resolved*
`comparison_edge`, never the `auto` that asked for it), so a report or manifest can
describe a result without the caller re-threading arguments.

**`tolerance` and `threshold` are different knobs.** `tolerance` (0–255) is "did *this
pixel* move"; `threshold` (0–1) is "did *enough pixels* move". Do not conflate them.

**Masks answer localised noise; the global knobs cannot.** A road, a tree, or a burned-in
clock changes forever, and raising `threshold`/`tolerance` enough to silence one also
discards the distant figure that matters. `mask.py` narrows *where* motion counts:
`Region` is a rectangle in **fractions** of the frame (so one description survives a
resolution change), `--watch` allow-lists and `--ignore` deny-lists, and ignores are
subtracted from watches. A mask covering everything raises rather than scoring `NaN`.

Three things there are load-bearing:

- **The denominator is the masked area, not the frame.** `change_score()` divides by the
  active pixel count. Dividing by `delta.size` instead would scale every score down by
  whatever was excluded, silently invalidating the calibrated `DEFAULT_THRESHOLD` the
  moment a region is added. `test_the_score_is_a_share_of_the_masked_area_not_the_frame`
  pins this.
- **The mask is built once, at the comparison size.** `comparison_size()` is factored out
  of `downscale_to_gray()` so both agree pixel for pixel; `summarize_video()` resolves it
  alongside the edge. Rescaling a mask per frame would be both slower and a chance to
  drift out of alignment.
- **The rolling background still sees whole frames.** Masking at count time is equivalent
  and keeps `RollingBackground`'s exact running total untouched. Do not mask the frames
  going into it.

Measured on a 320x240 clip with a ticking clock overlay and one walker: the clock put a
constant `0.00216` floor on every idle frame while never clearing `0.01` by itself, so it
looked harmless at the default threshold. At `--threshold 0.002` it kept **100%** of the
clip; ignoring its corner dropped idle frames to exactly `0.0`, left the walker at
`0.02342` (from `0.02384`), and recovered the real 7-second event at 35%. **The mask
changes the decision, not the pixels** — output stays untouched source footage.

**The comparison size is tiered, not a constant.** `downscale_to_gray()` caps the long
edge before diffing, and `comparison_edge_for(width, height)` picks that cap from the
source's short edge: ≤720p→256, ≤1080p→320, ≤1440p→480, above→640
(`COMPARISON_EDGE_TIERS` / `MAX_COMPARISON_EDGE`). `summarize_video()` resolves the edge
**once from `probe()`** and passes it down, so every score in a run is measured at the
same scale; `--comparison-edge` (`auto` or a pixel count) overrides it. Only the motion
decision is downscaled — decode and output stay at source resolution.

**Why tiers, measured.** Scores are a *share* of pixels, so shrinking the frame barely
moves them — on a 4K clip a 40x90px figure scored 0.00046 at 256 and 0.00045 at 640. The
tier does not raise the score, it decides whether the figure registers **at all**: area
averaging dilutes a small target's contrast until nothing clears `tolerance`. A 10x22px
figure 30 levels above its background scored exactly `0.0` at 256 and 320, and registered
at 480 and 640. So do not "tune" the tiers by looking at score magnitude; look for scores
collapsing to zero.

**Calibration, measured not guessed.** On a synthetic 640x360 corridor clip a walking
person scores **~0.01** (1% of pixels) and peaks at 0.027; idle frames score ~0.0000.
That is why `DEFAULT_THRESHOLD` is `0.01` and not something like `0.10` — at 0.05 the
same clip lost every event and returned a single frame. A distant figure covers very
little of the frame, so thresholds in the tenths are wrong for CCTV by an order of
magnitude.

**`--target` solves for a threshold; it does not infer one.** Deriving a threshold from
the footage alone does not work: on a locked-off camera the median score and its MAD are
both exactly `0.0`, so there is no baseline to scale. What *does* work is asking the user
how much of the clip to keep and bisecting for it — keep-ratio falls monotonically as the
threshold rises. The search is **geometric**, because useful thresholds span decades and
linear steps waste every iteration in the wrong one.

The solver runs on the scores pass one already produced, so `--target` costs no extra
decoding. Measured on a 61-minute clip: a 10% target solved to `0.0492` and returned
10.0% in 40 events.

**Padding quantises what is reachable.** One shortest event is
`(min_event + 2*pad) * fps` frames — 13% of a 37-second clip but 0.1% of an hour, so
small targets are impossible on short clips. `solve_threshold()` clamps the goal up to
that floor; without it the search drives the threshold past every event and returns an
empty summary, which is worse than the smallest real one. The CLI explains the overshoot
rather than silently missing the target.

**Frame counts from `probe()` are estimates** for some containers, so the real count can
overshoot. Anything displaying `current/total` must clamp, or it shows `1200/1120`.

**Where the time goes.** Profiled on a 1120-frame 720p clip, decode **54%**,
`downscale_to_gray` **37%**, rolling background **7%**, `change_score` **1%** — 5.0s
total. Decode is now the floor; the remaining pixel work is small. Those figures were
measured when every source was compared at 320px; the pixel work now scales with the
resolution tier, so a 4K source at 640px costs roughly 4x the per-frame pixel work.

`RollingBackground` used to dominate at **60%** (18.1s total) because `average` re-stacked
and re-averaged the whole window every frame. It now keeps a running total, adding the
incoming frame and subtracting the evicted one, which made the whole pipeline **3.6x**
faster. The cost is **O(1) in `window`** rather than O(window), so raising `--window` is
now nearly free — measured flat from 10 to 240 frames. Do not "simplify" it back into a
recompute.

Two things that implementation depends on, both tested:

- **Frames are copied on the way in.** The total is updated at `add()` time, so a caller
  reusing a buffer (as OpenCV does when decoding) would otherwise cause the eventual
  subtraction to remove values that were never added, corrupting the total permanently
  rather than skewing a single frame.
- **float64 makes the running total exact.** Every integer below 2**53 is exactly
  representable and a window of pixels sums to at most `window * 255`, so add/subtract
  never rounds and the total cannot drift. This would not hold for float32.

GPU is not the answer here: the PyPI wheel has no CUDA (`cv2.cuda` reports 0 devices),
and OpenCL `UMat` measured *slower* than NumPy (513 vs 630 fps) because the transfer
costs more than the tiny 320x180 operations save.

## How checking works

`diagnose.py` answers a different question from summarization — "can this camera still
see anything" rather than "what happened" — and the two fail in opposite directions: a
sprayed-over lens produces a beautifully quiet summary. Hence a separate module and the
`check` subcommand rather than a warning bolted onto `summarize`.

`frame_metrics()` reads five numbers from each frame in **one** decode pass (summarizing
needs two; checking writes nothing, so it needs one). All five come off the same
downscaled gray frame the motion path uses, so the thresholds mean the same thing across
resolutions. `detect_faults()` then groups them, and is pure — tests build `FrameMetrics`
directly instead of synthesising footage.

| Fault | Signal | Default |
| --- | --- | --- |
| `dark` | mean grey level | `< 20` |
| `blurry` | variance of the Laplacian | `< 15` |
| `obstructed` | share of frame with no local contrast | `>= 0.9` |
| `frozen` | byte-identical consecutive frames | `>= 2s` |
| `shaken` | share of pixels changing at once | `>= 0.2` |

Measured on a 320x240 synthetic corridor — healthy, then broken five ways:

| Clip | luminance | detail | flat | peak change |
| --- | --- | --- | --- | --- |
| healthy (walker) | 109.3 | 126.5 | 0.686 | 0.019 |
| blacked out | 6.2 | 0.2 | 1.000 | – |
| defocused | 109.2 | 0.9 | 0.823 | – |
| lens sprayed | 139.5 | 0.4 | 1.000 | – |
| knocked 22% sideways | – | – | – | 0.365 |

Real footage, two different cameras, no false positives: 1280x720 gave luminance 107.2
and detail 315.1; a 2320x2320 fisheye over 24 minutes gave 93.8 and 1832.0. Detail swings
by 6x between real cameras, so the threshold is set far below both rather than near
either.

Five things here are load-bearing:

- **Blur is suppressed when the picture is dark or flat.** A black or covered frame has
  no edges either, so an ungated blur check fires on both and sends someone to adjust a
  lens that is working. Dark and `obstructed` stay independent, because a capped lens
  genuinely is both and nothing here can tell a cap from a dead sensor.
- **`--obstruction 0.9` sits in the gap between defocused `0.823` and covered `1.000`.**
  That gap is the only thing separating "refocus it" from "go clean it". Do not widen the
  threshold without re-measuring both.
- **Freeze means byte-identical, not similar.** Live sensors always dither, so an exact
  match proves the stream repeated rather than that the scene held still. A tolerance
  here would flag every locked-off camera watching an empty room.
- **`shake` is set from the healthy floor, not the knock size.** Displacement does *not*
  map monotonically onto change: 22% sideways scored 0.369 but 50% scored only 0.227,
  because a self-similar scene realigns with itself. Walking peaked at 0.019, so 0.2 is
  ten times the floor. It also catches any abrupt whole-view change — a 40-level
  brightness step changed 100% of pixels, so lights coming on read as a knock.
- **Faults are never padded or merged.** Motion events are padded so people are seen
  walking in; a fault's edges are *evidence about the camera*, and widening them would
  misreport when the picture came back. `shaken` is additionally exempt from
  `--min-fault`, because a knock lasts one frame by nature.

`check` exits non-zero when it finds anything, and an unreadable file exits non-zero too.
Both mean the same thing to a cron job, which is the point.

**Where the time goes.** Profiled on the same 1120-frame 720p clip the summarize numbers
use: decode **47%**, `downscale_to_gray` **33%**, `flat_fraction` **9%**, `detail_score`
**9%**, frame differencing **2%** — 5.5s total. The two new measurements add roughly 18%
on top of the work a motion pass already does, and the whole check still costs about
*half* of summarizing, which decodes twice. Both new metrics scale with the comparison
tier, so a 4K source pays about 4x the per-frame pixel cost.

## Conventions

- **CLI shape.** Argument wiring lives in `build_parser()`; each subcommand registers its
  handler with `set_defaults(handler=...)`, and `main(argv=None) -> int` dispatches to it
  and is invoked as `raise SystemExit(main())`. Keep `main([])` callable in-process so
  tests never need a subprocess — with no subcommand it prints help and returns `1`
  rather than raising. New subcommands belong in `build_parser()`, with the real work in
  separate modules under `src/cctv_summary/`.
- **Video I/O stays in `video.py`.** Do not call `cv2.VideoCapture`/`VideoWriter` from
  other modules. Use `probe()`, `iter_frames()`, `open_capture()`, `write_frames()`;
  they validate input and always release the handle. `write_frames` raises rather than
  leaving the 0-byte file `VideoWriter` produces when a codec is missing.
- **Selection logic stays pure.** `motion_scores()` and `detect_events()` take and return
  plain values with no file I/O, so event logic is testable with a sketched signal
  (`"...###..."`) instead of real footage. Keep decoding and encoding in
  `summarize_video()`. `mask.py` is pure too: it builds arrays from numbers and never
  touches a file, and so is `detect_faults()`, which takes readings rather than frames.
- **Errors.** `video.py`, `summarize.py`, `mask.py`, `diagnose.py`, and `manifest.py`
  raise `VideoError`
  for anything a user can cause (missing file, unreadable container, bad
  speed/threshold/window, no GUI, missing codec, unwritable manifest path, a mask that
  watches nothing, a nonsense fault threshold). `main()`
  catches it, prints `error: ...` to stderr, and returns `1`. Never let
  a raw `cv2.error` reach the user.
- **Exit codes carry meaning for `check` only.** `check` returns `FAULTS_FOUND` (1) when
  it finds faults, the same code an error returns, because a monitoring script wants to
  be woken for either. Other subcommands return 0 unless something went wrong.
- **Drawing lives in `overlay.py`.** Annotation functions take a frame, return a new one,
  and **must not mutate the input** — OpenCV reuses decode buffers, so draw on
  `frame.copy()`. Sizes derive from the frame dimensions so markers scale with
  resolution instead of vanishing on 1080p.
- **Overlays need a backing plate, not an outline.** White marks with a thin dark outline
  are unreadable on bright footage; badges darken a rectangle behind themselves first.
  Test legibility by asserting on contrast (`corner.min()` / `corner.max()`) against
  white *and* black frames, not just "some pixels changed".
- **Badges occupy fixed slots in the top-right.** `SUMMARY_SLOT` is burned into the file
  by `summarize`; `SPEED_SLOT` is drawn during playback and stacks below it. Playback
  cannot tell whether a file already carries the summary mark, so the slots are reserved
  statically rather than packed. Give any new badge its own slot.
- **Inset the plate, not the glyph.** `_badge_box()` positions the *plate* inside the
  margin; insetting the glyph instead lets the plate bleed off the frame edge. The margin
  also shrinks on small frames, or a fixed inset drags the badge toward the middle.
- **Progress goes to stderr, and only to a terminal.** `summarize_video()` and `play()`
  take a `progress=` reporter and stay silent without one, mirroring the `Display`
  pattern — logic never touches the terminal. `cli._progress_for()` picks
  `TerminalProgress` or `NullProgress` by `isatty()`, so redirected output and CI logs
  stay free of carriage returns, and `> file` still captures a clean report. Draws are
  throttled to ~10/sec; redrawing per frame costs more than the work being measured.
  **Windowed playback is deliberately unreported** — the window is its own sign of life,
  so the CLI passes a reporter only for `--headless`.
- **The manifest is data, not a rendering of the table.** `manifest.py` builds a plain
  dict from a `SummaryStats` and writes it with `json`; it does no video work and never
  prints. `MANIFEST_VERSION` is bumped only when the shape changes in a way that could
  break a consumer — *adding* a key (as `watch`/`ignore` did) is not one of those.
  `format_timestamp()` lives there and the CLI table uses it, so both
  renderings of a run agree. **`--manifest -` moves the readable report to stderr** —
  JSON on stdout has to be the only thing there or piping it into a parser fails.
- **Keep GUI out of logic.** Playback writes to the `Display` protocol  (`show`/`wait`/`close`), with `WindowDisplay` (real `cv2.imshow`) and `NullDisplay`
  (headless, never waits) as the implementations. Anything needing a window must accept
  an injectable display so it stays testable. `play(..., display=...)` overrides
  `headless=`.
- **Copyright header.** Every `.py` file in `src/` and `tests/` opens with a GPLv3
  per-file notice (`Copyright (C) 2026 Bao.TangDuc`, before any module docstring). It
  isn't legally required for the license to apply — the appendix text is a
  recommendation — but it keeps each file self-identifying if copied out of context.
  Give new files the same header.
- **Typing.** Every module starts with `from __future__ import annotations` and uses
  modern typing (`X | None`, `collections.abc`); ruff's `UP` rules enforce this.
- **Lint/format config.** Ruff is configured in `pyproject.toml`: line length 88, rules
  `E, F, I, UP, B, SIM`, import sorting (`I`) enforced. Running it is covered by Rules 1
  and 2 above.
- **Version.** `__version__` in `src/cctv_summary/__init__.py` and `version` in
  `pyproject.toml` are maintained by hand and must be bumped together.
- **Dependencies.** Runtime deps go in `[project.dependencies]`, tooling in
  `[dependency-groups] dev`. After editing either, run `uv sync` and commit the updated
  `uv.lock` in the same change.
- **Media files are gitignored** (`test-data/`, `*.mp4`, `*.avi`, `*.mkv`, `*.mov`).
  Local footage is large and often personal — never commit it. Tests synthesise their
  own clips instead.
- **Artifacts.** `.venv/`, `.pytest_cache/`, `.ruff_cache/`, and `__pycache__/` are
  already gitignored. Never commit them.

## Troubleshooting

- `error: uv trampoline failed to canonicalize script path` from `uv run pytest` (or any
  other `.venv/Scripts/*.exe`) means that console-script shim is stale, not that the code
  is broken. Repair it with `uv sync --reinstall-package <name>` (e.g. `pytest`).
  `uv run python -m pytest` works as a fallback in the meantime.
- Installing `opencv-python` downloads a large wheel and can take several minutes on a
  cold cache. It is not hung — let `uv sync` finish.
- `VideoError` about not opening a display window means a headless build
  (`opencv-python-headless`) or no available desktop session. `info` still works; only
  `play` needs a GUI.

## Testing

Tests live in `tests/` (`testpaths` is pinned there, `addopts = "-ra"`). Follow the
existing patterns in `tests/test_cli.py` and `tests/test_video.py`:

- Call `main([...])` directly and assert on the returned exit code.
- Capture output with the `capsys` fixture.
- Wrap `--version` / `--help` style paths in `pytest.raises(SystemExit)` and assert on
  `excinfo.value.code`.
- Use the session-scoped `sample_video` fixture for anything needing real footage. It
  writes a tiny MJPG clip to a temp dir and carries its own `path`, `width`, `height`,
  `fps`, and `frames`, so assertions never hardcode those numbers. **No media files are
  committed** — keep it that way.
- **`sample_video` is never still.** It ramps brightness every frame, so it always
  registers motion, even at `--threshold 1.0`. Tests about *absence* of motion must
  synthesise their own static clip. It is also a **flat colour field**, so `check`
  correctly reports it as an obstructed lens — anything testing healthy footage needs its
  own textured clip (`_write_textured_clip` in `tests/test_cli.py`, `textured()` in
  `tests/test_diagnose.py`).
- Fault logic is tested from hand-built `FrameMetrics` via the `healthy(**overrides)` /
  `series(n, **overrides)` helpers, the same way event logic uses `signal("..##..")`:
  hysteresis-free grouping, gating, and minimum durations are about the readings, not the
  pixels. Only the handful of `diagnose_video` tests need real footage.
- Test event logic with a sketched signal via the `signal("..###..")` helper rather than
  real footage: the interesting cases (hysteresis, merging, blip rejection) are about the
  score sequence, not pixels.
- Manifest shape is tested from a hand-built `SummaryStats`, not from decoded footage;
  `build_manifest()` is pure, so only the CLI tests need a real clip. Pass
  `generated_at=` to assert on the timestamp.
- Mask behaviour is tested on `half_lit()` frames, where the moving share is known
  exactly, so a wrong denominator shows up as a wrong number rather than a vague
  "fewer events". `tests/test_mask.py` covers the geometry; `tests/test_summarize.py`
  covers what it does to scores.
- **Tests must never open a window.** Use `headless=True`, pass a fake `Display` to
  `play()`, or monkeypatch `cctv_summary.cli.play`. `tests/test_video.py::FakeDisplay`
  is the reference fake and can simulate a quit key.
- `tests/` is not an importable package: there is no `__init__.py`, so tests cannot
  `import tests.conftest`. Share state through fixtures instead.
- Fakes that stand in for `play()` take `(video, **kwargs)` and assert on keyword names,
  so adding a new `play()` option does not break every CLI test.
- `FakeDisplay` records every frame in `.shown`, so tests can assert on what playback
  actually handed the display (badged vs untouched), not just how many frames it saw.

## Next steps

**Decided:** the app reads and plays video with OpenCV, summarizes by detecting
motion events and keeping them as continuous clips, and checks for camera faults. Those
layers exist in `video.py` and
`summarize.py`, with frame annotations in `overlay.py`, spatial masks in `mask.py`,
camera diagnostics in `diagnose.py`, and a
JSON event manifest in `manifest.py`. Frame-by-frame dropping was tried
first and replaced: it produced scattered, unwatchable stills. Regions are specified as
fractional rectangles on the command line; a mask image and an interactive picker were
both considered and not taken, the picker because it would need a GUI session the project
keeps optional. Fault checking is its own subcommand rather than a warning inside
`summarize`, because it answers a different question and fails in the opposite direction.

**Still undecided:** whether to go beyond raw pixel motion — object or person detection
(so a swaying tree stops counting as an event), keyframe thumbnails, burned-in
timestamps, text summaries, a CSV manifest alongside the JSON one, a JSON report for
`check`, and whether live RTSP
input is in scope. Also unresolved: how
to pick a threshold automatically instead of asking the user to tune it per camera.

Ask the user for direction before choosing a summarization approach or adding further
heavy dependencies (model runtimes, cloud SDKs, ffmpeg bindings). Do not infer a roadmap
and start building.
