---
description: Adversarial review of the current diff or $ARGUMENTS
---
Review $ARGUMENTS. If $ARGUMENTS is empty, review the uncommitted changes:

!`git status --short; git diff --stat; git diff --cached --stat`

Review order:
1. Correctness bugs (logic errors, unhandled edge cases, wrong assumptions)
2. Violations of AGENTS.md (subprocess guardrails, library safety, conventions)
3. Missing tests for new behavior, or a bug fix without a regression test
4. Type/promise drift: stale docstrings, wrong annotations

Report findings in severity order with file:line references. For each
finding state: what breaks, how you know (evidence), and the minimal fix.
Do not fix anything unless I ask you to.
