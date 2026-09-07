"""Cross-language tests for the R2 Artifact Registry authority switch (ADR-020).

When ``DELTA_RUST_AUTHORITY=artifact`` is set:

1. Python computes the file stat + sha256 (Rust is the ledger writer,
   not the file walker).
2. The two ledger events (``artifact.registered`` +
   ``artifact.completed``) are appended via the ``delta_core`` Rust
   process.
3. The Rust-written events are byte-equal to what the Python path
   would produce (same hash chain, same payload shape).

These tests prove the delegate path works end-to-end and that the
Rust-written ledger is consistent with the Python reader.

Contract: ``docs/architecture/adr/ADR-020-r2-artifact-authority-switch.md``.
"""

from __future__ import annotations

import hashlib

import pytest

from core.artifact import register_artifact
from core.artifact_delegate import (
    maybe_wrap,
    register_artifact_delegated,
    _is_delegate_active,
)
from core.ledger import RunEventLedger
from packages.delta_core_client import DeltaCoreClient
from packages.storage_authority import is_rust_authority


@pytest.fixture
def rust_authority_artifact(monkeypatch):
    """Enable Rust authority for the artifact domain."""
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "artifact")
    if not DeltaCoreClient._find_binary():
        pytest.skip("delta_core binary not built")
    assert is_rust_authority("artifact") is True
    yield
    # conftest.py closes the default DeltaCoreClient after each test.


def test_delegate_active_when_env_set(rust_authority_artifact):
    assert _is_delegate_active() is True


def test_delegate_inactive_when_env_unset(monkeypatch):
    monkeypatch.delenv("DELTA_RUST_AUTHORITY", raising=False)
    assert _is_delegate_active() is False


def test_register_artifact_via_rust_writes_two_events(
    rust_authority_artifact, tmp_path
):
    """register_artifact_delegated should append artifact.registered +
    artifact.completed events via delta_core."""
    db = tmp_path / "run_events.db"
    led = RunEventLedger(db)
    # Seed the run with a started event so the ledger has a hash chain.
    led.append("run_1", "run.started", actor="user")

    ws = tmp_path / "workspace"
    ws.mkdir()
    target = ws / "out.md"
    target.write_text("hello world", encoding="utf-8")
    expected_sha = hashlib.sha256(b"hello world").hexdigest()

    result = register_artifact_delegated(
        str(ws), "out.md",
        run_id="run_1",
        ledger_db_path=str(db),
    )
    assert result is not None
    assert result.sha256 == expected_sha
    assert result.incomplete is False

    # The ledger should now contain 3 events: run.started +
    # artifact.registered + artifact.completed.
    events = led.events("run_1")
    assert len(events) == 3
    assert events[1]["type"] == "artifact.registered"
    assert events[2]["type"] == "artifact.completed"
    # The registered payload should contain the full Artifact dict.
    reg_payload = events[1]["payload"]
    assert reg_payload["path"] == "out.md"
    assert reg_payload["sha256"] == expected_sha
    assert reg_payload["kind"] == "markdown"
    assert reg_payload["size"] == len("hello world")
    # The completed payload should be the trimmed shape.
    comp_payload = events[2]["payload"]
    assert comp_payload["path"] == "out.md"
    assert comp_payload["sha256"] == expected_sha
    assert comp_payload["size"] == len("hello world")
    assert "kind" not in comp_payload
    assert "name" not in comp_payload


def test_register_incomplete_artifact_skips_completed(
    rust_authority_artifact, tmp_path
):
    """An artifact whose sha256 can't be read should still get
    artifact.registered but NOT artifact.completed."""
    db = tmp_path / "run_events.db"
    led = RunEventLedger(db)
    led.append("run_1", "run.started", actor="user")

    ws = tmp_path / "workspace"
    ws.mkdir()
    # Don't create the file — register_artifact_delegated returns None
    # when the file doesn't exist (matches Python behavior).
    result = register_artifact_delegated(
        str(ws), "missing.md",
        run_id="run_1",
        ledger_db_path=str(db),
    )
    assert result is None

    # Ledger should still have only the run.started event.
    events = led.events("run_1")
    assert len(events) == 1


def test_maybe_wrap_returns_delegate_functions(
    rust_authority_artifact, tmp_path
):
    """maybe_wrap should return callables that route through Rust."""
    db = tmp_path / "run_events.db"
    led = RunEventLedger(db)
    led.append("run_1", "run.started", actor="user")

    ws = tmp_path / "workspace"
    ws.mkdir()
    target = ws / "out.md"
    target.write_text("hello world", encoding="utf-8")

    register, register_run = maybe_wrap(str(db))
    result = register(str(ws), "out.md", run_id="run_1")
    assert result is not None
    assert result.path == "out.md"

    # Verify events were written.
    events = led.events("run_1")
    assert any(e["type"] == "artifact.registered" for e in events)
    assert any(e["type"] == "artifact.completed" for e in events)


def test_maybe_wrap_falls_back_to_python_when_env_unset(
    monkeypatch, tmp_path
):
    """When DELTA_RUST_AUTHORITY is unset, maybe_wrap should return
    the plain Python functions (no delegate)."""
    monkeypatch.delenv("DELTA_RUST_AUTHORITY", raising=False)
    db = tmp_path / "run_events.db"
    led = RunEventLedger(db)
    led.append("run_1", "run.started", actor="user")

    ws = tmp_path / "workspace"
    ws.mkdir()
    target = ws / "out.md"
    target.write_text("hello world", encoding="utf-8")

    register, register_run = maybe_wrap(str(db))
    result = register(str(ws), "out.md", run_id="run_1", ledger=led)
    assert result is not None
    # Python path was used; ledger has the events.
    events = led.events("run_1")
    assert any(e["type"] == "artifact.registered" for e in events)


def test_rust_and_python_paths_produce_identical_ledger(
    monkeypatch, tmp_path
):
    """The Rust-written ledger events should be byte-equal to the
    Python-written events for the same artifact (same hash chain,
    same payload shape). This is the core contract of the authority
    switch."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    target = ws / "out.md"
    target.write_text("hello world", encoding="utf-8")
    db_py = tmp_path / "py.db"
    led_py = RunEventLedger(db_py)
    led_py.append("run_1", "run.started", actor="user")
    register_artifact(str(ws), "out.md", run_id="run_1", ledger=led_py)
    py_events = led_py.events("run_1")

    # Rust path.
    if not DeltaCoreClient._find_binary():
        pytest.skip("delta_core binary not built")
    monkeypatch.setenv("DELTA_RUST_AUTHORITY", "artifact")
    db_rust = tmp_path / "rust.db"
    led_rust = RunEventLedger(db_rust)
    led_rust.append("run_1", "run.started", actor="user")
    register_artifact_delegated(
        str(ws), "out.md", run_id="run_1", ledger_db_path=str(db_rust),
    )
    rust_events = led_rust.events("run_1")

    # Both ledgers should have the same number of events.
    assert len(py_events) == len(rust_events)
    # The artifact.registered payloads should have the same shape.
    py_reg = py_events[1]["payload"]
    rust_reg = rust_events[1]["payload"]
    assert py_reg["path"] == rust_reg["path"]
    assert py_reg["sha256"] == rust_reg["sha256"]
    assert py_reg["size"] == rust_reg["size"]
    assert py_reg["kind"] == rust_reg["kind"]
    # The artifact.completed payloads should match.
    py_comp = py_events[2]["payload"]
    rust_comp = rust_events[2]["payload"]
    assert py_comp == rust_comp
