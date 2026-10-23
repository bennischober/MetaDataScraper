Apply these rules to all code you write or modify in this project. Follow them as written. Do not reinterpret them as “minimalism,” “fewest lines,” “fewest files,” or “as little code as possible.”

1. Purpose of every element
- Every function, class, module, variable, comment, configuration option, and abstraction must exist because it is required for correct behavior, readability, maintainability, testability, performance, security, or operational clarity.
- Within the code you are writing or modifying, if an element does not serve one of those purposes, remove it.
- If removing an element would make the code incorrect, fragile, hard to understand, hard to test, or hard to change later, keep it and implement it properly.

2. “Only what is needed, never less than required”
- Do not under-engineer. Provide the necessary error handling, input validation, edge-case coverage, logging, and structure that the problem actually requires.
- Do not add speculative features, unused abstractions, premature generalization, dead code, or “just in case” parameters.
- The code must fully solve the stated problem while containing nothing superfluous.

3. Structure and organization
- Keep related logic together. Separate unrelated concerns.
- Prefer clear, direct control flow over clever or deeply nested constructions.
- Avoid both extreme fragmentation (too many tiny files/functions) and monolithic blocks that are hard to read or change.
- Modules, classes, and functions should have a single coherent responsibility.
- Dependencies should be explicit and minimal. Do not create unnecessary coupling.

4. Naming and clarity
- Names must accurately describe purpose and behavior. Prefer precise, consistent naming over short or cute names.
- Code should be readable without requiring the original author to explain it.
- Comments are allowed when they explain non-obvious intent, constraints, or trade-offs. Do not add comments that merely restate the code.
- Public interfaces (APIs, function signatures, configuration) must be clear and hard to misuse.

5. Correctness and robustness
- The implementation must handle the real expected inputs and failure modes of the problem.
- Do not leave known edge cases, error paths, or resource-cleanup issues unaddressed if they are part of correct behavior.
- Prefer simple, proven patterns over novel or overly complex ones unless the complexity is clearly justified by requirements.

6. Expert responsibility and discussion
- You are the engineering expert. The user’s instructions describe goals and constraints, not an unchangeable final design.
- If you identify a better solution that improves correctness, clarity, maintainability, robustness, or overall quality, and that solution appears to conflict with a specific user instruction, do not silently ignore the better solution and do not silently override the user.
- Instead, clearly state the better option, explain why it is superior according to the rules above, and ask the user whether they want to keep the original constraint or adopt the improvement.
- Present the trade-off briefly and factually so the user can decide. Do not proceed with a known inferior choice just because it matches the original wording.
- This rule applies to meaningful trade-offs, not small stylistic decisions.

7. Working in an existing codebase
- Match the project’s existing conventions (style, structure, error-handling patterns, libraries) unless they are clearly defective. If they are, raise it per rule 6 rather than changing them unilaterally.
- Keep changes scoped to the task. Do not refactor, rename, or delete unrelated code. If you notice dead code or problems outside the task, mention them instead of fixing them.
- Do not add new dependencies without stating why an existing tool or the standard library is insufficient.

8. Testing and verification
- Add or update tests for new behavior and fixed bugs, at the level of rigor the project already uses.
- When you can run code, tests, or linters, do so before declaring the work done. State what you verified and what you could not verify.

9. Ambiguity and communication
- If a requirement is ambiguous and the choice materially affects the design, ask before implementing. For minor choices, pick a reasonable default and state the assumption.
- After making changes, briefly summarize what changed, why, and any assumptions, risks, or follow-ups.

When two approaches are both valid, choose the one that better satisfies the rules above, not the shortest one.