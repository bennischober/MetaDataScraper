# AGENTS.md — working rules for AI agents in this repository

Instructions for AI coding agents (OpenCode, Codex, Claude Code, ...) working in
this repository. These rules are the distilled, non-negotiable core; the
README documents the product, PRODUCT.md the product direction.

## What this project is

A Python CLI + resident app that catalogs a personal movie library
(`scan`), predicts savings (`plan`), re-encodes with measured quality
(`encode`, VMAF-driven CRF search) and renders an HTML report. The Python
code is an orchestrator: all heavy work is delegated to external binaries
(`ffprobe`, `ffmpeg`, `HandBrakeCLI`) via `subprocess`. The movie library
is sacred — source files are never modified in place.

## Non-negotiable rules

1. **Never modify or delete source movie files.** The library is sacred
   (see PRODUCT.md). New encodes go to a separate output directory. The
   only exception is `--remove-bars`, which is explicit, opt-in and
   irreversible — never enable it on your own.
2. **Never commit secrets.** The litellm API key lives in
   `.opencode/orthos-key` (gitignored). Do not copy keys into code, tests,
   logs or commit messages. Do not use PII in commit messages.
3. **Run the full gate before every commit.** All five must pass:

   ```sh
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run mypy
   uv run mypy --platform linux
   uv run pytest -q
   ```

   CI runs exactly these (mypy twice: the second run is CI's platform
   view — windows stubs know APIs like `os.startfile`, linux stubs do
   not; the local default hides exactly this class of failure). If you
   cannot make the gate pass, do not commit; report the blocker instead.

## Project conventions

- **Package manager is uv.** Use `uv add` / `uv remove`, not pip. Never
  edit `uv.lock` by hand. `requirements.txt` does not exist anymore — do
  not create one.
- **Python 3.15** (`.python-version`), minimum supported 3.10 (`pyproject.toml`).
- **src layout**: code lives in `src/shrinkray/`, tests in `tests/`.
  Entry point: `shrinkray` (typer app) or `python -m shrinkray`.
- **Formatting is ruff's job, not yours.** After writing or restructuring
  code, run `uv run ruff format` on the touched files before committing.
  The CI format check is strict.
- **Type hints are checked by mypy** with `check_untyped_defs` and
  `no_implicit_optional`. New public functions get full type hints and a
  docstring in Google style (see existing modules for the pattern).
- **Tests live in `tests/` as plain `unittest`** classes run by pytest,
  one file per source module (`test_encode.py`, `test_quality.py`,
  `test_watch.py`, `test_db.py`, `test_report.py`, `test_cli.py`,
  `test_jobs.py`, `test_dedupe.py`, `test_resources.py`,
  `test_media.py`, `test_misc.py`). New features need tests; bug fixes
  need a test that would have caught the bug. Tests must not require
  ffmpeg/HandBrake binaries or network access — mock or use `tmp_path`
  fixtures. Tests that touch the filesystem or path scopes use **real
  `tempfile`
  directories, never fictional paths** (`D:/Movies` is absolute on
  Windows but a relative path on Linux/CI — scope containment and
  `resolve()` behave differently and the local gate cannot see it).
- **Commit messages follow Conventional Commits** (`feat`, `fix`, `docs`,
  `refactor`, `perf`, `test`, `chore`, `ci`, `build`, `style`) with a scope
  where it adds context, e.g. `fix(encode): ...`. Body explains the *why*
  when the *what* is not obvious. Never push directly to `main` without
  the gate passing.
- **The sqlite database (`metadata.db`) is user data, not source.** Schema
  changes go through a migration in `db.py` (bump `SCHEMA_VERSION`, see the
  v3 migration for the pattern). Never commit a `.db` file.

## External binaries

`ffprobe`, `ffmpeg` and `HandBrakeCLI` are subprocess dependencies. When a
feature needs one of them:

- always pass argument **lists** to `subprocess` (never shell strings),
- set a `timeout`,
- treat a zero exit code as *not sufficient* success — verify the expected
  artifact exists (see the encode truncation fix for why),
- degrade gracefully when the binary is missing (see `find_handbrake()`).

## Multi-agent etiquette

Multiple agents (and humans) work in this repository concurrently.

- **Do not touch files you did not "own" in the current task** unless you
  have read them first. Uncommitted changes in the working tree belong to
  whoever is working — check `git status` before any `git add -A` or
  `git stash`, and only stage files from your own task.
- Prefer short-lived feature branches and worktrees over long-lived
  uncommitted WIP in the shared checkout. `main` should always be green.
- If you must switch branches in the shared checkout, check for uncommitted
  changes first and stash them with a descriptive message.
- After pulling, run the gate again before you continue — someone may have
  merged something that interacts with your change.

## Autonomy

- Work through the task list without stopping to ask what comes next;
  `TODO.md` records agreed next steps and known limitations.
- Prefer sensible defaults over questions; document the choice you made.
  Ask only for: irreversible or destructive actions, missing credentials,
  or genuinely ambiguous product direction.
- When you stop, state what is done, what is verified, and what remains —
  not as a substitute for doing it.
- Long-running background work (encodes, watch loops) must be started with
  a clear note on how to stop it, and must never block the session.
