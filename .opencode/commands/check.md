---
description: Run the full pre-commit gate (ruff, mypy, pytest) and fix what fails
---
Run the project's full verification gate and fix every issue you find:

!`uv run ruff check src tests 2>&1; uv run ruff format --check src tests 2>&1; uv run mypy 2>&1; uv run pytest -q 2>&1`

For each failing check: fix the root cause (do not weaken or delete tests,
do not add blanket ignores), re-run the failed check, and repeat until all
four pass. Then summarize what was broken and what you changed. If a check
cannot be made to pass, stop and report the blocker instead of committing.
