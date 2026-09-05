"""Cross-language consistency: Python writes side-effects → Rust reads state machine.

R1 State Foundation shadow-read proof (ADR-010) for IdempotencyLog. The
Python IdempotencyLog writes a state machine to a SQLite DB. The Rust
IdempotencyReader opens the same DB read-only and reports the same state
for each (run_id, tool_call_id).

This test builds the Rust ``dump_idemlog`` binary on first run and caches
it under ``target/``. If the Rust toolchain is unavailable, the test skips
(not fail) — it is a cross-language integration test, not a Python unit test.
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
BINARY = CRATE_DIR / "target" / "debug" / "dump_idemlog"
BINARY_EXE = CRATE_DIR / "target" / "debug" / "dump_idemlog.exe"


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
        ["cargo", "build", "--bin", "dump_idemlog"],
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
        pytest.skip("Rust toolchain or dump_idemlog binary not available")
    return binary


def _run_dump(binary: Path, db_path: str, run_id: str, filter: str = "all") -> list[dict]:
    result = subprocess.run(
        [str(binary), "--db", db_path, "--run-id", run_id, "--filter", filter],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"dump_idemlog failed: {result.stderr}"
    return json.loads(result.stdout)


def _compare_tool_call_ids(python_entries: list[dict], rust_entries: list[dict]) -> None:
    py_ids = {e["tool_call_id"] for e in python_entries}
    rust_ids = {e["tool_call_id"] for e in rust_entries}
    assert py_ids == rust_ids, (
        f"tool_call_id mismatch\n"
        f"  only-in-python: {py_ids - rust_ids}\n"
        f"  only-in-rust:   {rust_ids - py_ids}"
    )


def _rust_states(rust_entries: list[dict]) -> dict[str, str]:
    return {e["tool_call_id"]: e["state"] for e in rust_entries}


def test_python_committed_rust_reads_committed(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        log = IdempotencyLog(db)

        run_id = "run_idem_a"
        log.record_planned(run_id, "tc_1", "read_file", {"path": "a.txt"})
        log.mark_executing(run_id, "tc_1")
        log.commit(run_id, "tc_1", "read_file", {"path": "a.txt"}, {"text": "hello"})

        py_committed = log.committed_for_run(run_id)
        log.close()

        rust_committed = _run_dump(rust_binary, str(db), run_id, "committed")
        _compare_tool_call_ids(py_committed, rust_committed)
        states = _rust_states(rust_committed)
        assert all(s == "committed" for s in states.values())


def test_python_uncommitted_rust_reads_uncommitted(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        log = IdempotencyLog(db)

        run_id = "run_idem_b"
        log.record_planned(run_id, "tc_1", "write_file", {"path": "x.txt"})
        log.record_planned(run_id, "tc_2", "delete_file", {"path": "y.txt"})
        log.mark_executing(run_id, "tc_1")

        py_uncommitted = log.uncommitted_for_run(run_id)
        log.close()

        rust_uncommitted = _run_dump(rust_binary, str(db), run_id, "uncommitted")
        _compare_tool_call_ids(py_uncommitted, rust_uncommitted)
        states = _rust_states(rust_uncommitted)
        assert all(s in ("planned", "executing") for s in states.values())


def test_python_uncertain_rust_reads_uncertain(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        log = IdempotencyLog(db)

        run_id = "run_idem_c"
        log.record_planned(run_id, "tc_1", "send_message", {"to": "alice"})
        log.mark_executing(run_id, "tc_1")
        log.mark_uncertain(run_id, "tc_1")

        py_uncertain = log.uncertain_for_run(run_id)
        log.close()

        rust_uncertain = _run_dump(rust_binary, str(db), run_id, "uncertain")
        _compare_tool_call_ids(py_uncertain, rust_uncertain)
        states = _rust_states(rust_uncertain)
        assert all(s == "uncertain" for s in states.values())


def test_python_failed_rust_reads_failed(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        log = IdempotencyLog(db)

        run_id = "run_idem_d"
        log.record_planned(run_id, "tc_1", "write_file", {"path": "x.txt"})
        log.mark_executing(run_id, "tc_1")
        log.mark_failed(run_id, "tc_1", error="disk full")
        log.close()

        # Python has no "failed_for_run" query; Rust's `for_run` sees all
        # rows including failed. We assert Rust reports the failed state
        # that Python wrote.
        rust_all = _run_dump(rust_binary, str(db), run_id, "all")
        assert len(rust_all) == 1
        assert rust_all[0]["tool_call_id"] == "tc_1"
        assert rust_all[0]["state"] == "failed"

        assert any(e["state"] == "failed" for e in rust_all)


def test_python_sweep_stale_rust_agrees(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        log = IdempotencyLog(db)

        run_id = "run_idem_e"
        log.record_planned(run_id, "tc_1", "send_message", {"to": "bob"})
        log.mark_executing(run_id, "tc_1")

        log.sweep_stale([run_id])

        py_uncertain = log.uncertain_for_run(run_id)
        log.close()

        rust_uncertain = _run_dump(rust_binary, str(db), run_id, "uncertain")
        _compare_tool_call_ids(py_uncertain, rust_uncertain)
        assert len(rust_uncertain) == 1
        assert rust_uncertain[0]["state"] == "uncertain"


def test_python_operation_id_matches_rust(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        log = IdempotencyLog(db)

        run_id = "run_idem_f"
        log.record_planned(run_id, "tc_42", "read_file", {"path": "z.txt"})

        py_committed = log.committed_for_run(run_id)
        py_uncommitted = log.uncommitted_for_run(run_id)
        py_uncertain = log.uncertain_for_run(run_id)
        py_all = py_committed + py_uncommitted + py_uncertain
        log.close()

        rust_all = _run_dump(rust_binary, str(db), run_id, "all")
        assert len(rust_all) == 1
        assert rust_all[0]["operation_id"] == py_all[0]["operation_id"]
        assert len(rust_all[0]["operation_id"]) == 36


def test_python_empty_run_rust_returns_empty(rust_binary):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "side-effects.db"
        IdempotencyLog(db).close()

        result = _run_dump(rust_binary, str(db), "run_nonexistent", "all")
        assert result == []
