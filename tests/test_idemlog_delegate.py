"""IdempotencyLogWithDelegate — R1.5 authority cutover behavior.

R1.5 Idempotency authority cutover (ADR-014). The
``IdempotencyLogWithDelegate`` wrapper is the entry point for opt-in
Rust Core writes via the unified ``delta_core`` process. The default
``IdempotencyLog`` keeps its current behavior; this wrapper activates
only when ``DELTA_RUST_AUTHORITY=idempotency`` is set and the
``delta_core`` binary is built.

Fail-closed (P0-3): when authority is declared but the binary is
missing, ``maybe_wrap`` raises ``DeltaCoreError`` instead of
falling back to Python.

The tests below set / unset the env var explicitly per test so the
behavior is deterministic regardless of the host environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.idemlog import IdempotencyLog
from core.idemlog_delegate import (
    IdempotencyLogWithDelegate,
    maybe_wrap,
)
from packages.delta_core_client import DeltaCoreError

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"
BINARY = CRATE_DIR / "target" / "debug" / ("delta_core.exe" if sys.platform == "win32" else "delta_core")


@pytest.fixture
def rust_authority_off(monkeypatch):
    monkeypatch.delenv("DELTA_RUST_AUTHORITY", raising=False)


@pytest.fixture
def rust_authority_on(monkeypatch):
    if not BINARY.exists():
        pytest.skip("delta_core binary not built")
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency")


def test_maybe_wrap_returns_plain_log_when_authority_off(rust_authority_off, tmp_path):
    db = tmp_path / "side-effects.db"
    log = IdempotencyLog(db)
    wrapped = maybe_wrap(log, str(db))
    assert wrapped is log
    assert not isinstance(wrapped, IdempotencyLogWithDelegate)


def test_maybe_wrap_returns_delegate_when_authority_on(rust_authority_on, tmp_path):
    db = tmp_path / "side-effects.db"
    log = IdempotencyLog(db)
    wrapped = maybe_wrap(log, str(db))
    assert isinstance(wrapped, IdempotencyLogWithDelegate)
    assert wrapped.delegate_active is True


def test_maybe_wrap_fails_closed_when_authority_on_but_binary_missing(monkeypatch, tmp_path):
    """P0-3: when authority is declared but delta_core is unavailable,
    maybe_wrap must raise DeltaCoreError, not silently fall back."""
    from packages.delta_core_client import DeltaCoreClient

    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "idempotency")
    monkeypatch.setattr(DeltaCoreClient, "_find_binary", staticmethod(lambda: None))
    db = tmp_path / "side-effects.db"
    log = IdempotencyLog(db)
    with pytest.raises(DeltaCoreError, match="fail-closed"):
        maybe_wrap(log, str(db))


def test_delegate_writes_via_rust_and_python_reads_back(rust_authority_on, tmp_path):
    db = tmp_path / "side-effects.db"
    log = IdempotencyLog(db)
    wrapped = maybe_wrap(log, str(db))

    wrapped.record_planned("run_d1", "tc_1", "write_file", {"path": "x.txt"})
    wrapped.mark_executing("run_d1", "tc_1")
    wrapped.commit("run_d1", "tc_1", "write_file", {"path": "x.txt"}, {"ok": True})

    committed = wrapped.committed_for_run("run_d1")
    assert len(committed) == 1
    assert committed[0]["tool_call_id"] == "tc_1"
    assert committed[0]["tool_name"] == "write_file"


def test_delegate_writes_uncertain(rust_authority_on, tmp_path):
    db = tmp_path / "side-effects.db"
    log = IdempotencyLog(db)
    wrapped = maybe_wrap(log, str(db))

    wrapped.record_planned("run_d2", "tc_1", "send_message", {"to": "bob"})
    wrapped.mark_executing("run_d2", "tc_1")
    wrapped.mark_uncertain("run_d2", "tc_1")

    uncertain = wrapped.uncertain_for_run("run_d2")
    assert len(uncertain) == 1
    assert uncertain[0]["tool_call_id"] == "tc_1"


def test_delegate_writes_failed(rust_authority_on, tmp_path):
    db = tmp_path / "side-effects.db"
    log = IdempotencyLog(db)
    wrapped = maybe_wrap(log, str(db))

    wrapped.record_planned("run_d3", "tc_1", "write_file", {"path": "y.txt"})
    wrapped.mark_executing("run_d3", "tc_1")
    wrapped.mark_failed("run_d3", "tc_1", error="disk full")

    # Python has no for_run; assert via committed + uncommitted + uncertain
    assert wrapped.committed_for_run("run_d3") == []
    assert wrapped.uncommitted_for_run("run_d3") == []
    assert wrapped.uncertain_for_run("run_d3") == []


def test_plain_log_path_unchanged_when_authority_off(rust_authority_off, tmp_path):
    """Default IdempotencyLog keeps Python write path when authority is off."""
    db = tmp_path / "side-effects.db"
    log = IdempotencyLog(db)

    log.record_planned("run_d4", "tc_1", "read_file", {"path": "a.txt"})
    log.mark_executing("run_d4", "tc_1")
    log.commit("run_d4", "tc_1", "read_file", {"path": "a.txt"}, {"text": "hi"})

    committed = log.committed_for_run("run_d4")
    assert len(committed) == 1
    assert committed[0]["result"] == {"text": "hi"}
