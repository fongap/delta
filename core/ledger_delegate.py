"""RunEventLedger delegate to Rust Core when is_rust_authority is active.

This module is the R1.5 Ledger authority cutover (ADR-015). When
``DELTA_RUST_AUTHORITY=ledger`` is set, every ``append`` call on
:class:`RunEventLedgerWithDelegate` is forwarded to the unified
``delta_core`` Rust process via :class:`DeltaCoreClient` instead of
writing through the Python SQLite connection.

R1.5 cutover: the delegate now uses the persistent
:class:`DeltaCoreClient` (NDJSON over stdin/stdout) instead of
spawning a fresh ``write_ledger`` subprocess per command. The
per-op CLI binary ``write_ledger`` is retained only as a diagnostic
tool.

The delegate scrubs the payload through the shared sanitizer
**before** forwarding, so the hash basis that Rust computes is over
exactly what would have been stored by Python.

Fail-closed (P0-3): when ``DELTA_RUST_AUTHORITY=ledger`` is
declared but the ``delta_core`` binary is unavailable, the delegate
raises :class:`DeltaCoreError` — it never silently falls back to
the Python write path.
"""

from __future__ import annotations

import time
from typing import Any

from core.ledger import RunEventLedger

from packages.delta_core_client import DeltaCoreClient, DeltaCoreError, default_client
from packages.storage_authority import is_rust_authority


def _is_delegate_active() -> bool:
    """True iff Rust authority is on AND the delta_core binary is available."""
    if not is_rust_authority("ledger"):
        return False
    return DeltaCoreClient._find_binary() is not None


def _invoke_core(
    db_path: str,
    run_id: str,
    event_type: str,
    actor: str,
    ts: float,
    payload: dict | None,
    workspace: str | None,
) -> dict[str, Any]:
    """Send one ledger.append command to delta_core via the shared client."""
    cmd: dict[str, Any] = {
        "cmd": "ledger.append",
        "db": db_path,
        "run_id": run_id,
        "type": event_type,
        "actor": actor,
        "ts": ts,
    }
    if payload is not None:
        cmd["payload"] = payload
    if workspace:
        cmd["workspace"] = workspace
    client = default_client()
    result = client.command(cmd)
    return result if isinstance(result, dict) else {}


class RunEventLedgerWithDelegate:
    """Wrap a :class:`RunEventLedger` and forward ``append`` to Rust.

    Read methods (:meth:`events`, :meth:`verify`, etc.) call through to
    the inner Python object. :meth:`append` checks
    :func:`is_rust_authority` and either delegates to Rust or calls
    the Python implementation. The Python read path is unchanged.

    Use :func:`maybe_wrap_ledger` to construct.
    """

    def __init__(self, inner: RunEventLedger):
        self._inner = inner
        self._db_path = str(inner.db_path)
        self._delegate = _is_delegate_active()

    @property
    def delegate_active(self) -> bool:
        return self._delegate

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
        if self._delegate:
            ts = ts if ts is not None else time.time()
            from packages.sanitize import sanitize_payload

            stored_payload = sanitize_payload(payload)
            return _invoke_core(
                self._db_path,
                run_id,
                type,
                actor,
                ts,
                stored_payload,
                workspace or "",
            )
        return self._inner.append(
            run_id,
            type,
            actor=actor,
            payload=payload,
            ts=ts,
            workspace=workspace,
        )

    # -- reads / lifecycle: forward to inner -----------------------------------

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return self._inner.events(run_id)

    def run_status(self, run_id: str) -> str:
        return self._inner.run_status(run_id)

    def derive_run_status(self, run_id: str, fallback: str | None = None) -> str:
        return self._inner.derive_run_status(run_id, fallback=fallback)

    def events_in_workspace(
        self, run_id: str, workspace: str
    ) -> list[dict[str, Any]]:
        return self._inner.events_in_workspace(run_id, workspace)

    def runs(self) -> list[str]:
        return self._inner.runs()

    def open_runs(self) -> list[str]:
        return self._inner.open_runs()

    def recover_stale(self) -> list[dict[str, Any]]:
        return list(self._inner.recover_stale())

    def verify(self, run_id: str) -> bool:
        return self._inner.verify(run_id)

    def close(self) -> None:
        self._inner.close()


def maybe_wrap_ledger(ledger: RunEventLedger) -> RunEventLedger | RunEventLedgerWithDelegate:
    """Return a delegate wrapper iff Rust authority is active; else the original.

    Fail-closed (P0-3): when ``DELTA_RUST_AUTHORITY`` declares
    ``ledger`` as a Rust domain but the ``delta_core`` binary is
    unavailable, this raises :class:`DeltaCoreError` rather than
    silently falling back to the Python write path.
    """
    if not is_rust_authority("ledger"):
        return ledger
    if not _is_delegate_active():
        raise DeltaCoreError(
            "DELTA_RUST_AUTHORITY declares ledger as a Rust domain "
            "but delta_core binary is not available; refusing to fall "
            "back to Python (fail-closed). Build delta_core or unset "
            "DELTA_RUST_AUTHORITY."
        )
    return RunEventLedgerWithDelegate(ledger)
