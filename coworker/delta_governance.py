"""Local, replayable governance records for Delta tasks."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class GovernanceStore:
    """Append-only task evidence kept separately from model conversation state."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
              id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, path TEXT NOT NULL,
              kind TEXT NOT NULL, citations TEXT NOT NULL, validation TEXT NOT NULL,
              created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
              id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, action TEXT NOT NULL,
              decision TEXT NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
              id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, summary TEXT NOT NULL,
              next_step TEXT NOT NULL, risks TEXT NOT NULL, created_at REAL NOT NULL
            );
            """
        )
        self._conn.commit()

    def record_artifact(self, task_id: str, path: str, kind: str, *, citations: list[dict] | None = None, validation: dict | None = None) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO artifacts(task_id,path,kind,citations,validation,created_at) VALUES(?,?,?,?,?,?)", (task_id, path, kind, json.dumps(citations or []), json.dumps(validation or {}), time.time()))
            self._conn.commit()

    def record_approval(self, task_id: str, action: str, decision: str, *, reason: str = "") -> None:
        with self._lock:
            self._conn.execute("INSERT INTO approvals(task_id,action,decision,reason,created_at) VALUES(?,?,?,?,?)", (task_id, action, decision, reason, time.time()))
            self._conn.commit()

    def checkpoint(self, task_id: str, summary: str, next_step: str, *, risks: list[str] | None = None) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO checkpoints(task_id,summary,next_step,risks,created_at) VALUES(?,?,?,?,?)", (task_id, summary, next_step, json.dumps(risks or []), time.time()))
            self._conn.commit()

    def replay(self, task_id: str) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for table in ("artifacts", "approvals", "checkpoints"):
            with self._lock:
                rows = self._conn.execute(f"SELECT * FROM {table} WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
            result[table] = [dict(row) for row in rows]
        return result

    def close(self) -> None:
        with self._lock:
            self._conn.close()
