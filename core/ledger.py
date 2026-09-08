"""Run Event Ledger — Rust-authoritative append-only factual record.

ADR-023 R1 Ledger Hard-Cut: Rust ``delta_core`` is the sole authority for
all ledger writes and reads.  Python retains only the public adapter used
by the current runtime and the stable value types from the v0.3.2 contract.

R1 Final Convergence (ADR-025): No Python fallback exists.  The ledger is
the sole source of truth for run state.  ``TaskRun.status`` is never
consulted by production control flow.

Implements docs/architecture/adr/ADR-001-run-event-ledger.md slice 1:

- one row per durable event; rows are hash-chained per run
  (hash = sha256(prev_hash | seq | type | actor | ts | canonical payload))
- secrets never enter payloads: every append is scrubbed through the shared
  SensitiveDataSanitizer (packages/sanitize.py) before hashing/storage
- large results are referenced by id/sha256, never embedded
- crash recovery: a run without a terminal event gets a synthetic
  ``run.interrupted {reason: crashed}`` on cold start, preserving its
  durable prefix
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Iterable

from packages.delta_core_client import DeltaCoreError, default_client

TERMINAL_EVENTS = frozenset({
    "run.completed",
    "run.failed",
    "run.interrupted",
    "run.skipped",
    "run.cancelled",
})

# Event vocabulary (ADR-005).  All ``tool.*`` / ``approval.*`` / ``artifact.*``
# / ``validation.*`` / ``side_effect.*`` / ``run.resumed`` events flow through
# the same hash chain and use the same payload scrubbing as ``run.*`` events.
KNOWN_EVENT_TYPES = frozenset(
    {
        # Run lifecycle
        "run.started",
        "run.completed",
        "run.failed",
        "run.interrupted",
        "run.resumed",
        "run.skipped",
        "run.cancelled",
        # Tool calls
        "tool.proposed",
        "tool.started",
        "tool.finished",
        "tool.denied",
        # Approval flow
        "approval.requested",
        "approval.granted",
        "approval.denied",
        # Artifacts (WS2)
        "artifact.registered",
        "artifact.completed",
        # Validation (WS3)
        "validation.started",
        "validation.passed",
        "validation.failed",
        # Side-effect crash safety (WS4 / P0-A)
        "side_effect.planned",
        "side_effect.committed",
        "side_effect.replayed",
        "side_effect.failed",
        "side_effect.uncertain",
        "side_effect.uncommitted",  # legacy alias (pre-P0A vocabulary)
    }
)


class RunEventLedger:
    """Thin Python API over the single Rust ledger authority.

    Every method delegates to ``delta_core`` via the process-wide
    :func:`~packages.delta_core_client.default_client`.  The SQLite
    database is opened and managed entirely by the Rust side; Python
    never touches it directly.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(self.db_path)
        self._lock = threading.RLock()
        # Schema creation (CREATE TABLE IF NOT EXISTS) is handled by the
        # Rust LedgerWriter::open on first command — no Python init needed.

    def _invoke(self, cmd: str, **kwargs: Any) -> Any:
        """Send one command to delta_core and return the result."""
        return default_client().command({"cmd": cmd, "db": self._db_path, **kwargs})

    def append(
        self,
        run_id: str,
        type: str,
        *,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
        ts: float | None = None,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Append one event, extending the run's hash chain.

        The payload is scrubbed through the shared sanitizer before it
        is hashed and stored — the chain is computed over exactly what
        persists.
        """
        ts = ts if ts is not None else time.time()
        from packages.sanitize import sanitize_payload

        stored_payload = sanitize_payload(payload)
        result = self._invoke(
            "ledger.append",
            run_id=run_id,
            type=type,
            actor=actor,
            ts=ts,
            payload=stored_payload,
            workspace=workspace or "",
        )
        if not isinstance(result, dict):
            raise DeltaCoreError("invalid ledger.append response")
        return result

    def events(self, run_id: str) -> list[dict[str, Any]]:
        """List all events for a run, ordered by seq."""
        result = self._invoke("ledger.events", run_id=run_id)
        if not isinstance(result, list):
            raise DeltaCoreError("invalid ledger.events response")
        return result

    def events_in_workspace(
        self, run_id: str, workspace: str
    ) -> list[dict[str, Any]]:
        """Read the run's events filtered by ``workspace``."""
        result = self._invoke(
            "ledger.events_in_workspace", run_id=run_id, workspace=workspace
        )
        if not isinstance(result, list):
            raise DeltaCoreError("invalid ledger.events_in_workspace response")
        return result

    def runs(self) -> list[str]:
        """List all run_ids that have at least one event."""
        result = self._invoke("ledger.runs")
        if not isinstance(result, list):
            raise DeltaCoreError("invalid ledger.runs response")
        return result

    def open_runs(self) -> list[str]:
        """Run ids that have at least one event but no terminal event."""
        result = self._invoke("ledger.open_runs")
        if not isinstance(result, list):
            raise DeltaCoreError("invalid ledger.open_runs response")
        return result

    def run_status(self, run_id: str) -> str:
        """Derive a run's lifecycle status from the ledger.

        Mapping:

        * no events         → ``"unknown"`` (run was never started)
        * has start, no end → ``"running"`` (or ``"resumed"`` after a
                               ``run.resumed``)
        * ``run.completed``  → ``"ok"``
        * ``run.failed``     → ``"error"``
        * ``run.interrupted`` → ``"interrupted"`` (crash recovery)
        * ``run.skipped``    → ``"skipped"``
        * ``run.cancelled``  → ``"cancelled"``
        * ``validation.failed`` last → ``"validation_failed"``
        * any other state    → ``"unknown"``
        """
        result = self._invoke("ledger.run_status", run_id=run_id)
        if not isinstance(result, dict) or "status" not in result:
            raise DeltaCoreError("invalid ledger.run_status response")
        return result["status"]

    def derive_run_status(self, run_id: str) -> str:
        """Derive the authoritative run status from the ledger.

        The ledger is the sole source of truth.  No fallback is
        consulted — ``"unknown"`` means the run has no ledger events.
        """
        return self.run_status(run_id)

    def recover_stale(self) -> Iterable[dict[str, Any]]:
        """Cold-start sweep: close every open run with a synthetic interrupted event."""
        result = self._invoke("ledger.recover_stale")
        if not isinstance(result, dict) or "events" not in result:
            raise DeltaCoreError("invalid ledger.recover_stale response")
        return result["events"]

    def verify(self, run_id: str) -> bool:
        """Recompute the chain for one run; True iff every link matches."""
        result = self._invoke("ledger.verify", run_id=run_id)
        if not isinstance(result, dict) or "valid" not in result:
            raise DeltaCoreError("invalid ledger.verify response")
        return bool(result["valid"])

    def close(self) -> None:
        """Release the SQLite handle for this ledger in the Rust ConnCache.

        Idempotent: calling multiple times is safe. After close, subsequent
        operations on this instance will reopen the handle transparently.
        """
        self._invoke("ledger.close")
