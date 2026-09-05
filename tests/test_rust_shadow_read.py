"""Cross-language consistency: Python writes events → Rust verifies hash chain.

R1 State Foundation shadow-read proof (ADR-010). The Python RunEventLedger
writes hash-chained events to a SQLite DB. The Rust LedgerReader opens
the same DB read-only and recomputes the chain. This test proves they agree.

This test builds the Rust ``verify_ledger`` binary on first run and caches
it under ``target/``. If the Rust toolchain is unavailable, the test skips
(not fail) — it is a cross-language integration test, not a Python unit test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from core.ledger import RunEventLedger

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"
BINARY = CRATE_DIR / "target" / "debug" / "verify_ledger"
BINARY_EXE = CRATE_DIR / "target" / "debug" / "verify_ledger.exe"


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
        ["cargo", "build", "--bin", "verify_ledger"],
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
        pytest.skip("Rust toolchain or verify_ledger binary not available")
    return binary


def _run_verify(binary: Path, db_path: str, run_id: str) -> tuple[int, str]:
    result = subprocess.run(
        [str(binary), "--db", db_path, "--run-id", run_id],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip()


def test_python_writes_rust_verifies_clean_chain(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "run-events.db"
        ledger = RunEventLedger(db)

        run_id = "run_cross_lang_001"
        ledger.append(run_id, "run.started", actor="system", payload={"ws": "/tmp"})
        ledger.append(
            run_id,
            "tool.proposed",
            actor="system",
            payload={"tool": "read_file", "args": {"path": "a.txt"}},
        )
        ledger.append(
            run_id,
            "tool.finished",
            actor="system",
            payload={"tool": "read_file", "result": "hello"},
        )
        ledger.append(run_id, "run.completed", actor="system", payload={})

        assert ledger.verify(run_id), "Python verify should pass on clean chain"
        ledger.close()

        code, out = _run_verify(rust_binary, str(db), run_id)
        assert code == 0, f"Rust verify failed: exit={code}, stdout={out}"
        assert out == "OK"


def test_python_writes_rust_verifies_single_event(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "run-events.db"
        ledger = RunEventLedger(db)

        run_id = "run_single"
        ledger.append(run_id, "run.started", actor="system")

        assert ledger.verify(run_id)
        ledger.close()

        code, out = _run_verify(rust_binary, str(db), run_id)
        assert code == 0, f"Rust verify failed: exit={code}, stdout={out}"
        assert out == "OK"


def test_python_writes_rust_verifies_empty_run(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "run-events.db"
        RunEventLedger(db).close()

        run_id = "run_empty"

        code, out = _run_verify(rust_binary, str(db), run_id)
        assert code == 0, f"Rust verify on empty run should pass: exit={code}, stdout={out}"
        assert out == "OK"


def test_python_writes_rust_detects_tampered_chain(rust_binary):
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "run-events.db"
        ledger = RunEventLedger(db)

        run_id = "run_tampered"
        ledger.append(run_id, "run.started", actor="system", payload={"v": 1})
        ledger.append(run_id, "tool.proposed", actor="system", payload={"tool": "x"})
        ledger.append(run_id, "run.completed", actor="system")
        ledger.close()

        conn = sqlite3.connect(str(db))
        conn.execute(
            "UPDATE run_events SET payload = '{\"v\": 999}' WHERE run_id = ? AND seq = 1",
            (run_id,),
        )
        conn.commit()
        conn.close()

        ledger2 = RunEventLedger(db)
        assert not ledger2.verify(run_id), "Python should detect tamper"
        ledger2.close()

        code, out = _run_verify(rust_binary, str(db), run_id)
        assert code == 1, f"Rust should detect tamper: exit={code}, stdout={out}"
        assert out == "FAIL"


def test_rust_verifies_multiple_runs(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "run-events.db"
        ledger = RunEventLedger(db)

        for i in range(5):
            rid = f"run_multi_{i}"
            ledger.append(rid, "run.started", actor="system", payload={"index": i})
            ledger.append(rid, "run.completed", actor="system")

        for i in range(5):
            rid = f"run_multi_{i}"
            assert ledger.verify(rid), f"Python verify failed for {rid}"
        ledger.close()

        for i in range(5):
            rid = f"run_multi_{i}"
            code, out = _run_verify(rust_binary, str(db), rid)
            assert code == 0, f"Rust verify failed for {rid}: exit={code}, stdout={out}"
            assert out == "OK"
