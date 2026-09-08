"""Rust-authoritative side-effect state facade.

Idempotency is a hard-cut R1 domain. Every persisted state transition,
replay decision, stale sweep, and operator resolution is executed by
``delta_core``. Python retains only the public adapter used by the current
runtime and the stable value types from the v0.3.2 contract.

There is deliberately no Python SQLite writer and no runtime fallback. A
missing, incompatible, or failed Rust Core process raises ``DeltaCoreError``.
"""

from __future__ import annotations

import enum
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packages.delta_core_client import DeltaCoreError, close_default_client, default_client

if TYPE_CHECKING:
    from core.ledger import RunEventLedger


def _canonical(arguments: Any) -> str:
    return json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), default=str)


def _normalized(arguments: Any) -> Any:
    return json.loads(_canonical(arguments))


def args_sha256(arguments: Any) -> str:
    """Return stable request metadata; Rust decides replay eligibility."""
    response = default_client().command(
        {
            "cmd": "idem.identify",
            "run_id": "",
            "tool_call_id": "",
            "args": _normalized(arguments),
        }
    )
    if not isinstance(response, dict) or not isinstance(response.get("args_sha256"), str):
        raise DeltaCoreError("invalid idem.identify response")
    return response["args_sha256"]


def operation_id(run_id: str, tool_call_id: str) -> str:
    """Return the stable external idempotency key from the public contract."""
    response = default_client().command(
        {
            "cmd": "idem.identify",
            "run_id": run_id,
            "tool_call_id": tool_call_id,
            "args": {},
        }
    )
    if not isinstance(response, dict) or not isinstance(response.get("operation_id"), str):
        raise DeltaCoreError("invalid idem.identify response")
    return response["operation_id"]


class SideEffectState(str, enum.Enum):
    PLANNED = "planned"
    EXECUTING = "executing"
    COMMITTED = "committed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"

    @classmethod
    def from_str(cls, raw: str | None) -> SideEffectState | None:
        if not raw:
            return None
        try:
            return cls(raw)
        except ValueError:
            return cls.COMMITTED


