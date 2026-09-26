# cctv-summary

Summarization tooling for CCTV footage.

The project is scaffolded with a `src/` layout, managed by [uv](https://docs.astral.sh/uv/),
tested with `pytest`, and linted/formatted with `ruff`.

## Requirements

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Setup

```bash
uv sync
```

This creates `.venv/` and `uv.lock` with the runtime and dev dependencies.

## Run

```bash
uv run cctv-summary --help
uv run cctv-summary --name tangd
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
src/cctv_summary/   package source
tests/              pytest suite
pyproject.toml      project metadata, dependencies, tool config
AGENTS.md           working notes for agents and contributors
```

## Status

The CLI is currently a placeholder that prints a greeting and the package
version. Real summarization functionality can be added under
`src/cctv_summary/`.

## Agent notes

[AGENTS.md](./AGENTS.md) holds the shared working notes for AI agents and new
contributors: toolchain rules, code conventions, testing patterns, and the
current project status. Read it before making changes, and keep it up to date.
