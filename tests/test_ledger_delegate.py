"""RunEventLedgerWithDelegate — R1 Ledger authority switch behavior.

R1 Ledger authority switch (ADR-015). The
``RunEventLedgerWithDelegate`` wrapper is the entry point for opt-in
Rust Core writes. The default ``RunEventLedger`` keeps its current
behavior; this wrapper activates only when ``DELTA_RUST_AUTHORITY=1``
is set and the Rust binary is built.

The tests below set / unset the env var explicitly per test so the
behavior is deterministic regardless of the host environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.ledger import RunEventLedger
from core.ledger_delegate import (
    RunEventLedgerWithDelegate,
    maybe_wrap_ledger,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"
BINARY = CRATE_DIR / "target" / "debug" / ("write_ledger.exe" if sys.platform == "win32" else "write_ledger")


@pytest.fixture
def rust_authority_off(monkeypatch):
    monkeypatch.delenv("DELTA_RUST_AUTHORITY", raising=False)


@pytest.fixture
def rust_authority_on(monkeypatch):
    if not BINARY.exists():
        pytest.skip("write_ledger binary not built")
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "1")


def test_maybe_wrap_returns_plain_ledger_when_authority_off(rust_authority_off, tmp_path):
    db = tmp_path / "run-events.db"
    ledger = RunEventLedger(db)
    wrapped = maybe_wrap_ledger(ledger)
    assert wrapped is ledger
    assert not isinstance(wrapped, RunEventLedgerWithDelegate)


def test_maybe_wrap_returns_delegate_when_authority_on(rust_authority_on, tmp_path):
    db = tmp_path / "run-events.db"
    ledger = RunEventLedger(db)
    wrapped = maybe_wrap_ledger(ledger)
    assert isinstance(wrapped, RunEventLedgerWithDelegate)
    assert wrapped.delegate_active is True


def test_delegate_writes_via_rust_and_python_reads_back(rust_authority_on, tmp_path):
    db = tmp_path / "run-events.db"
    ledger = RunEventLedger(db)
    wrapped = maybe_wrap_ledger(ledger)

    wrapped.append("run_d1", "run.started", actor="user", payload={"kind": "run"})
    wrapped.append("run_d1", "run.completed", actor="system", payload={"kind": "run"})

    events = wrapped.events("run_d1")
    assert len(events) == 2
    assert events[0]["type"] == "run.started"
    assert events[1]["type"] == "run.completed"
    # Hash chain must verify
    assert wrapped.verify("run_d1") is True


def test_delegate_sanitizes_payload(rust_authority_on, tmp_path):
    """The delegate must scrub secrets via the shared sanitizer before
    forwarding to Rust, matching the Python append() contract."""
    db = tmp_path / "run-events.db"
    ledger = RunEventLedger(db)
    wrapped = maybe_wrap_ledger(ledger)

    wrapped.append("run_d2", "tool.proposed", actor="tool", payload={
        "tool": "shell",
        "api_key": "sk-1234567890abcdef",
    })

    events = wrapped.events("run_d2")
    assert len(events) == 1
    # The secret-keyed value is scrubbed; what we wrote verbatim is not stored.
    assert events[0]["payload"].get("api_key") == "[redacted]"


def test_delegate_workspace_index(rust_authority_on, tmp_path):
    db = tmp_path / "run-events.db"
    ledger = RunEventLedger(db)
    wrapped = maybe_wrap_ledger(ledger)

    wrapped.append("run_d3", "run.started", actor="user", payload={}, workspace="ws_1")
    wrapped.append("run_d3", "run.completed", actor="system", payload={}, workspace="ws_1")
    wrapped.append("run_d4", "run.started", actor="user", payload={}, workspace="ws_2")

    ws1 = wrapped.events_in_workspace("run_d3", "ws_1")
    assert len(ws1) == 2
    assert all(e["workspace"] == "ws_1" for e in ws1)


def test_plain_ledger_path_unchanged_when_authority_off(rust_authority_off, tmp_path):
    """Default RunEventLedger keeps Python write path when authority is off."""
    db = tmp_path / "run-events.db"
    ledger = RunEventLedger(db)

    ledger.append("run_d5", "run.started", actor="user", payload={"kind": "run"})
    ledger.append("run_d5", "run.completed", actor="system", payload={"kind": "run"})

    assert ledger.verify("run_d5") is True
    events = ledger.events("run_d5")
    assert len(events) == 2
