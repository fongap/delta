"""Run Event Ledger — the append-only factual record of what each run did.

Implements docs/run-ledger-adr.md slice 1:

- one row per durable event; rows are hash-chained per run
  (hash = sha256(prev_hash | seq | type | actor | ts | canonical payload))
- secrets never enter payloads: every append is scrubbed through the shared
  SensitiveDataSanitizer (src/delta/sanitize.py) before hashing/storage — callers
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
from typing import Any, Iterable, Optional

TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.interrupted"})


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
                hash TEXT NOT NULL
            )
            """)
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
    ) -> dict[str, Any]:
        """Append one event, extending the run's hash chain. Returns the stored row.
        The payload is scrubbed through the shared sanitizer before it is hashed and
        stored — the chain is computed over exactly what persists."""
        ts = time.time() if ts is None else ts
        from .sanitize import sanitize_payload

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
                INSERT INTO run_events (run_id, seq, type, ts, actor, payload, prev_hash, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        return {
            "run_id": r["run_id"],
            "seq": r["seq"],
            "type": r["type"],
            "ts": r["ts"],
            "actor": r["actor"],
            "payload": json.loads(r["payload"] or "{}"),
            "prev_hash": r["prev_hash"],
            "hash": r["hash"],
        }
