"""IdempotencyLog delegate to Rust Core when is_rust_authority is active.

This module is the R1.5 Idempotency authority cutover (ADR-014). When
``DELTA_RUST_AUTHORITY=idempotency`` is set, every write call on
:class:`IdempotencyLogWithDelegate` is forwarded to the unified
``delta_core`` Rust process via :class:`DeltaCoreClient` instead of
writing through the Python SQLite connection.

R1.5 cutover: the delegate now uses the persistent
:class:`DeltaCoreClient` (NDJSON over stdin/stdout) instead of
spawning a fresh ``write_idemlog`` subprocess per command. The
per-op CLI binaries (``write_idemlog`` etc.) are retained only as
diagnostic tools.

Fail-closed (P0-3): when ``DELTA_RUST_AUTHORITY=idempotency`` is
declared but the ``delta_core`` binary is unavailable, the delegate
raises :class:`DeltaCoreError` — it never silently falls back to
the Python write path. Rollback is achieved by unsetting
``DELTA_RUST_AUTHORITY``.
"""

from __future__ import annotations

from typing import Any

from core.idemlog import IdempotencyLog

from packages.delta_core_client import DeltaCoreClient, DeltaCoreError, default_client
from packages.storage_authority import is_rust_authority


def _is_delegate_active() -> bool:
    """True iff Rust authority is on AND the delta_core binary is available."""
    if not is_rust_authority("idempotency"):
        return False
    return DeltaCoreClient._find_binary() is not None


def _invoke_core(cmd: str, db_path: str, **kw: Any) -> Any:
    """Send one command to delta_core via the shared DeltaCoreClient."""
    client = default_client()
    return client.command({"cmd": cmd, "db": db_path, **kw})


class IdempotencyLogWithDelegate:
    """Wrap an :class:`IdempotencyLog` and forward writes to Rust.

    Read methods (:meth:`committed_for_run` etc.) call through to the
    inner Python object. Write methods (record_planned, mark_executing,
    commit, mark_failed, mark_uncertain) check
    :func:`is_rust_authority` and either delegate to Rust or call the
    Python implementation. The Python read path is unchanged.

    Use :func:`maybe_wrap` to construct.
    """

    def __init__(self, inner: IdempotencyLog, db_path: str):
        self._inner = inner
        self._db_path = str(db_path)
        self._delegate = _is_delegate_active()

    @property
    def delegate_active(self) -> bool:
        return self._delegate

    # -- writes ----------------------------------------------------------------

    def record_planned(
        self,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Any,
        *,
        ledger: Any = None,
        workspace: str | None = None,
    ) -> str | None:
        if self._delegate:
            _invoke_core(
                "idem.record_planned",
                self._db_path,
                run_id=run_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=arguments,
            )
            return None
        return self._inner.record_planned(
            run_id, tool_call_id, tool_name, arguments,
            ledger=ledger, workspace=workspace,
        )

    def mark_executing(
        self,
        run_id: str,
        tool_call_id: str,
        *,
        ledger: Any = None,
        workspace: str | None = None,
    ) -> None:
        if self._delegate:
            _invoke_core("idem.mark_executing", self._db_path, run_id=run_id, tool_call_id=tool_call_id)
            return
        self._inner.mark_executing(run_id, tool_call_id, ledger=ledger, workspace=workspace)

    def commit(
        self,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: Any,
        result: Any,
        *,
        ledger: Any = None,
        workspace: str | None = None,
    ) -> None:
        if self._delegate:
            _invoke_core(
                "idem.commit",
                self._db_path,
                run_id=run_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=arguments,
                result=result,
            )
            return
        return self._inner.commit(
            run_id, tool_call_id, tool_name, arguments, result,
            ledger=ledger, workspace=workspace,
        )

    def mark_failed(
        self,
        run_id: str,
        tool_call_id: str,
        error: str,
        *,
        ledger: Any = None,
        workspace: str | None = None,
    ) -> None:
        if self._delegate:
            _invoke_core(
                "idem.mark_failed",
                self._db_path,
                run_id=run_id,
                tool_call_id=tool_call_id,
                error=error,
            )
            return
        self._inner.mark_failed(run_id, tool_call_id, error, ledger=ledger, workspace=workspace)

    def mark_uncertain(
        self,
        run_id: str,
        tool_call_id: str,
        *,
        ledger: Any = None,
        workspace: str | None = None,
    ) -> None:
        if self._delegate:
            _invoke_core("idem.mark_uncertain", self._db_path, run_id=run_id, tool_call_id=tool_call_id)
            if ledger is not None:
                try:
                    row = self._inner._row(run_id, tool_call_id)
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
            return
        self._inner.mark_uncertain(run_id, tool_call_id, ledger=ledger, workspace=workspace)

    # -- reads -----------------------------------------------------------------

    def lookup(self, run_id: str, tool_call_id: str, arguments: Any) -> dict | None:
        return self._inner.lookup(run_id, tool_call_id, arguments)

    def uncommitted_for_run(self, run_id: str) -> list[dict]:
        return self._inner.uncommitted_for_run(run_id)

    def uncertain_for_run(self, run_id: str) -> list[dict]:
        return self._inner.uncertain_for_run(run_id)

    def committed_for_run(self, run_id: str) -> list[dict]:
        return self._inner.committed_for_run(run_id)

    def sweep_stale(
        self, interrupted_run_ids: list[str], **kw: Any
    ) -> list[dict]:
        if not self._delegate:
            return self._inner.sweep_stale(interrupted_run_ids, **kw)
        swept: list[dict] = []
        for run_id in interrupted_run_ids:
            stale = self._inner.uncommitted_for_run(run_id)
            for entry in stale:
                self.mark_uncertain(
                    run_id, entry["tool_call_id"], **kw
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

    def resolve_uncertain(self, *args: Any, **kw: Any) -> None:
        return self._inner.resolve_uncertain(*args, **kw)

    def close(self) -> None:
        self._inner.close()


def maybe_wrap(log: IdempotencyLog, db_path: str) -> IdempotencyLog | IdempotencyLogWithDelegate:
    """Return a delegate wrapper iff Rust authority is active; else the original.

    Fail-closed (P0-3): when ``DELTA_RUST_AUTHORITY`` declares
    ``idempotency`` as a Rust domain but the ``delta_core`` binary is
    unavailable, this raises :class:`DeltaCoreError` rather than
    silently falling back to the Python write path.
    """
    if not is_rust_authority("idempotency"):
        return log
    if not _is_delegate_active():
        raise DeltaCoreError(
            "DELTA_RUST_AUTHORITY declares idempotency as a Rust domain "
            "but delta_core binary is not available; refusing to fall "
            "back to Python (fail-closed). Build delta_core or unset "
            "DELTA_RUST_AUTHORITY."
        )
    return IdempotencyLogWithDelegate(log, db_path)
