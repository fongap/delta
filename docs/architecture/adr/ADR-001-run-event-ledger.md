# ADR-001 Run Event Ledger

**Status:** Active

## Context

Delta needs an immutable, append‑only record of everything that happens during a run to support crash recovery, auditability, and reproducible facts. The ledger must:
- Store one row per durable event.
- Hash‑chain rows per run (`sha256(prev_hash|seq|type|actor|ts|canonical payload)`).
- Sanitize payloads via `packages.sanitize` before hashing.
- Provide a synthetic `run.interrupted` event for any run left without a terminal event on cold‑start.
- Remain read‑only for consumers; queries are performed by `AuditStore`.

## Decision

Implemented in `core/ledger.py` as `RunEventLedger` with the schema defined in `run_events` table. The class offers:
- `append(run_id, type, ...)` – appends and returns the stored row.
- `events(run_id)` – returns ordered events.
- `open_runs()` – detects runs missing terminal events.
- `recover_stale()` – creates synthetic `run.interrupted` events on startup (used in `services/server/run.py`).
- Verification via `verify(run_id)` to recompute and validate the hash chain.

All callers (`core/runtime.py`, `services/server/manager_sessions.py`, tests) now reference `docs/run-ledger-adr.md` contract.

## Consequences

- Guarantees an append‑only factual source.
- Enables deterministic crash recovery.
- Provides a stable contract for downstream components (`AuditStore`, UI).