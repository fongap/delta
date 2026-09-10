# ADR-042: Run Lifecycle Transition Authority

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-11 |
| **Related** | ADR-041 (R4 Plan), ADR-040 (R3 Final Convergence), ADR-025 (R1 Final Convergence) |

## Context

R3 (Execution Lifecycle) completed with all tool-call lifecycle decisions in Rust
(ADR-040). The remaining runtime control authority in R4 includes run lifecycle
state transitions (`run.started` / `run.resumed` / `run.completed` / `run.failed` /
`run.interrupted`).

**Pre-migration state**: Python `runtime.py:_track` (lines 307–325) emitted
run lifecycle events via `ledger.append()` — Python decided *when* to transition,
Rust merely stored the event. No state-machine validation existed: illegal
sequences (e.g. `run.completed` on an already-`run.interrupted` run) were
silently accepted.

The run-event ledger (`run-events.db`) is already Rust-authoritative for
persistence and crash recovery (`ledger.recover_stale`). The gap was the
*transition decision* — the state-machine logic that validates whether a
lifecycle event is legal.

## Decision

Move run lifecycle transition authority to Rust via a new `run.transition`
command (protocol 12 → 13).

### Rust state machine (in `LedgerWriter::transition`)

```
unknown   ──run.started──▶ running
running   ──run.resumed──▶ running          (multi-turn resume)
running   ──run.completed──▶ completed      (terminal)
running   ──run.failed─────▶ failed         (terminal)
running   ──run.interrupted▶ interrupted    (terminal, crash recovery)
running   ──run.skipped────▶ skipped        (terminal)
running   ──run.cancelled──▶ cancelled      (terminal)
resumed   ──(same as running)─────────────▶ terminal
terminal  ──any──────────────────────────▶ rejected
```

### Rules

- `run.started` legal only from `unknown` (no prior events for this `run_id`).
- `run.resumed` legal from `running`, `resumed`, or `interrupted`
  (crash recovery: resume an interrupted run).
- `run.completed` / `run.failed` / `run.interrupted` / `run.skipped` /
  `run.cancelled` legal only from `running` or `resumed` (not terminal).
- Any transition from a terminal state (`completed` / `failed` /
  `interrupted` / `skipped` / `cancelled`) is rejected.
- Non-`run.*` event types rejected (delegates to `ledger.append`).

### Python changes

- `RunEventLedger.transition()` added — same signature as `append()`,
  delegates to `run.transition`.
- `TurnEngineAdapter._track()` now calls `transition()` for all
  `run.*` events. Non-`run.*` events (tool/validation/side-effect) still use
  `append()`.
- For explicit `run_id` + `resume()`, `_track` queries ledger status:
  - `status in ("running", "resumed", "interrupted")` → emit `run.resumed`
  - `unknown` or terminal → emit `run.started` (fresh turn).
- After terminal event (`run.completed` / `run.failed`), `self._run_id`
  cleared so subsequent turns mint new `run_id`s.

### run_status fix (concurrent)

The ledger's `run_status` derivation was corrected to scan backwards and
return the **first lifecycle-determining event** (terminal or `run.started`/
`run.resumed`), skipping non-lifecycle events (tool/process). This fixes
a pre-existing bug where `run.interrupted` masked a later `run.resumed`.

## Protocol Version

12 → 13 (new `run.transition` command).

## Migration Status

Hard-cut: Python `runtime.py:_track` switched to `run.transition` for all
`run.*` events. No fallback, no dual-write path. `ledger.append` remains
for non-`run.*` events.

## Rollback

Git revert. No data migration (schema unchanged).

## Tests

- `tests/test_run_transition_authority.py` — 14 tests covering legal/
  illegal transitions, hash chain integrity, non-`run.*` passthrough.
- `tests/test_recovery_production_wiring.py` — 12 tests including
  `run.resumed` from `interrupted`, fresh-resume-as-started.
- All existing run/ledger/status tests pass (121 tests).
- Full suite: 1733 pass, 25 fail (pre-existing MCP import error only).

## Consequences

- Run lifecycle state machine now enforced by Rust — illegal transitions
  fail-closed at the authority boundary.
- Crash recovery (`recover_stale` → `run.interrupted` → `run.resumed`)
  is now validated by the same state machine.
- Protocol 13 required; `hello` handshake enforces version match.