class IdempotencyLog:
    """Thin Python API over the single Rust idempotency authority."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser()
        self._db_path = str(self.db_path)
        self._lock = threading.RLock()
        self._invoke("idem.initialize")

    def _invoke(self, cmd: str, **kwargs: Any) -> Any:
        return default_client().command({"cmd": cmd, "db": self._db_path, **kwargs})

    @staticmethod
    def _append_ledger(
        ledger: RunEventLedger | None,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        workspace: str | None,
    ) -> None:
        if ledger is None:
            return
        try:
            ledger.append(
                run_id,
                event_type,
                actor="system",
                payload=payload,
                workspace=workspace or None,
            )
        except Exception:
            # Preserve the established cross-domain behavior until the
            # ledger's independent R1 hard cut removes this compatibility.
            pass

    def record_planned(
        self,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Any,
        *,
        ledger: RunEventLedger | None = None,
        workspace: str | None = None,
    ) -> str | None:
        if not run_id or not tool_call_id:
            return None
        normalized_arguments = _normalized(arguments)
        response = self._invoke(
            "idem.record_planned",
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args=normalized_arguments,
        )
        if not isinstance(response, dict) or not isinstance(response.get("operation_id"), str):
            raise DeltaCoreError("invalid idem.record_planned response")
        op_id = response["operation_id"]
        self._append_ledger(
            ledger,
            run_id,
            "side_effect.planned",
            {
                "tool_call_id": tool_call_id,
                "tool": tool_name,
                "args_sha256": args_sha256(normalized_arguments),
                "operation_id": op_id,
            },
            workspace,
        )
        return op_id

    def mark_executing(
        self,
        run_id: str,
        tool_call_id: str,
        *,
        ledger: RunEventLedger | None = None,
        workspace: str | None = None,
    ) -> None:
        del ledger, workspace
        if run_id and tool_call_id:
            self._invoke("idem.mark_executing", run_id=run_id, tool_call_id=tool_call_id)

    def commit(
        self,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Any,
        result: Any,
        *,
        ledger: RunEventLedger | None = None,
        workspace: str | None = None,
    ) -> None:
        if not run_id or not tool_call_id:
            return
        normalized_arguments = _normalized(arguments)
        self._invoke(
            "idem.commit",
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args=normalized_arguments,
            result=result,
        )
        self._append_ledger(
            ledger,
            run_id,
            "side_effect.committed",
            {
                "tool_call_id": tool_call_id,
                "tool": tool_name,
                "args_sha256": args_sha256(normalized_arguments),
                "operation_id": operation_id(run_id, tool_call_id),
            },
            workspace,
        )

    def mark_failed(
        self,
        run_id: str,
        tool_call_id: str,
        error: str,
        *,
        ledger: RunEventLedger | None = None,
        workspace: str | None = None,
    ) -> None:
        if not run_id or not tool_call_id:
            return
        self._invoke(
            "idem.mark_failed",
            run_id=run_id,
            tool_call_id=tool_call_id,
            error=error,
        )
        self._append_ledger(
            ledger,
            run_id,
            "side_effect.failed",
            {"tool_call_id": tool_call_id, "error": error},
            workspace,
        )

    def mark_uncertain(
        self,
        run_id: str,
        tool_call_id: str,
        *,
        ledger: RunEventLedger | None = None,
        workspace: str | None = None,
    ) -> None:
        if not run_id or not tool_call_id:
            return
        self._invoke("idem.mark_uncertain", run_id=run_id, tool_call_id=tool_call_id)
        row = self._row(run_id, tool_call_id)
        self._append_ledger(
            ledger,
            run_id,
            "side_effect.uncertain",
            {
                "tool_call_id": tool_call_id,
                "tool": row.get("tool_name", "unknown") if row else "unknown",
                "operation_id": row.get("operation_id", "") if row else "",
            },
            workspace,
        )

    def lookup(
        self,
        run_id: str,
        tool_call_id: str,
        arguments: Any,
    ) -> dict[str, Any] | None:
        if not run_id or not tool_call_id:
            return None
        response = self._invoke(
            "idem.lookup",
            run_id=run_id,
            tool_call_id=tool_call_id,
            args=_normalized(arguments),
        )
        if response is None:
            return None
        if not isinstance(response, dict):
            raise DeltaCoreError("invalid idem.lookup response")
        return response

    def _row(self, run_id: str, tool_call_id: str) -> dict[str, Any] | None:
        response = self._invoke("idem.get", run_id=run_id, tool_call_id=tool_call_id)
        if response is None:
            return None
        if not isinstance(response, dict):
            raise DeltaCoreError("invalid idem.get response")
        return response

    def _list(self, run_id: str, view: str) -> list[dict[str, Any]]:
        if not run_id:
            return []
        response = self._invoke("idem.list", run_id=run_id, view=view)
        if not isinstance(response, list) or not all(isinstance(item, dict) for item in response):
            raise DeltaCoreError(f"invalid idem.list response for {view}")
        return response

    def uncommitted_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return self._list(run_id, "uncommitted")

    def uncertain_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return self._list(run_id, "uncertain")

    def committed_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return self._list(run_id, "committed")

    def sweep_stale(
        self,
        interrupted_run_ids: list[str],
        *,
        ledger: RunEventLedger | None = None,
        workspace: str | None = None,
    ) -> list[dict[str, Any]]:
        response = self._invoke(
            "idem.sweep_stale",
            interrupted_run_ids=interrupted_run_ids,
        )
        if not isinstance(response, list) or not all(isinstance(item, dict) for item in response):
            raise DeltaCoreError("invalid idem.sweep_stale response")
        for entry in response:
            self._append_ledger(
                ledger,
                str(entry.get("run_id", "")),
                "side_effect.uncertain",
                {
                    "tool_call_id": entry.get("tool_call_id", ""),
                    "tool": entry.get("tool_name", "unknown"),
                    "operation_id": entry.get("operation_id", ""),
                },
                workspace,
            )
        return response

    def resolve_uncertain(
        self,
        run_id: str,
        tool_call_id: str,
        resolution: str,
        *,
        result: Any = None,
        ledger: RunEventLedger | None = None,
        workspace: str | None = None,
    ) -> None:
        del ledger, workspace
        if not run_id or not tool_call_id:
            return
        self._invoke(
            "idem.resolve_uncertain",
            run_id=run_id,
            tool_call_id=tool_call_id,
            resolution=resolution,
            result=result,
        )

    def close(self) -> None:
        """Release the process-scoped authority and its SQLite handles."""
        close_default_client()
