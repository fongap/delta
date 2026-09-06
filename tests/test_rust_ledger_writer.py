"""Cross-language consistency: Rust writes ledger events → Python reads them.

R1 Ledger authority switch (ADR-015). The Rust LedgerWriter writes
hash-chained events to a SQLite DB. The Python RunEventLedger reads
the same DB and the hash chain must verify.

This test builds the Rust ``write_ledger`` binary on first run. If
the Rust toolchain is unavailable, the test skips (not fail) — it is
a cross-language integration test, not a Python unit test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"
BINARY = CRATE_DIR / "target" / "debug" / "write_ledger"
BINARY_EXE = CRATE_DIR / "target" / "debug" / "write_ledger.exe"


def _cargo_available() -> bool:
    return shutil.which("cargo") is not None


def _build_binary() -> Path | None:
    target = BINARY_EXE if sys.platform == "win32" else BINARY
    if target.exists():
        return target
    if not _cargo_available():
        return None
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(CRATE_DIR / "target")
    result = subprocess.run(
        ["cargo", "build", "--bin", "write_ledger"],
        cwd=CRATE_DIR,
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        return None
    return target if target.exists() else None


@pytest.fixture(scope="module")
def rust_binary():
    binary = _build_binary()
    if binary is None:
        pytest.skip("Rust toolchain or write_ledger binary not available")
    return binary


def _run_write(
    binary: Path,
    db: str,
    run_id: str,
    event_type: str,
    actor: str,
    ts: float,
    **kw,
) -> dict:
    args = [
        str(binary),
        "--db",
        db,
        "--run-id",
        run_id,
        "--type",
        event_type,
        "--actor",
        actor,
        "--ts",
        str(ts),
    ]
    if "payload" in kw and kw["payload"] is not None:
        args += ["--payload", json.dumps(kw["payload"])]
    if "workspace" in kw and kw["workspace"]:
        args += ["--workspace", kw["workspace"]]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, f"write_ledger failed: {result.stderr}"
    return json.loads(result.stdout)


def test_rust_writes_run_started_python_reads(rust_binary):
    from core.ledger import RunEventLedger

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "run-events.db"
        row = _run_write(
            rust_binary,
            str(db),
            "run_a",
            "run.started",
            "user",
            1000.0,
            payload={"kind": "run"},
        )
        assert row["seq"] == 1
        assert row["prev_hash"] == ""

        ledger = RunEventLedger(db)
        events = ledger.events("run_a")
        ledger.close()
        assert len(events) == 1
        assert events[0]["type"] == "run.started"
        assert events[0]["actor"] == "user"


def test_rust_writes_hash_chain_verifies_via_python(rust_binary):
    from core.ledger import RunEventLedger

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "run-events.db"
        _run_write(
            rust_binary,
            str(db),
            "run_b",
            "run.started",
            "user",
            1000.0,
            payload={"kind": "run"},
        )
        _run_write(
            rust_binary,
            str(db),
            "run_b",
            "tool.proposed",
            "tool",
            1001.0,
            payload={"tool": "read_file"},
        )
        _run_write(
            rust_binary,
            str(db),
            "run_b",
            "run.completed",
            "system",
            1002.0,
            payload={"kind": "run"},
        )

        ledger = RunEventLedger(db)
        try:
            assert ledger.verify("run_b") is True
            events = ledger.events("run_b")
            assert len(events) == 3
            assert events[0]["prev_hash"] == ""
            assert events[1]["prev_hash"] == events[0]["hash"]
            assert events[2]["prev_hash"] == events[1]["hash"]
        finally:
            ledger.close()


def test_rust_writes_workspace_column(rust_binary):
    from core.ledger import RunEventLedger

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "run-events.db"
        _run_write(
            rust_binary,
            str(db),
            "run_c",
            "run.started",
            "user",
            1000.0,
            payload={},
            workspace="ws_1",
        )
        _run_write(
            rust_binary,
            str(db),
            "run_c",
            "run.completed",
            "system",
            1001.0,
            payload={},
            workspace="ws_1",
        )
        _run_write(
            rust_binary,
            str(db),
            "run_d",
            "run.started",
            "user",
            2000.0,
            payload={},
            workspace="ws_2",
        )

        ledger = RunEventLedger(db)
        try:
            ws1 = ledger.events_in_workspace("run_c", "ws_1")
            assert len(ws1) == 2
            assert all(e["workspace"] == "ws_1" for e in ws1)
        finally:
            ledger.close()


def test_rust_writes_runs_listing(rust_binary):
    from core.ledger import RunEventLedger

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "run-events.db"
        _run_write(rust_binary, str(db), "run_e", "run.started", "user", 1000.0, payload={})
        _run_write(rust_binary, str(db), "run_f", "run.started", "user", 1000.0, payload={})

        ledger = RunEventLedger(db)
        try:
            runs = ledger.runs()
            assert "run_e" in runs
            assert "run_f" in runs
        finally:
            ledger.close()


def test_rust_writer_hash_matches_python_writer(rust_binary):
    """Rust and Python writers must produce byte-identical hash chains for
    the same input."""
    from core.ledger import RunEventLedger

    with tempfile.TemporaryDirectory() as tmp:
        # Rust writes
        db_rust = Path(tmp) / "rust.db"
        _run_write(
            rust_binary,
            str(db_rust),
            "run_x",
            "run.started",
            "user",
            1000.0,
            payload={"kind": "run"},
        )
        _run_write(
            rust_binary,
            str(db_rust),
            "run_x",
            "run.completed",
            "system",
            1001.0,
            payload={"kind": "run"},
        )

        # Python writes
        db_py = Path(tmp) / "py.db"
        py_ledger = RunEventLedger(db_py)
        py_ledger.append("run_x", "run.started", actor="user", payload={"kind": "run"}, ts=1000.0)
        py_ledger.append("run_x", "run.completed", actor="system", payload={"kind": "run"}, ts=1001.0)
        py_ledger.close()

        # Compare hash chains
        rust_ledger = RunEventLedger(db_rust)
        py_ledger = RunEventLedger(db_py)
        try:
            rust_events = rust_ledger.events("run_x")
            py_events = py_ledger.events("run_x")
            assert len(rust_events) == len(py_events) == 2
            for r, p in zip(rust_events, py_events):
                assert r["hash"] == p["hash"]
                assert r["prev_hash"] == p["prev_hash"]
        finally:
            rust_ledger.close()
            py_ledger.close()
