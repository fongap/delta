"""Idempotency log — durable record of committed side effects (ADR-005 WS4).

The blueprint's recovery scenarios include:

  "Tool 已产生副作用但 Run 未结束 → 恢复后不重复执行"

`core/runtime.py:resume()` already handles durable-prompt-resume (a parked
approval, the answered tool calls, etc.). What it does NOT handle is the
narrow window where:

  1. A side-effecting tool (write_file, run_shell, send_email, …) executed
     successfully inside `_execute_sync` and produced its real-world effect.
  2. The corresponding tool-result message was NOT yet appended to
     `self.messages` (the `messages.append(_tool_result_message(...))` line
     in `_record_result`).
  3. The process crashed (or the socket dropped, or the server restarted).

When `resume()` later reconstructs the turn from the persisted message
thread, that call looks unanswered → the engine will re-invoke it →
the side effect runs a second time.

The idempotency log closes that window. After every side-effecting tool
returns "ok", we write a `(run_id, tool_call_id, sha256(args))` row AND
a `side_effect.committed` ledger event BEFORE the result message is
appended. On `resume()` the engine consults the log:

  - `committed`  → skip execution, replay the recorded result message
                   (`side_effect.replayed` ledger event)
  - `uncommitted` (a half-finished call from before the crash) → surface
                   the run in Inbox as "needs resume"

The log is append-only, keyed by `(run_id, tool_call_id)`. The args
sha256 defends against argument mutation: if the persisted call's
arguments differ from the resumed call's arguments, the log is for a
DIFFERENT call and we re-execute (we don't paper over a model change).
"""

from __future__ import annotations

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


class IdempotencyLog:
    """Append-only dedupe store for committed side effects.

    One row per `(run_id, tool_call_id)`. A re-execute of the SAME call
    is treated as a replay (the engine reuses the recorded result); a
    re-execute with DIFFERENT arguments is a different call and re-runs.
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
                committed_at REAL NOT NULL,
                PRIMARY KEY (run_id, tool_call_id)
            )
            """
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
        """Record that a side effect committed for `(run_id, tool_call_id)`.

        Overwrites on duplicate (idempotent re-commit is fine). Emits a
        `side_effect.committed` ledger event when a ledger is provided.
        """
        if not run_id or not tool_call_id:
            return
        sha = args_sha256(arguments)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO side_effects
                    (run_id, tool_call_id, tool_name, args_sha256, result_json, committed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, tool_call_id) DO UPDATE SET
                    tool_name=excluded.tool_name,
                    args_sha256=excluded.args_sha256,
                    result_json=excluded.result_json,
                    committed_at=excluded.committed_at
                """,
                (
                    run_id,
                    tool_call_id,
                    tool_name,
                    sha,
                    _canonical(result),
                    time.time(),
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
                    },
                    workspace=workspace or None,
                )
            except Exception:
                pass

    def lookup(
        self,
        run_id: str,
        tool_call_id: str,
        arguments: Any,
    ) -> dict[str, Any] | None:
        """Return the stored result for a (run_id, tool_call_id) replay, or
        None if the call is fresh (must execute). A stored row whose
        `args_sha256` differs from the resumed call's arguments is treated
        as a DIFFERENT call → returns None → engine re-executes."""
        if not run_id or not tool_call_id:
            return None
        sha = args_sha256(arguments)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT tool_name, args_sha256, result_json, committed_at
                FROM side_effects WHERE run_id = ? AND tool_call_id = ?
                """,
                (run_id, tool_call_id),
            ).fetchone()
        if row is None:
            return None
        if row["args_sha256"] != sha:
            return None
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError:
            result = {}
        return {
            "tool_name": row["tool_name"],
            "result": result,
            "committed_at": row["committed_at"],
        }

    def uncommitted_for_run(self, run_id: str) -> list[str]:
        """Run ids that have a ledger `tool.started` but no matching
        `side_effect.committed` — i.e. a crash between execution and
        commit. Returns tool_call_ids that need user attention."""
        # Requires the ledger to be in scope; this is a placeholder used
        # by the Inbox surfacing logic in WS4 integration. Kept here so
        # the log owns its own concept of "uncommitted" rather than
        # exposing raw row access to the rest of the system.
        return []

    def close(self) -> None:
        self._conn.close()
