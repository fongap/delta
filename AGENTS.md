# Agent Instructions

Delta is a personal AI work assistant built on OpenWorker.

## Core Principles

- Understand the requested goal before modifying code.
- Preserve existing working behavior unless the task requires changing it.
- Prefer the smallest correct change.
- Do not modify unrelated code.
- Do not perform speculative refactoring or cleanup.
- Preserve upstream compatibility where practical.
- Read `UPSTREAM.md` before making significant changes to OpenWorker-derived core code.

## Before Modifying Code

1. Read the relevant project documentation when needed.
2. Inspect the existing implementation before proposing changes.
3. Trace the relevant dependencies and call paths.
4. For bug fixes, reproduce or establish evidence of the bug when practical.
5. Check whether an implementation already exists before creating a new one.
6. Identify the smallest set of files required for the task.

Do not start broad refactoring merely because the existing structure could be cleaner.

## Code Placement Rules

The user should not need to decide where code belongs.

Before modifying or creating code, determine the correct module from the existing architecture.

General rules:

- Delta-specific core capabilities → `delta/`
- UI and interaction changes → `surfaces/gui/`
- OpenWorker-derived core fixes → `coworker/`
- Speech-related code → `stt/`
- Packaging and distribution → `packaging/`
- Tests → `tests/`

Do not create duplicate implementations merely to avoid modifying upstream-derived code.

Do not move existing modules only for directory cleanliness.

Do not create a new top-level directory unless there is a clear architectural need.

If placement is ambiguous:

1. Inspect the existing implementation.
2. Trace the relevant dependency structure.
3. Follow the current architecture where reasonable.
4. Choose the smallest-change solution.
5. Briefly explain the placement decision in the final report.

Do not ask the user to choose a directory unless there is a genuine architectural tradeoff.

## Implementation Rules

During implementation:

- Stay within the requested scope.
- Do not rewrite working code simply because another design appears cleaner.
- Do not perform unrelated formatting or cleanup.
- Do not repeatedly inspect the same files without a concrete reason.
- Do not repeatedly apply speculative fixes.
- Preserve existing APIs and behavior unless changing them is necessary.
- Reuse existing abstractions before introducing new ones.

If a broader architectural change becomes necessary, explain why before expanding the scope.

## Verification

After modifying code, run the verification that is relevant to the change.

Depending on the affected area, this may include:

1. Relevant unit or integration tests.
2. Typecheck.
3. Lint.
4. Build.
5. Targeted E2E or regression tests.

Start with the smallest relevant verification.

Do not automatically run the entire test suite when a targeted test is sufficient.

Fix failures caused by the current changes.

Do not expand the task into unrelated pre-existing failures.

## Anti-Loop Rules

Do not get stuck in repetitive investigate → modify → test cycles.

If the same issue persists after two reasonable attempts:

1. Stop making speculative changes.
2. Reassess the root cause.
3. Review the actual error, logs, dependencies, and assumptions.
4. Choose a different evidence-based approach.

Do not keep changing code merely to improve architecture, formatting, coverage, or metrics after the requested task is already complete.

Do not continuously add tests for edge cases unrelated to the requested behavior.

## Completion Rules

A task is complete when:

- the requested behavior is implemented;
- relevant verification passes;
- no regression caused by the change is known;
- the requested scope has been satisfied.

Once these conditions are met, stop.

Do not continue improving unrelated parts of the project.

## Final Report

Report only:

- what changed;
- which files changed;
- how the change was verified;
- any unresolved risks or known limitations.

Keep the final report concise.
