# ADR: Durable Run Event Ledger + Audit Hash Chain (ARCH-003)

- Status: Proposed
- Date: 2026-08-25
- Scope: run persistence, recovery, audit integrity

## Context

Run state today lives in two disconnected places: the live session transcript
(`SessionManager` + engine buffers) and a thin audit table (`core/audit.py`,
`audit_events`: timestamp/session/tool/approval/args/result_preview — no chaining, no
run grouping). Recovery exists only as automation durable-resume. Consequences:

- A crash mid-turn leaves no durable record of what the turn did before dying.
- "How was this report produced?" cannot be answered from data; only prose in messages.
- Audit rows are append-only but tamper-evident by nothing.

Upstream-style full event sourcing (persist every token delta) is explicitly rejected:
Delta is an office product, not a harness debugger. The unit of truth is the
**semantic event**, not the chunk.

## Decision

### 1. Two event classes

```text
Transient (in-memory / WS only)          Durable (append-only ledger)
─────────────────────────────────        ─────────────────────────────────
message.delta                            run.started / run.steered / run.interrupted
reasoning.delta                          input.accepted / plan.accepted
progress / typing                        tool.proposed / approval.requested / resolved
download progress                        tool.completed / failed
                                         source.used / artifact.created
                                         checkpoint.created / compensation.recorded
                                         process.spawned / process.killed
                                         run.completed / run.failed
```

Only durable events hit disk. LLM history remains derivable from persisted messages;
the ledger records *what happened*, not every token.

### 2b. Process events reach the ledger without signature threading

Background-process lifecycle (`run_in_background` spawns, `shell_task_kill`, managed
teardown kills) happens deep below the Runtime Adapter, in the executor. Instead of
threading `run_id` through `build_engine → shell_tools → executor` signatures, the
adapter publishes the active `(run_id, session_id)` into an ambient context variable
(`core/runscope.py`) for the duration of each driven turn; `asyncio.to_thread`
copies contexts, so the executor observes the scope with no API changes. The executor
stays ledger-agnostic: it reports structured facts to an injected sink
(`process_event_sink`), and the manager decides persistence:

- inside a run → durable `process.spawned` / `process.killed` ledger rows (actor
  "tool"), payloads scrubbed by the shared sanitizer;
- outside any run (session/app teardown kills) → session-scoped audit rows instead,
  since the hash-chained narrative is per-run and must not grow synthetic pseudo-runs.

Detached tasks surviving a restart are recorded as spawned-with-`detach:true`; a full
OS-level process table for cross-restart liveness tracking remains future work.

### 2. Ledger shape

Append-only table `run_events`:

```text
id, run_id, seq, type, ts, actor (user|agent|tool|system),
payload (safe JSON), prev_hash, hash
hash = sha256(prev_hash || seq || type || ts || actor || canonical(payload))
```

Rules:

- **Secrets never enter payload** (reuse `_SECRET_KEYS` scrubbing from audit.py).
- Large results are referenced, not embedded: `{artifact_id, sha256}` or
  `{source_id, cited_ranges}`.
- One writer per run (the Runtime Adapter's event sink) to keep the chain trivially
  serializable.

### 3. Crash recovery = synthetic terminal events

If a run has no terminal event (`run.completed|failed|interrupted`), cold recovery
appends `run.interrupted {reason: crashed}` — preserving whatever durable prefix
exists, mirroring upstream session-log practice. Resume then continues from the last
`checkpoint.created` instead of replaying blind.

### 4. Existing audit becomes a projection

`audit_events` stays for compatibility and gains `prev_hash/hash` itself (its own
chain over its own rows). `run_events` is the authoritative narrative; audit rows are
a security-oriented projection of tool/approval events. The two chains are independent
— cross-referenced by `run_id` and event ids, never merged.

### 5. Storage layout (unchanged principle: run process is event-sourced, the product is not)

```text
SQLite projections   tasks/runs/sources/artifacts/settings/search index
Run Event Ledger     append-only run_events (+ audit_events)
Filesystem           artifacts/ · source-cache/ · workspace/
Secret Store         unchanged, separate
```

## Non-goals

- No token-chunk durability, no replay-to-restore-context (compaction handles context).
- No distributed/witness anchoring in v1 — local tamper-evidence only.

## Consequences

- Checkpoint/Resume and Compensation gain their factual substrate (feeds ARCH-002).
- UI can render honest run history ("what did this run do?") from durable events alone.
- The Runtime Adapter contract must expose an ordered event sink — this becomes a
  required method on the RuntimePort, reinforcing ARCH-001/002 boundaries.
