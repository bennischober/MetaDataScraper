---
description: Create a feature branch, implement a TODO.md item end to end, gate, commit
---
Implement one item from TODO.md as a complete, shippable unit of work.

1. Read TODO.md and pick the first open item matching: $ARGUMENTS (if
   empty, pick the highest-priority actionable item)
2. Create a feature branch `feature/<short-name>` from current main
3. Plan the implementation with the todo tool, then implement it
4. Follow AGENTS.md: gate before commit, tests included, conventional
   commit messages, library safety
5. When the gate passes and everything is committed: summarize what was
   implemented, what was tested, and what remains in TODO.md

Do not push or merge to main unless I explicitly ask. If the item turns
out to be blocked (missing credentials, ambiguous direction), stop early
and say so instead of guessing.
