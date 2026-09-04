"""Side-effect crash-safety log — durable state machine for every consequential
tool call (ADR-005 WS4, P0-A Side Effect Crash Safety).

The blueprint requires:

  "Tool 已产生副作用但 Run 未结束 → 恢复后不重复执行"
  "Intent 已存在 / Committed 不存在 / 进程已重启 → Uncertain"

The log closes the crash window between *executing* a side effect and
*committing* its result. Each side effect moves through an explicit
state machine::

    Planned   → the engine decided to execute; intent persisted
    Executing → the tool is running right now (in-process)
    Committed → the tool returned ok; result + ledger event recorded
    Failed    → the tool raised; no side effect happened
    Uncertain → intent exists, no commit, process already restarted

Keyed by ``(run_id, tool_call_id)``. The ``args_sha256`` fingerprint defends
against argument mutation: if the persisted call's arguments differ from
the resumed call's, the log row is for a DIFFERENT call -> re-execute.

``operation_id`` is a stable idempotency key derived from
``(run_id, tool_call_id)`` so external APIs that support request-id
dedup get the SAME key across retries/restarts — not a fresh random id
on every attempt.
"""

from __future__ import annotations

import enum
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.ledger import RunEventLedger


def _canonical(args: Any) -> str:
    return json.dumps(args or {}, sort_keys=True, separators=(",", ":"), default=str)


def args_sha256(arguments: Any) -> str:
    """Stable fingerprint of tool arguments for idempotency dedupe."""
    return hashlib.sha256(_canonical(arguments).encode("utf-8")).hexdigest()


def operation_id(run_id: str, tool_call_id: str) -> str:
    """Stable idempotency key for external APIs.

    Derived from ``(run_id, tool_call_id)`` so the same logical operation
    carries the same key across retries and restarts — never a fresh
    random id per attempt.
    """
    raw = f"{run_id}:{tool_call_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:36]


class SideEffectState(str, enum.Enum):
    PLANNED = "planned"
    EXECUTING = "executing"
    COMMITTED = "committed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"

    @classmethod
    def from_str(cls, raw: str | None) -> "SideEffectState | None":
        if not raw:
            return None
        try:
            return cls(raw)
        except ValueError:
            return cls.COMMITTED


