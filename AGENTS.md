# AGENTS.md — working rules for AI agents in this repository

Instructions for AI coding agents (OpenCode, Codex, Claude Code, ...) working in
this repository. These rules are the distilled, non-negotiable core; the
README documents the product, PRODUCT.md the product direction.

## What this project is

A Python CLI + resident server app that answers one question per file: **is
this file bigger than its quality requires?** It catalogs a video collection
(`scan`), predicts savings (`plan`), re-encodes with measured quality
(`encode`, VMAF-driven CRF search) and renders an HTML report. The Python
code is an orchestrator: all heavy work is delegated to external binaries
(`ffprobe`, `ffmpeg`, `HandBrakeCLI`) via `subprocess`.

It is built to be **given to the community**: it will be installed by strangers on
their own servers, pointed at libraries they cannot re-acquire, and run unattended.
That is why the engineering standard below is non-negotiable — a defect here can
destroy someone else's data, not just annoy its author.

The target shape is a container on a server, pointed at a media folder, running
unattended with a watchdog and a LAN dashboard. The CLI is a first-class
product, not a debugging surface: every capability works without the resident
process. See PRODUCT.md for the product contract.

## Engineering standard (non-negotiable)

This is not a hobby project. It is built to be **given to the community**, which
raises the bar from "works on my machine" to "a stranger can trust it with their
library". Read every rule below as a requirement, not an aspiration.

1. **Secure by construction.** Every path from an untrusted input to a file, a
   subprocess or a socket is validated at the boundary. Argument lists, never shell
   strings. Paths contained to an allowlist after `realpath`. No secret in a tracked
   file. A destructive action is opt-in, explicit and recoverable. "It's only on my
   LAN" is not a security boundary — the target deployment is a NAS reachable by
   every device on that network.
2. **Correct under interruption.** This runs unattended for hours against a library
   that cannot be re-acquired. Every write is atomic or resumable; every crash leaves
   a recoverable state, never a missing or half-written file. A zero exit code is not
   success — verify the artifact.
3. **Honest about failure.** No silent fallback, no swallowed exception, no "it
   probably worked". If a decision cannot be measured, it is reported as unmeasured
   and the file is left alone. An error a user cannot see is a bug.
4. **Tested where it matters.** A behaviour change ships with a test that would fail
   without it. Tests never pollute the working tree, never depend on the wall clock or
   timing luck, and never encode a platform assumption that silently inverts on
   another OS.
5. **Readable by a stranger.** A module has one coherent responsibility, names state
   intent, and the control flow is direct. The test is whether someone who has never
   seen the file can change it safely. Cleverness is a cost, not a credential.
6. **No unsupported claim in a doc.** Every `file:line`, every performance number,
   every "supported" in the README is checked against the code or the measurement it
   describes. A stale doc is a defect with a longer half-life than a stale test.

"Proper" here means exactly what RULES.md means: only what is needed, never less than
required. It does not mean maximal — it means nothing missing and nothing superfluous.

## Scope boundary (read before adding a feature)

ShrinkRay changes a file's **bytes**, never its **identity**. Path, filename and
the set of files stay as they are.

- **In scope:** re-encoding at measured quality, black-bar cropping, deciding
  *whether* a file is worth touching, and recording that decision.
- **Out of scope:** naming, renaming, moving, organizing, matching, scraping,
  deduplicating, acquiring, playing or serving media. That work belongs to the
  tooling that already assembled the collection.

Do not add a feature whose usefulness depends on knowing what a file is *called*
or *which* file it is. "Is this file the size its quality needs?" is the only
question this tool answers.

Never name, vendor, or assume any particular acquisition or naming automation in
code, comments, docs or commit messages. ShrinkRay is written for "a large
existing video collection" and nothing more specific.

## Non-negotiable rules

1. **Never delete a source movie file.** The library is sacred (see
   PRODUCT.md). ShrinkRay has no code path that deletes a source; replaced
   originals are renamed aside and kept until the operator removes them
   manually. A destructive action in a feature request is a signal to stop and
   ask, not to implement.

   **Swap-in-place is the intended shape** (encode to a temp file, verify it,
   then move it into the original's path with the original renamed aside), but
   it is **not implemented yet** — today `encode` writes to a separate output
   directory. `--remove-bars` stays explicit and opt-in; never enable it on
   your own.
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
  `test_media.py`, `test_misc.py`). Opt-in smoke tests against the real
  binaries live in `tests/test_smoke.py` — skipped unless
  `SHRINKRAY_SMOKE=1` is set AND ffmpeg/HandBrakeCLI are on PATH (they
  never run in CI or the default gate; run them explicitly after changes
  to the probe/encode/watchdog paths). New features need tests; bug fixes
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

- **Claim an issue before you start working on it.** Assign it to yourself as the
  first action, before the first edit:

  ```sh
  gh issue edit <number> --add-assignee @me
  ```

  An assigned issue is work in progress; an unassigned one is free to take. This is
  the only coordination channel that works across separate checkouts, separate agent
  sessions and separate humans — a local branch name is invisible to everyone else.
  Pick an unassigned issue, or one already assigned to you. If it is assigned to
  somebody else, do not start: leave it, and pick a different one.
- **When you stop, say where it stands.** If the issue is finished and merged,
  close it. If you are handing off mid-way, leave the assignment in place and write
  a comment on the issue saying what is done and what is left, so the next agent
  resumes instead of restarting. Unassign only if you are abandoning it.
- **One issue, one agent.** Do not assign yourself a second issue while one is still
  assigned to you and unfinished — the point of the claim is that it means something.
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
  `TODO.md` records agreed next steps and known limitations. Work that is
  already filed as a GitHub issue lives there, not in `TODO.md`.
- Prefer sensible defaults over questions; document the choice you made.
  Ask only for: irreversible or destructive actions, missing credentials,
  or genuinely ambiguous product direction.
- When you stop, state what is done, what is verified, and what remains —
  not as a substitute for doing it.
- Long-running background work (encodes, watch loops) must be started with
  a clear note on how to stop it, and must never block the session.
