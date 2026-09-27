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

**Status: video I/O and motion-event summarization work.** The app reads and plays video
with OpenCV. `cctv-summary info` prints container metadata; `cctv-summary play` shows a
file in a window (badging the corner when `--speed` is above 1) or decodes headless
(`--headless`); `cctv-summary summarize` finds the stretches where something moves and
writes them out as continuous clips, listing their timestamps and burning a scissors mark
into every output frame. Motion is raw pixel change — there is no object or person
detection, so a swaying tree counts as an event.

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
uv run cctv-summary play FILE --headless   # no window, decode as fast as possible
uv run cctv-summary summarize SRC DST      # shorter copy, changed frames only
uv run cctv-summary summarize SRC --dry-run  # report keep/drop without writing
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
src/cctv_summary/summarize.py  motion scoring, event detection, summarize_video
tests/conftest.py              sample_video fixture (synthesises a small MJPG clip)
tests/                         pytest suite (test_cli/_video/_overlay/_summarize.py)
pyproject.toml                 single source of truth for metadata, deps, ruff, pytest
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
   `window` frames), with each pixel thresholded by `tolerance`. Comparing against the
   background rather than the previous frame is deliberate: a slow walker keeps scoring
   the whole time they cross, instead of only when they move quickly.
2. **Group** the signal — `detect_events()` opens an event when the score reaches
   `threshold` and closes it only after the score has stayed below `threshold *
   STOP_RATIO` for `cooldown_seconds`. That hysteresis is what stops one walker pausing
   mid-frame from becoming three events. Events shorter than `min_event_seconds` are
   dropped **before** padding, so padding cannot rescue camera noise; survivors are then
   padded by `pad_seconds` either side and merged where they overlap.
3. **Write** the kept ranges, badged with the scissors mark.

**The source is decoded twice.** Padding reaches backwards from the moment motion is
noticed, and buffering an hour of frames to look behind is not viable, so pass one scores
and pass two writes. Do not "optimise" this into a single pass without solving the
look-behind problem.

Output is written at the source fps, so it is shorter but no longer wall-clock accurate,
and OpenCV carries no audio. `SummaryStats.events` carries the timings, which the CLI
prints as an `HH:MM:SS` table — for a 60-minute recording, knowing *when* something
happened matters as much as the clip itself.

**`tolerance` and `threshold` are different knobs.** `tolerance` (0–255) is "did *this
pixel* move"; `threshold` (0–1) is "did *enough pixels* move". Do not conflate them.

**Calibration, measured not guessed.** On a synthetic 640x360 corridor clip a walking
person scores **~0.01** (1% of pixels) and peaks at 0.027; idle frames score ~0.0000.
That is why `DEFAULT_THRESHOLD` is `0.01` and not something like `0.10` — at 0.05 the
same clip lost every event and returned a single frame. A distant figure covers very
little of the frame, so thresholds in the tenths are wrong for CCTV by an order of
magnitude.

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
  `summarize_video()`.
- **Errors.** `video.py` and `summarize.py` raise `VideoError` for anything a user can
  cause (missing file, unreadable container, bad speed/threshold/window, no GUI, missing
  codec). `main()` catches it, prints `error: ...` to stderr, and returns `1`. Never let
  a raw `cv2.error` reach the user.
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
- **Keep GUI out of logic.** Playback writes to the `Display` protocol
  (`show`/`wait`/`close`), with `WindowDisplay` (real `cv2.imshow`) and `NullDisplay`
  (headless, never waits) as the implementations. Anything needing a window must accept
  an injectable display so it stays testable. `play(..., display=...)` overrides
  `headless=`.
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
  synthesise their own static clip.
- Test event logic with a sketched signal via the `signal("..###..")` helper rather than
  real footage: the interesting cases (hysteresis, merging, blip rejection) are about the
  score sequence, not pixels.
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

**Decided:** the app reads and plays video with OpenCV, and summarizes by detecting
motion events and keeping them as continuous clips. Those layers exist in `video.py` and
`summarize.py`, with frame annotations in `overlay.py`. Frame-by-frame dropping was tried
first and replaced: it produced scattered, unwatchable stills.

**Still undecided:** whether to go beyond raw pixel motion — object or person detection
(so a swaying tree stops counting as an event), keyframe thumbnails, burned-in
timestamps, text summaries, and whether live RTSP input is in scope. Also unresolved: how
to pick a threshold automatically instead of asking the user to tune it per camera.

Ask the user for direction before choosing a summarization approach or adding further
heavy dependencies (model runtimes, cloud SDKs, ffmpeg bindings). Do not infer a roadmap
and start building.
