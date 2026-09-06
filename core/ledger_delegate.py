"""RunEventLedger delegate to Rust Core when is_rust_authority is active.

This module is the R1 Ledger authority switch (ADR-015). When
``DELTA_RUST_AUTHORITY=1`` is set and the binary is built, every
``append`` call on :class:`RunEventLedgerWithDelegate` is forwarded to
the Rust ``write_ledger`` binary instead of writing through the Python
SQLite connection. The Python code path is otherwise unchanged.

The delegate scrubs the payload through the shared sanitizer
**before** forwarding, so the hash basis that Rust computes is over
exactly what would have been stored by Python.

The default factory :func:`maybe_wrap_ledger` returns either a plain
:class:`RunEventLedger` (when authority is not active) or a delegate
wrapper (when authority is active). Callers that explicitly construct
``RunEventLedger(...)`` keep their existing behavior.

Risk: this PR introduces a real authority switch. Rolling back is
achieved by either (a) unsetting ``DELTA_RUST_AUTHORITY`` or (b)
uninstalling the ``delta-runtime-native`` binary — both routes
immediately restore Python-only writes. The Python read path is
unaffected; both authorities write to the same SQLite table and
:meth:`RunEventLedger.events` etc. continue to work.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core.ledger import RunEventLedger

from packages.storage_authority import is_rust_authority

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"


def _binary_path() -> Path | None:
    target = "write_ledger.exe" if sys.platform == "win32" else "write_ledger"
    path = CRATE_DIR / "target" / "debug" / target
    return path if path.exists() else None


def _is_delegate_active() -> bool:
    """True iff Rust authority is on AND the binary is available."""
    if not is_rust_authority("ledger"):
        return False
    return _binary_path() is not None


def _invoke(
    db_path: str,
    run_id: str,
    event_type: str,
    actor: str,
    ts: float,
    payload: dict | None,
    workspace: str | None,
) -> dict[str, Any]:
    binary = _binary_path()
    if binary is None:
        raise RuntimeError("write_ledger binary not built")
    args = [
        str(binary),
        "--db",
        db_path,
        "--run-id",
        run_id,
        "--type",
        event_type,
        "--actor",
        actor,
        "--ts",
        str(ts),
    ]
    if payload is not None:
        args += ["--payload", json.dumps(payload)]
    if workspace:
        args += ["--workspace", workspace]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"write_ledger failed: rc={result.returncode}, stderr={result.stderr}"
        )
    out = result.stdout.strip()
    return json.loads(out) if out else {}


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
            return _invoke(
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
    """Return a delegate wrapper iff Rust authority is active; else the original."""
    if not _is_delegate_active():
        return ledger
    return RunEventLedgerWithDelegate(ledger)
