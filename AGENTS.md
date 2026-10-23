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
3. **Run the full gate before every commit.** All six must pass:

   ```sh
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run mypy --platform linux
   uv run mypy --platform win32
   uv run pytest -q
   ```

   Both mypy runs are explicit so the gate is identical on every host. Running
   them is the whole point: `--platform` defaults to `sys.platform`, so a bare
   `uv run mypy` only ever checks the host's own view and is blind to the other
   one. A Windows-only API (`os.startfile`) passes on a Windows machine and
   breaks every Linux/NAS user; a POSIX-only import (`pwd`, `fcntl`) passes on the
   Linux runner and breaks a Windows contributor. Checking both directions catches
   both, whichever machine you are on.

   CI runs exactly these six. If you cannot make the gate pass, do not commit;
   report the blocker instead.

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
- **`test_media/` is for real media, and it is never committed.** The
  folder is part of the repository (tracked via `test_media/.gitkeep`); every
  file in it is ignored by `/test_media/*`. Drop real video files there for
  manual runs and for end-to-end validation, and expect tens of GB to sit in the
  working tree — that is the point, and it is why the ignore rule exists. Never
  `git add -f` a file from this folder, never remove the ignore rule, and
  never point a test or a script at it without a skip guard.
  **No automated test reads `test_media/` today.** `tests/test_smoke.py`
  generates its own synthetic 100s clip in a `tempfile` directory, so the
  default gate and the smoke tests both pass with the folder empty. Real media
  is for a **manual** end-to-end run: `scan`, then `plan`, then `encode`
  against the files in this folder, and `report` to read the result back. That
  is the only way to catch a defect that only real content shows (grain,
  letterboxing, HDR, an aspect-ratio change) — a stub-based test cannot.
  Never commit the media itself, and never reference these filenames in code,
  tests or docs: the set of files in the folder is the operator's business.
- **Commit messages follow Conventional Commits** (`feat`, `fix`, `docs`,
  `refactor`, `perf`, `test`, `chore`, `ci`, `build`, `style`) with a scope
  where it adds context, e.g. `fix(encode): ...`. Body explains the *why*
  when the *what* is not obvious. One logical change per commit — see the
  issue workflow above for how a change reaches `main`.
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

## Issue workflow (the default for every change)

Working on an issue always means the same five steps, in this order. There is no
"small enough to skip the branch" exception — a one-line fix goes through the same
path, because the point is that `main` only ever receives reviewed work. The only way
around it is a deliberate direct push, which has its own rules below.

1. **Claim it.** `gh issue edit <N> --add-assignee @me` (see Multi-agent etiquette
   below). An assigned issue is work in progress; an unassigned one is free to take.
2. **Branch from an up-to-date `main`.** Work on a branch, not on `main`:

   ```sh
   git switch main && git pull --ff-only
   git switch -c <type>/<issue>-<slug>      # e.g. fix/38-test-pollutes-tree
   ```

   The issue number belongs in the branch name, so `git branch --list` answers "what
   is in flight" without consulting GitHub.
3. **Do the work, then pass the gate.** The five commands in Non-negotiable rule 3
   must pass locally before anything is pushed. Commit in reviewable units, not one
   dump — see the commit rules below.
4. **Push the branch and open a pull request.** Reference the issue so it links and
   closes on merge:

   ```sh
   git push -u origin <branch>
   gh pr create --fill --body "Closes #<N>"
   ```

   The PR body says what changed and what was verified, not just what was intended.
   A PR that cannot state how it was verified is not ready.
5. **Merge only when CI is green.** Merge to `main`, delete the branch, and let
   the `Closes #<N>` reference close the issue. If the work is handed off instead,
   see Multi-agent etiquette — do not open a half-finished PR as a way to park it.

### When a PR does not go through

A red or conflicting PR is not finished work, and it is yours until it merges. Do not
close it and open a second one for the same issue, do not ask for a review while CI is
red, and do not force-push a branch a reviewer may already have read. Bring `main` into
the branch, fix what broke, and push the same PR again:

```sh
gh pr checks <N>                              # what is actually red
gh pr view <N> --json mergeable,mergeStateStatus

git switch <branch>
git fetch origin && git merge origin/main       # resolve conflicts HERE, in your branch
# fix the failure, run the five gate commands, commit
git push                                      # the same PR updates in place
```

Repeat until every check is green and the PR reports no conflicts, then merge. This is
the normal case, not an exception: when several agents work at once, `main` moves while
your PR is open, so expect to merge `origin/main` into your branch at least once on any
PR that lives longer than a few hours.

**Merge, do not rebase.** `git merge origin/main` records what was resolved and keeps
the history a reviewer already read; a rebase rewrites those commits and needs a
force-push. If a conflict is genuinely yours to resolve (two agents edited the same
hunk), resolve it in the branch and say in the PR body what the resolution was.

This is about *updating your branch*, not about how the PR lands — squash-and-merge at the
end is fine and is what most merges here use. Squashing the PR is safe; rebasing the open
branch under a reviewer is not.

### Pushing directly to `main`

Direct pushes are technically possible — branch protection is not enabled (verified
2026-10-23: the protection API returns 404) and the `push` permission is held — and
they are permitted when you have a concrete reason: an urgent fix, unblocking a broken
`main`, or a change that cannot be reviewed meaningfully as a diff. The default is still
branch → PR → merge, for one reason: a direct push runs the gate *after* `main` is
already red, while a PR runs CI before anything lands. `main` is what a stranger
clones; it must be green at every commit.

When you do push directly, **Multi-agent etiquette still applies in full**: claim the
issue, keep one issue in flight, check `git status` before staging, and pull before you
push. A direct push is a shortcut around the review step, never around the coordination.

GitHub does not let an author approve their own pull request, and every PR here is opened
by the same account that would merge it — so "approved" in practice means "green checks and
no conflicts", which is what step 5 gates on.

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
- **When you stop, say where it stands.** A merged PR closes its issue through
  the `Closes #<N>` reference — verify it closed, and close it yourself if it did
  not. If you are handing off mid-way, leave the assignment in place and write
  a comment on the issue saying what is done and what is left, so the next agent
  resumes instead of restarting. Unassign only if you are abandoning it.
- **One issue, one agent.** Do not assign yourself a second issue while one is still
  assigned to you and unfinished — the point of the claim is that it means something.
- **Do not touch files you did not "own" in the current task** unless you
  have read them first. Uncommitted changes in the working tree belong to
  whoever is working — check `git status` before any `git add -A` or
  `git stash`, and only stage files from your own task.
- Prefer short-lived branches over long-lived
  uncommitted WIP in the shared checkout. `main` should always be green — see the
  issue workflow above for the exact branch/PR sequence.
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
