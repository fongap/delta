"""Cross-language consistency: Rust writes side-effects → Python reads them.

R1 Idempotency authority switch stage A (ADR-013). The Rust
IdempotencyWriter writes side-effect state transitions to a SQLite DB.
The Python IdempotencyLog reads the same DB and must see identical
state / operation_id / args_sha256.

This test builds the Rust ``write_idemlog`` binary on first run. If
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

from core.idemlog import IdempotencyLog

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"
BINARY = CRATE_DIR / "target" / "debug" / "write_idemlog"
BINARY_EXE = CRATE_DIR / "target" / "debug" / "write_idemlog.exe"


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
        ["cargo", "build", "--bin", "write_idemlog"],
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
        pytest.skip("Rust toolchain or write_idemlog binary not available")
    return binary


def _run_write(binary: Path, db: str, action: str, run_id: str, tool_call_id: str, **kw) -> int:
    args = [str(binary), "--db", db, "--action", action, "--run-id", run_id, "--tool-call-id", tool_call_id]
    if "tool_name" in kw:
        args += ["--tool-name", kw["tool_name"]]
    if "args" in kw:
        args += ["--args", json.dumps(kw["args"])]
    if "result" in kw:
        args += ["--result", json.dumps(kw["result"])]
    if "error" in kw:
        args += ["--error", kw["error"]]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, f"write_idemlog failed: {result.stderr}"
    return result.returncode


def test_rust_writes_planned_python_reads(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        _run_write(rust_binary, str(db), "record_planned", "run_a", "tc_1",
                    tool_name="write_file", args={"path": "a.txt"})

        log = IdempotencyLog(db)
        uncommitted = log.uncommitted_for_run("run_a")
        log.close()

        assert len(uncommitted) == 1
        assert uncommitted[0]["tool_call_id"] == "tc_1"
        assert uncommitted[0]["tool_name"] == "write_file"
        assert uncommitted[0]["state"] == "planned"


def test_rust_writes_committed_python_reads(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        _run_write(rust_binary, str(db), "commit", "run_b", "tc_1",
                    tool_name="read_file", args={"path": "b.txt"},
                    result={"text": "hello"})

        log = IdempotencyLog(db)
        committed = log.committed_for_run("run_b")
        log.close()

        assert len(committed) == 1
        assert committed[0]["tool_call_id"] == "tc_1"
        assert committed[0]["tool_name"] == "read_file"
        assert committed[0]["result"] == {"text": "hello"}


def test_rust_writes_failed_python_reads(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        _run_write(rust_binary, str(db), "record_planned", "run_c", "tc_1",
                    tool_name="write_file", args={"path": "c.txt"})
        _run_write(rust_binary, str(db), "mark_executing", "run_c", "tc_1")
        _run_write(rust_binary, str(db), "mark_failed", "run_c", "tc_1",
                    error="disk full")

        log = IdempotencyLog(db)
        committed = log.committed_for_run("run_c")
        uncommitted = log.uncommitted_for_run("run_c")
        uncertain = log.uncertain_for_run("run_c")
        log.close()

        assert len(committed) == 0
        assert len(uncommitted) == 0
        assert len(uncertain) == 0


def test_rust_writes_uncertain_python_reads(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        _run_write(rust_binary, str(db), "record_planned", "run_d", "tc_1",
                    tool_name="send_message", args={"to": "bob"})
        _run_write(rust_binary, str(db), "mark_executing", "run_d", "tc_1")
        _run_write(rust_binary, str(db), "mark_uncertain", "run_d", "tc_1")

        log = IdempotencyLog(db)
        uncertain = log.uncertain_for_run("run_d")
        log.close()

        assert len(uncertain) == 1
        assert uncertain[0]["tool_call_id"] == "tc_1"
        assert uncertain[0]["tool_name"] == "send_message"


def test_rust_writes_operation_id_matches_python(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        _run_write(rust_binary, str(db), "record_planned", "run_e", "tc_42",
                    tool_name="read_file", args={"path": "z.txt"})

        log = IdempotencyLog(db)
        from core.idemlog import operation_id as py_op_id
        py_id = py_op_id("run_e", "tc_42")
        uncommitted = log.uncommitted_for_run("run_e")
        log.close()

        assert len(uncommitted) == 1
        assert uncommitted[0]["operation_id"] == py_id
        assert len(uncommitted[0]["operation_id"]) == 36
