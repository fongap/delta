# Delta Development Rules

Delta is a personal AI work assistant built on OpenWorker.

## Core Principles

1. Preserve existing working functionality unless the task explicitly requires changing it.
2. Preserve upstream compatibility and syncability where practical.
3. Follow the existing architecture before introducing new structures.
4. Record major architectural changes and important deviations from upstream.
5. Read `UPSTREAM.md` before making significant changes to OpenWorker-derived core code.
6. Prefer the smallest correct change over broad refactoring.
7. Do not modify unrelated code while completing a task.

## Code Placement Rules

The user should not need to decide where code belongs.

Before modifying or creating code, inspect the existing implementation and determine the correct module based on the current architecture.

General rules:

- Delta-specific core capabilities → `delta/`
- UI and interaction changes → `surfaces/gui/`
- OpenWorker-derived core fixes → `coworker/`
- Speech-related code → `stt/`
- Packaging and distribution → `packaging/`
- Tests → `tests/`

Do not create duplicate implementations merely to avoid modifying upstream-derived code.

Do not move existing modules only to make the directory structure cleaner.

Do not create new top-level directories unless there is a clear architectural need.

If placement is ambiguous:

1. Inspect the existing implementation.
2. Trace the relevant dependencies and call paths.
3. Follow the current architecture where reasonable.
4. Choose the smallest-change solution.
5. Briefly explain the placement decision in the final report.

Do not ask the user to choose a directory unless there is a genuine architectural tradeoff.

## Execution Rules

Before making changes:

1. Understand the user's actual goal.
2. Inspect the relevant existing code.
3. Identify the smallest set of files that needs to change.
4. Check for existing implementations before creating new ones.

During implementation:

- Stay focused on the requested task.
- Do not perform unrelated cleanup or refactoring.
- Do not rewrite working code merely because another design appears cleaner.
- Do not repeatedly search or inspect the same files without a concrete reason.
- Do not repeatedly apply speculative fixes.
- If an approach fails, diagnose the cause before trying another approach.
- Preserve existing behavior unless changing it is part of the task.

After implementation:

1. Run the most relevant tests.
2. Run typecheck/lint/build when applicable.
3. Fix failures caused by the current changes.
4. Do not expand into unrelated pre-existing failures.
5. Stop when the requested task is complete and verification passes.

## Anti-Loop Rules

Do not get stuck in repetitive investigate → modify → test cycles.

If the same problem persists after two reasonable attempts:

1. Stop making speculative changes.
2. Re-evaluate the root cause.
3. Inspect logs, errors, dependencies, and assumptions.
4. Choose a different evidence-based approach.

Do not keep making changes simply to improve metrics, formatting, architecture, or test coverage after the requested task has already been completed.

## Completion

A task is complete when:

- the requested behavior is implemented;
- relevant tests pass;
- the build/typecheck passes when applicable;
- no known regression caused by the change remains.

Then stop and report:

- what changed;
- which files changed;
- how it was verified;
- any remaining known risks.
