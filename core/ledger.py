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
import time
from pathlib import Path
from typing import Any, Iterable

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
        # Side-effect idempotency (WS4)
        "side_effect.committed",
        "side_effect.replayed",
        "side_effect.uncommitted",
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
        with self._conn:
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