class IdempotencyLog:
    """Crash-safe state machine store for side effects.

    One row per ``(run_id, tool_call_id)``. The ``state`` column drives
    resume decisions:

    * ``committed``  -> replay the recorded result (side effect already happened)
    * ``planned`` / ``executing`` with no commit -> ``uncertain`` after restart
    * ``uncertain``   -> NEVER auto-replay; surface to the user
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS side_effects (
                run_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                args_sha256 TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                state TEXT NOT NULL DEFAULT 'committed',
                operation_id TEXT NOT NULL DEFAULT '',
                committed_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (run_id, tool_call_id)
            )
            """
        )
        for _col, ddl in (
            (
                "state",
                "ALTER TABLE side_effects ADD COLUMN state TEXT NOT NULL DEFAULT 'committed'",
            ),
            (
                "operation_id",
                "ALTER TABLE side_effects ADD COLUMN operation_id TEXT NOT NULL DEFAULT ''",
            ),
            (
                "updated_at",
                "ALTER TABLE side_effects ADD COLUMN updated_at REAL NOT NULL DEFAULT 0",
            ),
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_side_effects_state "
            "ON side_effects(state, run_id)"
        )
        self._conn.commit()

    # -- write paths -----------------------------------------------------------

    def record_planned(
        self,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Any,
        *,
        ledger: "RunEventLedger | None" = None,
        workspace: str | None = None,
    ) -> str | None:
        """Persist *intent* before the external operation begins.

        Writes a ``Planned`` row keyed by ``(run_id, tool_call_id)``. If
        the row already exists with the same args hash and a terminal
        state (committed/failed/uncertain), leave it — the intent was
        already resolved.

        Returns the ``operation_id`` for this side effect, or None if
        the inputs are empty.
        """
        if not run_id or not tool_call_id:
            return None
        sha = args_sha256(arguments)
        op_id = operation_id(run_id, tool_call_id)
        now = time.time()
        terminal = (
            SideEffectState.COMMITTED.value,
            SideEffectState.UNCERTAIN.value,
            SideEffectState.FAILED.value,
        )
        with self._lock:
            existing = self._conn.execute(
                "SELECT state, args_sha256 FROM side_effects "
                "WHERE run_id=? AND tool_call_id=?",
                (run_id, tool_call_id),
            ).fetchone()
            if (
                existing
                and existing["args_sha256"] == sha
                and existing["state"] in terminal
            ):
                return op_id
            self._conn.execute(
                """
                INSERT INTO side_effects
                    (run_id, tool_call_id, tool_name, args_sha256, result_json,
                     state, operation_id, committed_at, updated_at)
                VALUES (?, ?, ?, ?, '{}', ?, ?, 0, ?)
                ON CONFLICT(run_id, tool_call_id) DO UPDATE SET
                    tool_name=excluded.tool_name,
                    args_sha256=excluded.args_sha256,
                    state=excluded.state,
                    operation_id=excluded.operation_id,
                    updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    tool_call_id,
                    tool_name,
                    sha,
                    SideEffectState.PLANNED.value,
                    op_id,
                    now,
                ),
            )
            self._conn.commit()
        if ledger is not None:
            try:
                ledger.append(
                    run_id,
                    "side_effect.planned",
                    actor="system",
                    payload={
                        "tool_call_id": tool_call_id,
                        "tool": tool_name,
                        "args_sha256": sha,
                        "operation_id": op_id,
                    },
                    workspace=workspace or None,
                )
            except Exception:
                pass
        return op_id

    def mark_executing(
        self,
        run_id: str,
        tool_call_id: str,
        *,
        ledger: "RunEventLedger | None" = None,
        workspace: str | None = None,
    ) -> None:
        """Transition a Planned side effect to Executing."""
        if not run_id or not tool_call_id:
            return
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE side_effects SET state=?, updated_at=? "
                "WHERE run_id=? AND tool_call_id=? AND state=?",
                (
                    SideEffectState.EXECUTING.value,
                    now,
                    run_id,
                    tool_call_id,
                    SideEffectState.PLANNED.value,
                ),
            )
            self._conn.commit()

    def commit(
        self,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Any,
        result: Any,
        *,
        ledger: "RunEventLedger | None" = None,
        workspace: str | None = None,
    ) -> None:
        """Record that a side effect committed for ``(run_id, tool_call_id)``.

        Transitions (or inserts) the row to ``Committed`` state with the
        recorded result. Overwrites on duplicate (idempotent re-commit is
        fine). Emits a ``side_effect.committed`` ledger event when a ledger
        is provided.
        """
        if not run_id or not tool_call_id:
            return
        sha = args_sha256(arguments)
        op_id = operation_id(run_id, tool_call_id)
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO side_effects
                    (run_id, tool_call_id, tool_name, args_sha256, result_json,
                     state, operation_id, committed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, tool_call_id) DO UPDATE SET
                    tool_name=excluded.tool_name,
                    args_sha256=excluded.args_sha256,
                    result_json=excluded.result_json,
                    state=excluded.state,
                    operation_id=excluded.operation_id,
                    committed_at=excluded.committed_at,
                    updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    tool_call_id,
                    tool_name,
                    sha,
                    _canonical(result),
                    SideEffectState.COMMITTED.value,
                    op_id,
                    now,
                    now,
                ),
            )
            self._conn.commit()
        if ledger is not None:
            try:
                ledger.append(
                    run_id,
                    "side_effect.committed",
                    actor="system",
                    payload={
                        "tool_call_id": tool_call_id,
                        "tool": tool_name,
                        "args_sha256": sha,
                        "operation_id": op_id,
                    },
                    workspace=workspace or None,
                )
            except Exception:
                pass

    def mark_failed(
        self,
        run_id: str,
        tool_call_id: str,
        error: str,
        *,
        ledger: "RunEventLedger | None" = None,
        workspace: str | None = None,
    ) -> None:
        """Transition a side effect to Failed (the tool raised; no real
        side effect happened)."""
        if not run_id or not tool_call_id:
            return
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE side_effects SET state=?, result_json=?, updated_at=? "
                "WHERE run_id=? AND tool_call_id=? AND state IN (?,?)",
                (
                    SideEffectState.FAILED.value,
                    _canonical({"error": error}),
                    now,
                    run_id,
                    tool_call_id,
                    SideEffectState.PLANNED.value,
                    SideEffectState.EXECUTING.value,
                ),
            )
            self._conn.commit()
        if ledger is not None:
            try:
                ledger.append(
                    run_id,
                    "side_effect.failed",
                    actor="system",
                    payload={
                        "tool_call_id": tool_call_id,
                        "error": error,
                    },
                    workspace=workspace or None,
                )
            except Exception:
                pass

    def mark_uncertain(
        self,
        run_id: str,
        tool_call_id: str,
        *,
        ledger: "RunEventLedger | None" = None,
        workspace: str | None = None,
    ) -> None:
        """Transition a non-committed side effect to Uncertain.

        Called during cold-start recovery when a side effect was Planned
        or Executing but the run was interrupted by a crash. Uncertain
        side effects must NEVER be auto-replayed; the user must resolve
        them (confirm success, re-execute, or mark as failed).
        """
        if not run_id or not tool_call_id:
            return
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE side_effects SET state=?, updated_at=? "
                "WHERE run_id=? AND tool_call_id=? AND state IN (?,?)",
                (
                    SideEffectState.UNCERTAIN.value,
                    now,
                    run_id,
                    tool_call_id,
                    SideEffectState.PLANNED.value,
                    SideEffectState.EXECUTING.value,
                ),
            )
            self._conn.commit()
        if ledger is not None:
            try:
                row = self._row(run_id, tool_call_id)
                ledger.append(
                    run_id,
                    "side_effect.uncertain",
                    actor="system",
                    payload={
                        "tool_call_id": tool_call_id,
                        "tool": row["tool_name"] if row else "unknown",
                        "operation_id": row["operation_id"] if row else "",
                    },
                    workspace=workspace or None,
                )
            except Exception:
                pass

    # -- read paths ------------------------------------------------------------

    def lookup(
        self,
        run_id: str,
        tool_call_id: str,
        arguments: Any,
    ) -> dict[str, Any] | None:
        """Return the stored result for a (run_id, tool_call_id) replay, or
        None if the call is fresh (must execute). A stored row whose
        ``args_sha256`` differs from the resumed call's arguments is treated
        as a DIFFERENT call -> returns None -> engine re-executes.

        A row in ``uncertain`` state returns a dict with ``state="uncertain"``
        and no result — the engine must NOT replay it; it must surface the
        uncertainty to the user.
        """
        if not run_id or not tool_call_id:
            return None
        sha = args_sha256(arguments)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT tool_name, args_sha256, result_json, committed_at,
                       state, operation_id
                FROM side_effects WHERE run_id = ? AND tool_call_id = ?
                """,
                (run_id, tool_call_id),
            ).fetchone()
        if row is None:
            return None
        if row["args_sha256"] != sha:
            return None
        state = row["state"] or SideEffectState.COMMITTED.value
        if state == SideEffectState.UNCERTAIN.value:
            return {
                "tool_name": row["tool_name"],
                "result": None,
                "state": "uncertain",
                "operation_id": row["operation_id"] or "",
                "committed_at": row["committed_at"],
            }
        if state != SideEffectState.COMMITTED.value:
            return None
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError:
            result = {}
        return {
            "tool_name": row["tool_name"],
            "result": result,
            "state": "committed",
            "operation_id": row["operation_id"] or "",
            "committed_at": row["committed_at"],
        }

    def _row(self, run_id: str, tool_call_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM side_effects WHERE run_id=? AND tool_call_id=?",
                (run_id, tool_call_id),
            ).fetchone()

    def uncommitted_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Side effects in this run that are Planned or Executing (not yet
        committed, failed, or marked uncertain). These are the crash-window
        candidates — on cold-start recovery they should be transitioned to
        Uncertain.

        Returns a list of dicts with tool_call_id, tool_name, state, and
        operation_id.
        """
        if not run_id:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT tool_call_id, tool_name, state, operation_id, updated_at
                FROM side_effects
                WHERE run_id = ? AND state IN (?, ?)
                ORDER BY updated_at
                """,
                (
                    run_id,
                    SideEffectState.PLANNED.value,
                    SideEffectState.EXECUTING.value,
                ),
            ).fetchall()
        return [
            {
                "tool_call_id": r["tool_call_id"],
                "tool_name": r["tool_name"],
                "state": r["state"],
                "operation_id": r["operation_id"] or "",
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def uncertain_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """All side effects in this run that are in the Uncertain state —
        the user must resolve them (confirm, re-execute, or mark failed).
        """
        if not run_id:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT tool_call_id, tool_name, operation_id, updated_at
                FROM side_effects
                WHERE run_id = ? AND state = ?
                ORDER BY updated_at
                """,
                (run_id, SideEffectState.UNCERTAIN.value),
            ).fetchall()
        return [
            {
                "tool_call_id": r["tool_call_id"],
                "tool_name": r["tool_name"],
                "operation_id": r["operation_id"] or "",
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def committed_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """All committed side effects in this run (for replay / audit)."""
        if not run_id:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT tool_call_id, tool_name, args_sha256, result_json,
                       operation_id, committed_at
                FROM side_effects
                WHERE run_id = ? AND state = ?
                ORDER BY committed_at
                """,
                (run_id, SideEffectState.COMMITTED.value),
            ).fetchall()
        out = []
        for r in rows:
            try:
                result = json.loads(r["result_json"])
            except json.JSONDecodeError:
                result = {}
            out.append(
                {
                    "tool_call_id": r["tool_call_id"],
                    "tool_name": r["tool_name"],
                    "args_sha256": r["args_sha256"],
                    "result": result,
                    "operation_id": r["operation_id"] or "",
                    "committed_at": r["committed_at"],
                }
            )
        return out

    # -- cold-start recovery ---------------------------------------------------

    def sweep_stale(
        self,
        interrupted_run_ids: list[str],
        *,
        ledger: "RunEventLedger | None" = None,
        workspace: str | None = None,
    ) -> list[dict[str, Any]]:
        """Transition all Planned/Executing side effects in the given
        interrupted runs to Uncertain. Returns the list of newly-uncertain
        side effects (for Inbox surfacing).

        Called during cold-start recovery after ``RunEventLedger.recover_stale``
        has identified the interrupted runs.
        """
        swept: list[dict[str, Any]] = []
        for run_id in interrupted_run_ids:
            stale = self.uncommitted_for_run(run_id)
            for entry in stale:
                self.mark_uncertain(
                    run_id,
                    entry["tool_call_id"],
                    ledger=ledger,
                    workspace=workspace,
                )
                swept.append(
                    {
                        "run_id": run_id,
                        "tool_call_id": entry["tool_call_id"],
                        "tool_name": entry["tool_name"],
                        "operation_id": entry["operation_id"],
                    }
                )
        return swept

    def resolve_uncertain(
        self,
        run_id: str,
        tool_call_id: str,
        resolution: str,
        *,
        result: Any = None,
        ledger: "RunEventLedger | None" = None,
        workspace: str | None = None,
    ) -> None:
        """User-driven resolution of an Uncertain side effect.

        ``resolution`` is one of:
        * ``"confirmed"``  — the side effect did happen; mark Committed
        * ``"failed"``     — the side effect did NOT happen; mark Failed
        * ``"dismissed"``  — user handled it out-of-band; mark Failed
        """
        if not run_id or not tool_call_id:
            return
        now = time.time()
        if resolution == "confirmed":
            with self._lock:
                self._conn.execute(
                    "UPDATE side_effects SET state=?, result_json=?, "
                    "committed_at=?, updated_at=? "
                    "WHERE run_id=? AND tool_call_id=? AND state=?",
                    (
                        SideEffectState.COMMITTED.value,
                        _canonical(result or {"confirmed": True}),
                        now,
                        now,
                        run_id,
                        tool_call_id,
                        SideEffectState.UNCERTAIN.value,
                    ),
                )
                self._conn.commit()
        else:
            with self._lock:
                self._conn.execute(
                    "UPDATE side_effects SET state=?, result_json=?, "
                    "updated_at=? "
                    "WHERE run_id=? AND tool_call_id=? AND state=?",
                    (
                        SideEffectState.FAILED.value,
                        _canonical({"resolution": resolution}),
                        now,
                        run_id,
                        tool_call_id,
                        SideEffectState.UNCERTAIN.value,
                    ),
                )
                self._conn.commit()

    def close(self) -> None:
        self._conn.close()
