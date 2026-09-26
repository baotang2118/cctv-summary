# AGENTS.md

Working notes for AI agents (and humans) picking up this repository. Keep this file
short, factual, and current: it is shared memory, not documentation. Update it when a
convention, command, or the project status actually changes.

## Project

`cctv-summary` is intended to become summarization tooling for CCTV footage.

**Status: scaffold only.** The package exists, is packaged, tested, and linted, but it
contains no summarization logic. `src/cctv_summary/cli.py` is a placeholder that parses
`--name` / `--version` and prints a greeting. `[project.dependencies]` is empty — there
is no video, imaging, or ML stack wired in yet.

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
uv run python -m cctv_summary  # module entry point
```

`requires-python` is `>=3.11`; `.python-version` currently pins `3.14` for local work.
Code must stay compatible with 3.11 — do not use syntax or stdlib APIs newer than that.

## Layout

```
src/cctv_summary/__init__.py   package docstring + __version__
src/cctv_summary/__main__.py   enables `python -m cctv_summary`
src/cctv_summary/cli.py        build_parser() + main(argv) -> int
tests/                         pytest suite (currently tests/test_cli.py)
pyproject.toml                 single source of truth for metadata, deps, ruff, pytest
uv.lock                        committed lockfile — regenerate with uv, never hand-edit
```

This is a **src layout**: the package is only importable via the installed environment,
so imports are always absolute (`from cctv_summary.cli import main`), never relative to
the repo root.

## Conventions

- **CLI shape.** Argument wiring lives in `build_parser()`; `main(argv=None) -> int`
  returns an exit code and is invoked as `raise SystemExit(main())`. Keep `main([])`
  callable in-process so tests never need a subprocess. New subcommands/flags belong in
  `build_parser()`, with the real work in separate modules under `src/cctv_summary/`.
- **Typing.** Every module starts with `from __future__ import annotations` and uses
  modern typing (`X | None`, `collections.abc`); ruff's `UP` rules enforce this.
- **Lint/format.** Ruff is configured in `pyproject.toml`: line length 88, rules
  `E, F, I, UP, B, SIM`. Import sorting (`I`) is enforced, so run `uv run ruff format .`
  and `uv run ruff check .` before committing.
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

## Testing

Tests live in `tests/` (`testpaths` is pinned there, `addopts = "-ra"`). Follow the
existing patterns in `tests/test_cli.py`:

- Call `main([...])` directly and assert on the returned exit code.
- Capture output with the `capsys` fixture.
- Wrap `--version` / `--help` style paths in `pytest.raises(SystemExit)` and assert on
  `excinfo.value.code`.

The four current tests only cover the placeholder greeting. They are expected to be
**replaced**, not preserved, once real functionality lands.

## Next steps

**No roadmap has been decided yet.** Nothing about the summarization pipeline — input
formats, detection/ML approach, output shape, storage, or runtime targets — has been
chosen.

Before designing features or adding heavy dependencies (OpenCV, ffmpeg bindings, model
runtimes, cloud SDKs), **ask the user for direction**. Do not infer a roadmap from the
project name and start building.
