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

**Status: video I/O works; summarization does not exist yet.** The app reads and plays
video with OpenCV (`opencv-python`, the only runtime dependency). `cctv-summary info`
prints container metadata and `cctv-summary play` shows a file in an OpenCV window. No
detection, tracking, or summarization logic has been written.

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
tests/conftest.py              sample_video fixture (synthesises a small MJPG clip)
tests/                         pytest suite (test_cli.py, test_video.py)
pyproject.toml                 single source of truth for metadata, deps, ruff, pytest
uv.lock                        committed lockfile — regenerate with uv, never hand-edit
```

This is a **src layout**: the package is only importable via the installed environment,
so imports are always absolute (`from cctv_summary.cli import main`), never relative to
the repo root.

## Conventions

- **CLI shape.** Argument wiring lives in `build_parser()`; each subcommand registers its
  handler with `set_defaults(handler=...)`, and `main(argv=None) -> int` dispatches to it
  and is invoked as `raise SystemExit(main())`. Keep `main([])` callable in-process so
  tests never need a subprocess — with no subcommand it prints help and returns `1`
  rather than raising. New subcommands belong in `build_parser()`, with the real work in
  separate modules under `src/cctv_summary/`.
- **Video access goes through `video.py`.** Do not call `cv2` from `cli.py` or new
  feature modules. Use `probe()`, `iter_frames()`, `open_capture()`; they validate input
  and always release the `VideoCapture`.
- **Errors.** `video.py` raises `VideoError` for anything a user can cause (missing file,
  unreadable container, bad speed, no GUI). `main()` catches it, prints `error: ...` to
  stderr, and returns `1`. Never let a raw `cv2.error` reach the user.
- **Keep GUI out of logic.** Playback writes to the `Display` protocol
  (`show`/`wait`/`close`), with `WindowDisplay` as the real `cv2.imshow` implementation.
  Anything needing a window must accept an injectable display so it stays testable.
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
- **Tests must never open a window.** Pass a fake `Display` to `play()`, or monkeypatch
  `cctv_summary.cli.play`. `tests/test_video.py::FakeDisplay` is the reference fake and
  can simulate a quit key.
- `tests/` is not an importable package: there is no `__init__.py`, so tests cannot
  `import tests.conftest`. Share state through fixtures instead.

## Next steps

**Decided:** the app reads and plays video with OpenCV. That layer exists in `video.py`.

**Still undecided:** everything about summarization itself — what "summary" means
(keyframes, motion segments, event clips, text), the detection/ML approach, output
format, storage, and whether live RTSP streams are in scope. `probe()`/`iter_frames()`
are the intended entry points to build on.

Ask the user for direction before choosing a summarization approach or adding further
heavy dependencies (model runtimes, cloud SDKs, ffmpeg bindings). Do not infer a roadmap
and start building.
