"""IdempotencyLog delegate to Rust Core when is_rust_authority is active.

This module is the R1 Idempotency authority switch stage B (ADR-014).
When ``DELTA_RUST_AUTHORITY=1`` is set and the binary is built, every
write call on :class:`IdempotencyLogWithDelegate` is forwarded to the
Rust ``write_idemlog`` binary instead of writing through the Python
SQLite connection. The Python code path is otherwise unchanged.

The default factory :func:`maybe_wrap` returns either a plain
:class:`IdempotencyLog` (when authority is not active) or a delegate
wrapper (when authority is active). Callers that explicitly construct
``IdempotencyLog(...)`` keep their existing behavior.

Risk: this PR introduces a real authority switch. Rolling back is
achieved by either (a) unsetting ``DELTA_RUST_AUTHORITY`` or (b)
uninstalling the ``delta-runtime-native`` binary — both routes
immediately restore Python-only writes. The Python read path is
unaffected; both authorities write to the same SQLite table and
:func:`IdempotencyLog.committed_for_run` etc. continue to work.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.idemlog import IdempotencyLog

from packages.storage_authority import is_rust_authority

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"


def _binary_path() -> Path | None:
    target = "write_idemlog.exe" if sys.platform == "win32" else "write_idemlog"
    path = CRATE_DIR / "target" / "debug" / target
    return path if path.exists() else None


def _is_delegate_active() -> bool:
    """True iff Rust authority is on AND the binary is available."""
    if not is_rust_authority("idempotency"):
        return False
    return _binary_path() is not None


def _invoke(action: str, db_path: str, **kw: Any) -> None:
    binary = _binary_path()
    if binary is None:
        raise RuntimeError("write_idemlog binary not built")
    args = [str(binary), "--db", db_path, "--action", action]
    for key, value in kw.items():
        if value is None:
            continue
        flag = "--" + key.replace("_", "-")
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        args += [flag, str(value)]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"write_idemlog {action} failed: rc={result.returncode}, stderr={result.stderr}"
        )


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
            _invoke(
                "record_planned",
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
            _invoke("mark_executing", self._db_path, run_id=run_id, tool_call_id=tool_call_id)
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
            _invoke(
                "commit",
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
            _invoke(
                "mark_failed",
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
            _invoke("mark_uncertain", self._db_path, run_id=run_id, tool_call_id=tool_call_id)
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

    def sweep_stale(self, interrupted_run_ids: list[str], **kw: Any) -> list[dict]:
        return self._inner.sweep_stale(interrupted_run_ids, **kw)

    def resolve_uncertain(self, *args: Any, **kw: Any) -> None:
        return self._inner.resolve_uncertain(*args, **kw)

    def close(self) -> None:
        self._inner.close()


def maybe_wrap(log: IdempotencyLog, db_path: str) -> IdempotencyLog | IdempotencyLogWithDelegate:
    """Return a delegate wrapper iff Rust authority is active; else the original.

    Caller controls the DB path so we can re-derive it from the inner
    object's :attr:`db_path` if not provided.
    """
    if not _is_delegate_active():
        return log
    return IdempotencyLogWithDelegate(log, db_path)
