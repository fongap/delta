"""Run Event Ledger — the append-only factual record of what each run did.

Implements docs/architecture/adr/ADR-001-run-event-ledger.md slice 1:

- one row per durable event; rows are hash-chained per run
  (hash = sha256(prev_hash | seq | type | actor | ts | canonical payload))
- secrets never enter payloads: every append is scrubbed through the shared
  SensitiveDataSanitizer (packages/sanitize.py) before hashing/storage — callers
  are still expected to pass clean payloads, but the ledger no longer trusts them
- large results are referenced by id/sha256, never embedded
- crash recovery: a run without a terminal event gets a synthetic
  `run.interrupted {reason: crashed}` on cold start, preserving its durable prefix

The ledger records what happened. It is not queried to reconstruct LLM context,
and it is not the product's settings store — the rest of the system stays
projection-shaped.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from packages.storage_authority import is_rust_authority  # noqa: F401

TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.interrupted"})

# Event vocabulary (ADR-005). All `tool.*` / `approval.*` / `artifact.*` / `validation.*`
# / `side_effect.*` / `run.resumed` events flow through the same hash chain and use the
# same payload scrubbing as `run.*` events. Sanitizer is the single source of truth.
KNOWN_EVENT_TYPES = frozenset(
    {
        # Run lifecycle
        "run.started",
        "run.completed",
        "run.failed",
        "run.interrupted",
        "run.resumed",
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


def _canonical(payload: Any) -> str:
    return json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)


class RunEventLedger:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                ts REAL NOT NULL,
                actor TEXT NOT NULL DEFAULT 'system',
                payload TEXT NOT NULL DEFAULT '{}',
                prev_hash TEXT NOT NULL DEFAULT '',
                hash TEXT NOT NULL,
                workspace TEXT
            )
            """)
        # Per ADR-005 chain contract: ``workspace`` is a denormalized index
        # hint for cross-workspace queries (e.g. P3 Run Analyzer) and is
        # NOT part of the hash basis — adding the column is therefore
        # backward compatible with rows written before the column
        # existed (their chain still verifies). The migration below is
        # idempotent: a fresh DB gets the column via CREATE TABLE; an
        # existing DB gets it via ALTER TABLE once, then the OperationalError
        # is swallowed on subsequent boots.
        for ddl in (
            "ALTER TABLE run_events ADD COLUMN workspace TEXT",
            "CREATE INDEX IF NOT EXISTS idx_run_events_workspace "
            "ON run_events(workspace, run_id, seq)",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, seq)"
        )
        self._conn.commit()

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
        """Append one event, extending the run's hash chain. Returns the stored row.
        The payload is scrubbed through the shared sanitizer before it is hashed and
        stored — the chain is computed over exactly what persists.

        ``workspace`` is a denormalized index hint recorded on the row so
        the P3 Run Analyzer (and any other cross-workspace query) can
        scope to a workspace without re-deriving it from
        ``payload.workspace``. It is **not** part of the hash basis
        (ADR-005: the chain is the durable fact; the column is a query
        accelerator) — a row's workspace can be backfilled without
        breaking ``verify()``.
        """
        ts = time.time() if ts is None else ts
        from packages.sanitize import sanitize_payload

        stored_payload = sanitize_payload(payload)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT seq, hash FROM run_events WHERE run_id = ? "
                "ORDER BY seq DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            seq = (row["seq"] + 1) if row else 1
            prev_hash = row["hash"] if row else ""
            basis = "|".join(
                [prev_hash, str(seq), type, actor, repr(ts), _canonical(stored_payload)]
            )
            digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
            self._conn.execute(
                """
                INSERT INTO run_events (run_id, seq, type, ts, actor, payload, prev_hash, hash, workspace)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    seq,
                    type,
                    ts,
                    actor,
                    _canonical(stored_payload),
                    prev_hash,
                    digest,
                    workspace,
                ),
            )
        return {
            "run_id": run_id,
            "seq": seq,
            "type": type,
            "ts": ts,
            "actor": actor,
            "payload": stored_payload or {},
            "prev_hash": prev_hash,
            "hash": digest,
            "workspace": workspace,
        }

    def events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [self._as_dict(r) for r in rows]

    def events_in_workspace(
        self, run_id: str, workspace: str
    ) -> list[dict[str, Any]]:
        """Read the run's events filtered by ``workspace``.

        Returns the same shape as :meth:`events` but only rows whose
        ``workspace`` column equals the bound ``workspace``. Pushed to
        SQL so the planner can use the ``idx_run_events_workspace
        (workspace, run_id, seq)`` compound index (added in ADR-007
        §10.6 step 1) instead of doing the filter in Python after a
        full ledger read.

        ``workspace=""`` matches rows whose column is NULL (the legacy
        pre-migration state, which ``_as_dict`` reports as ``""``) —
        the SQL ``=`` on NULL would otherwise be a no-match. We translate
        the empty-string sentinel to ``IS NULL`` so callers asking
        "show me the pre-migration events" still get them.
        """
        if workspace:
            rows = self._conn.execute(
                "SELECT * FROM run_events "
                "WHERE run_id = ? AND workspace = ? ORDER BY seq",
                (run_id, workspace),
            ).fetchall()
        else:
            # Empty string → NULL on disk (legacy rows); the column's
            # default semantics treat "" and NULL as the same "no
            # workspace" sentinel in the Python view.
            rows = self._conn.execute(
                "SELECT * FROM run_events "
                "WHERE run_id = ? AND (workspace IS NULL OR workspace = '') "
                "ORDER BY seq",
                (run_id,),
            ).fetchall()
        return [self._as_dict(r) for r in rows]

    def runs(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT run_id FROM run_events ORDER BY rowid"
        ).fetchall()
        return [r["run_id"] for r in rows]

    def close(self) -> None:
        self._conn.close()

    def open_runs(self) -> list[str]:
        """Run ids that have at least one event but no terminal event."""
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT run_id FROM run_events
            WHERE run_id NOT IN (
                SELECT run_id FROM run_events WHERE type IN
                ({",".join("?" for _ in TERMINAL_EVENTS)})
            )
            """,
            tuple(sorted(TERMINAL_EVENTS)),
        ).fetchall()
        return [r["run_id"] for r in rows]

    def run_status(self, run_id: str) -> str:
        """Derive a run's lifecycle status from the ledger.

        The ledger is the single source of truth for run lifecycle. This
        function reads the run's terminal event and maps it to the
        UI-facing ``status`` vocabulary consumed by ``TaskRun.status``,
        the analyzer, and the Inbox.

        Mapping:

        * no events         → ``"unknown"`` (run was never started)
        * has start, no end → ``"running"`` (or ``"resumed"`` after a
                               ``run.resumed``)
        * ``run.completed``  → ``"ok"``
        * ``run.failed``     → ``"error"``
        * ``run.interrupted`` → ``"interrupted"`` (crash recovery)
        * ``validation.failed`` last (no run.completed) → ``"validation_failed"``
        * any other state    → ``"unknown"``

        R1 contract (P1-C): callers must read this rather than trusting
        ``TaskRun.status``, which is a denormalized convenience copy.
        """
        if not run_id:
            return "unknown"
        with self._lock:
            row = self._conn.execute(
                "SELECT type, seq FROM run_events "
                "WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return "unknown"
        event_type = row["type"]
        if event_type == "run.completed":
            return "ok"
        if event_type == "run.failed":
            return "error"
        if event_type == "run.interrupted":
            return "interrupted"
        if event_type in ("run.started", "run.resumed"):
            return "running" if event_type == "run.started" else "resumed"
        if event_type == "validation.failed":
            return "validation_failed"
        return "unknown"

    def derive_run_status(self, run_id: str, fallback: str | None = None) -> str:
        """P0-4: Derive the authoritative run status from the ledger.

        The ledger is the single source of truth for run lifecycle. This
        method is the canonical entry point for production read paths
        that previously trusted the denormalized ``TaskRun.status``
        column. Callers MUST use this rather than reading
        ``run.status`` directly when the truth matters (analyzer,
        run-state checks, UI status badges, reference harness).

        Coverage (R1.5 contract):

        * ``running``  — ``run.started`` is the last event
        * ``ok``       — ``run.completed`` is the last event
        * ``error``    — ``run.failed`` is the last event
        * ``interrupted`` — ``run.interrupted`` is the last event
          (crash recovery, see :meth:`recover_stale`)
        * ``validation_failed`` — ``validation.failed`` is the last
          event with no prior ``run.completed``
        * ``unknown``  — no events (the run was never started)
        * ``resumed``  — ``run.resumed`` is the last event

        The ``fallback`` is consulted only when the ledger returns
        ``"unknown"`` (no events at all). This covers ``skipped``
        runs that never wrote to the ledger, and legacy rows
        predating the ledger migration.
        """
        status = self.run_status(run_id)
        if status == "unknown" and fallback:
            return fallback
        return status

    def recover_stale(self) -> Iterable[dict[str, Any]]:
        """Cold-start sweep: close every open run with a synthetic interrupted event."""
        recovered = []
        for run_id in self.open_runs():
            last = self.events(run_id)[-1] if self.events(run_id) else None
            recovered.append(
                self.append(
                    run_id,
                    "run.interrupted",
                    actor="system",
                    payload={"reason": "crashed", "last_event_seq": last["seq"] if last else 0},
                )
            )
        return recovered

    def verify(self, run_id: str) -> bool:
        """Recompute the chain for one run; True iff every link matches."""
        prev = ""
        for row in self.events(run_id):
            basis = "|".join(
                [
                    prev,
                    str(row["seq"]),
                    row["type"],
                    row["actor"],
                    repr(row["ts"]),
                    _canonical(row["payload"]),
                ]
            )
            if hashlib.sha256(basis.encode("utf-8")).hexdigest() != row["hash"]:
                return False
            if row["prev_hash"] != prev:
                return False
            prev = row["hash"]
        return True

    @staticmethod
    def _as_dict(r: sqlite3.Row) -> dict[str, Any]:
        d = {
            "run_id": r["run_id"],
            "seq": r["seq"],
            "type": r["type"],
            "ts": r["ts"],
            "actor": r["actor"],
            "payload": json.loads(r["payload"] or "{}"),
            "prev_hash": r["prev_hash"],
            "hash": r["hash"],
        }
        # workspace is NULL on rows written before ADR-007's column
        # migration (§10.6 path: old → new). Report it as "" so callers
        # never KeyError when filtering — the empty string is the same
        # sentinel Analyzer._check_task_workspace rejects.
        try:
            d["workspace"] = r["workspace"] or ""
        except (IndexError, KeyError):
            d["workspace"] = ""
        return d
