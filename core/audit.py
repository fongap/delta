"""Durable local audit log for connector/tool actions.

ADR-031: audit event *writes* are delegated to the Rust authority
(``core.approval.record`` → ``delta_core approval.record``). The *read*
path (``list``) stays in Python via its own SQLite connection to the same
database file.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from integrations.connectors import connector_for_tool
from packages.sanitize import (
    BODY_KEYS,
    SECRET_KEY_MARKERS,
)
from packages.storage_authority import is_rust_authority  # noqa: F401 (ADR-031 structural guard)

# Legacy names kept as aliases (the definitions now live in packages/sanitize.py,
# the one shared SensitiveDataSanitizer).
_SECRET_KEYS = SECRET_KEY_MARKERS
_BODY_KEYS = BODY_KEYS


class AuditStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
                agent TEXT,
                workspace TEXT,
                connector TEXT,
                tool TEXT,
                stage TEXT,
                status TEXT,
                approval TEXT,
                args TEXT,
                result_preview TEXT,
                reason TEXT,
                resource TEXT,
                level TEXT DEFAULT '',
                isolation TEXT DEFAULT ''
            )
            """)
        self._conn.commit()

    def append(self, event: dict[str, Any]) -> None:
        tool = str(event.get("tool") or event.get("tool_name") or "")
        connector = str(event.get("connector") or connector_for_tool(tool) or "")
        args = _sanitize_args(tool, event.get("arguments") or {})
        resource = _resource(
            tool, event.get("arguments") or {}, event.get("result") or {}
        )
        payload = {
            **event,
            "tool": tool,
            "connector": connector,
            "arguments": args,
            "result_preview": _truncate(str(event.get("result_preview") or "")),
            "reason": _truncate(str(event.get("reason") or "")),
            "resource": _truncate(str(resource or "")),
            "level": str(event.get("level") or ""),
            "isolation": str(event.get("isolation") or ""),
        }
        from core.approval import record

        with self._lock:
            record(str(self.db_path), payload)

    def list(
        self,
        *,
        limit: int = 100,
        session_id: str | None = None,
        connector: str | None = None,
        tool: str | None = None,
    ) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if session_id:
            where.append("session_id = ?")
            params.append(session_id)
        if connector:
            where.append("connector = ?")
            params.append(connector)
        if tool:
            where.append("tool = ?")
            params.append(tool)
        sql = "SELECT * FROM audit_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit or 100), 500)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["args"] = json.loads(item.get("args") or "{}")
            except json.JSONDecodeError:
                item["args"] = {}
            out.append(item)
        return out

    def close(self) -> None:
        self._conn.close()


def _sanitize_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Audit-shaped scrubbing: the shared SensitiveDataSanitizer decides what is
    secret; this layer only adds the tool-specific input rule (what the model was
    asked to TYPE into a page is sensitive even though its key says "text") and
    the preview truncation."""
    if not isinstance(args, dict):
        return {}
    from packages.sanitize import sanitize_value

    typed = frozenset({"text"}) if tool == "browser_type" else frozenset()
    return _summarize(sanitize_value(args, typed_input_keys=typed))


def _summarize(value: Any) -> Any:
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_summarize(v) for v in value[:10]]
    if isinstance(value, dict):
        return {str(k): _summarize(v) for k, v in list(value.items())[:20]}
    return _truncate(str(value))


def _resource(tool: str, args: dict[str, Any], result: Any) -> str:
    for key in (
        "url",
        "owner",
        "repo",
        "issue_key",
        "page_id",
        "ticket_id",
        "calendar_id",
        "message_id",
    ):
        if isinstance(args, dict) and args.get(key):
            return str(args[key])
    if isinstance(args, dict) and args.get("subdomain"):
        return f"{args['subdomain']}.zendesk.com"
    if isinstance(result, dict) and result.get("url"):
        return str(result["url"])
    return ""


def _truncate(text: str, limit: int = 500) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."
