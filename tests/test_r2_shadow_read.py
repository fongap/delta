"""Cross-language shadow-read tests for R2 readers (ADR-019).

For each R2 domain that has both a Python authority and a Rust
reader, this test:

1. Performs a write through the **Python** code path (which is
   still the only writer; the Rust reader is shadow-only).
2. Invokes the **Rust** inspect binary (or library) to read the
   same state.
3. Asserts the two views agree on the structural facts (sha256,
   row count, schema version, citation shape).

If the Python authority and the Rust reader disagree, the Rust
reader is a shadow that's seeing something different from the
authoritative source — that is a regression in the contract.

The tests do NOT use ``DELTA_RUST_READERS`` — the Rust reader is
invoked directly via subprocess / library call, not through a
production gate. Production callers wire the env var in
``core/*_reader.py`` modules added by per-domain R2 ADRs.

Contract: ``docs/architecture/adr/ADR-019-r2-pre-plumbing.md``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CRATE = REPO / "core" / "runtime-native"
BINARY_DIR = CRATE / "target" / "release"


def _binary(name: str) -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    p = BINARY_DIR / f"{name}{suffix}"
    if not p.exists():
        p = CRATE / "target" / "debug" / f"{name}{suffix}"
    if not p.exists():
        pytest.skip(f"Rust binary {name} not built; run `cargo build --release`")
    return p


# -- artifact reader --------------------------------------------------------


def test_artifact_inspect_reports_empty_run(tmp_path):
    """A run with no artifacts should report an empty list."""
    from core.ledger import RunEventLedger

    db = tmp_path / "run_events.db"
    led = RunEventLedger(db)
    led.append("run_1", "run.started", actor="user")

    out = subprocess.run(
        [str(_binary("inspect_artifact")), "--db", str(db), "--run-id", "run_1"],
        capture_output=True, text=True, check=True,
    )
    report = json.loads(out.stdout)
    assert report["run_id"] == "run_1"
    assert report["artifact_count"] == 0
    assert report["mismatch_count"] == 0
    assert report["artifacts"] == []


def test_artifact_inspect_lists_registered_artifact(tmp_path):
    """An artifact.registered event should surface in inspect_artifact."""
    from core.artifact import register_artifact
    from core.ledger import RunEventLedger

    db = tmp_path / "run_events.db"
    led = RunEventLedger(db)
    ws = tmp_path / "workspace"
    ws.mkdir()
    target = ws / "out.md"
    target.write_text("hello world", encoding="utf-8")

    register_artifact(str(ws), "out.md", run_id="run_1", ledger=led)
    expected_sha = hashlib.sha256(b"hello world").hexdigest()

    out = subprocess.run(
        [
            str(_binary("inspect_artifact")),
            "--db", str(db), "--run-id", "run_1",
            "--workspace", str(ws),
        ],
        capture_output=True, text=True, check=True,
    )
    report = json.loads(out.stdout)
    assert report["artifact_count"] == 1
    assert report["mismatch_count"] == 0
    a = report["artifacts"][0]
    assert a["path"] == "out.md"
    assert a["sha256"] == expected_sha
    assert a["incomplete"] is False


def test_artifact_inspect_detects_workspace_tampering(tmp_path):
    """If a file is modified after registration, inspect_artifact should
    report a sha256 mismatch — proving the Rust reader actually walks
    the workspace and re-hashes."""
    from core.artifact import register_artifact
    from core.ledger import RunEventLedger

    db = tmp_path / "run_events.db"
    led = RunEventLedger(db)
    ws = tmp_path / "workspace"
    ws.mkdir()
    target = ws / "out.md"
    target.write_text("hello world", encoding="utf-8")

    register_artifact(str(ws), "out.md", run_id="run_1", ledger=led)

    # Tamper with the file AFTER registration.
    target.write_text("goodbye world", encoding="utf-8")

    out = subprocess.run(
        [
            str(_binary("inspect_artifact")),
            "--db", str(db), "--run-id", "run_1",
            "--workspace", str(ws),
        ],
        capture_output=True, text=True, check=True,
    )
    report = json.loads(out.stdout)
    assert report["mismatch_count"] == 1
    m = report["mismatches"][0]
    assert m["path"] == "out.md"
    assert m["reason"] == "sha256 mismatch"


# -- validation reader ------------------------------------------------------


def test_validation_library_matches_python_for_passing_run():
    """The Rust validation library should agree with the Python
    implementation on a run that passes all gates."""
    from core.validation import DEFAULT_CRITERIA, run_validation

    artifacts = [
        {
            "path": "out.md",
            "name": "out.md",
            "kind": "markdown",
            "size": 100,
            "modified_at": 0.0,
            "run_id": "run_1",
            "sha256": "abc",
            "incomplete": False,
        }
    ]
    py_result = run_validation(artifacts, DEFAULT_CRITERIA)
    assert py_result.ok

    # Cross-check via the inspect_validation CLI (subprocess wrapper
    # around the Rust reimplementation).
    payload = {
        "criteria": DEFAULT_CRITERIA.to_dict(),
        "artifacts": [a.to_dict() if hasattr(a, "to_dict") else a for a in artifacts],
    }
    out = subprocess.run(
        [str(_binary("inspect_validation"))],
        input=json.dumps(payload), text=True, capture_output=True, check=True,
    )
    rust_report = json.loads(out.stdout)
    assert rust_report["ok"] is True
    assert rust_report["evidence"] == json.loads(json.dumps(py_result.evidence))


def test_validation_library_agrees_on_failure_modes():
    """The Rust and Python validators should agree on which checks
    pass and which fail for the same inputs."""
    from core.validation import ValidationCriteria, run_validation

    criteria = ValidationCriteria(
        min_artifacts=2,
        max_artifacts=5,
        required_paths=["out.md", "summary.csv"],
        min_size={"out.md": 50},
    )
    artifacts = [
        {
            "path": "out.md", "name": "out.md", "kind": "markdown",
            "size": 10, "modified_at": 0.0, "run_id": "run_1",
            "sha256": "x", "incomplete": False,
        },
        {
            "path": "other.md", "name": "other.md", "kind": "markdown",
            "size": 10, "modified_at": 0.0, "run_id": "run_1",
            "sha256": "y", "incomplete": False,
        },
    ]
    py_result = run_validation(artifacts, criteria)

    payload = {
        "criteria": criteria.to_dict(),
        "artifacts": artifacts,
    }
    out = subprocess.run(
        [str(_binary("inspect_validation"))],
        input=json.dumps(payload), text=True, capture_output=True, check=True,
    )
    rust_report = json.loads(out.stdout)

    # Both should fail, and on the same check.
    assert not py_result.ok
    assert rust_report["ok"] is False
    py_fail_names = {c["name"] for c in py_result.to_dict()["checks"] if not c["ok"]}
    rust_fail_names = {c["name"] for c in rust_report["checks"] if not c["ok"]}
    assert py_fail_names == rust_fail_names


# -- checkpoint reader ------------------------------------------------------


def test_checkpoint_inspect_rejects_wrong_schema(tmp_path):
    """A snapshot with an unknown schema must be rejected (the Rust
    reader refuses to silently misinterpret future versions)."""
    snap = {
        "schema": 999,  # future schema the Rust reader doesn't know
        "phase": "running",
    }
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(json.dumps(snap), encoding="utf-8")

    result = subprocess.run(
        [str(_binary("inspect_checkpoint")), str(snap_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "schema" in result.stderr.lower()


def test_checkpoint_inspect_accepts_current_schema(tmp_path):
    """A snapshot at the current schema version must parse and
    report structural counts (todos, recent_artifacts)."""
    snap = {
        "schema": 1,
        "snapshot_at": "2026-09-07T00:00:00Z",
        "run_id": "run_1",
        "session_id": "sess_1",
        "phase": "running",
        "last_event_seq": 5,
        "todo_summary": [
            {"content": "do X", "status": "pending", "active_form": "doing X"},
            {"content": "do Y", "status": "completed", "active_form": "doing Y"},
        ],
        "recent_artifacts": [
            {"path": "a.md", "kind": "markdown"},
        ],
    }
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(json.dumps(snap), encoding="utf-8")

    result = subprocess.run(
        [str(_binary("inspect_checkpoint")), str(snap_path)],
        capture_output=True, text=True, check=True,
    )
    report = json.loads(result.stdout)
    assert report["schema"] == 1
    assert report["run_id"] == "run_1"
    assert report["phase"] == "running"
    assert report["todo_count"] == 2
    assert report["recent_artifact_count"] == 1


# -- citation reader -------------------------------------------------------


def test_citation_inspect_validates_lines_citation():
    """A well-formed lines citation should pass inspection."""
    citations = [{"kind": "lines", "start": 1, "end": 5}]
    result = subprocess.run(
        [str(_binary("inspect_citation")), "-"],
        input=json.dumps(citations), text=True, capture_output=True, check=True,
    )
    report = json.loads(result.stdout)
    assert report["count"] == 1
    assert report["citations"][0]["kind"] == "lines"


def test_citation_inspect_rejects_missing_field():
    citations = [{"kind": "lines", "start": 1}]
    result = subprocess.run(
        [str(_binary("inspect_citation")), "-"],
        input=json.dumps(citations), text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "end" in result.stderr or "missing" in result.stderr.lower()


def test_citation_inspect_rejects_unknown_kind():
    citations = [{"kind": "made_up", "x": 1}]
    result = subprocess.run(
        [str(_binary("inspect_citation")), "-"],
        input=json.dumps(citations), text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "kind" in result.stderr.lower()


def test_citation_inspect_short_circuits_on_first_error():
    """validate_all short-circuits at the first bad entry, matching
    Python's normalize_cited_ranges behavior."""
    citations = [
        {"kind": "lines", "start": 1, "end": 2},
        {"kind": "bogus"},
        {"kind": "lines", "start": 3, "end": 4},
    ]
    result = subprocess.run(
        [str(_binary("inspect_citation")), "-"],
        input=json.dumps(citations), text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "kind" in result.stderr.lower()


def test_citation_inspect_agrees_with_python_for_each_kind():
    """For every kind the Rust reader accepts, the Python validator
    should also accept a well-formed entry of the same kind."""
    from core.sources import to_range_dict

    cases = [
        {"kind": "lines", "start": 1, "end": 3},
        {"kind": "page", "page": 2},
        {"kind": "cells", "sheet": "S1", "cell_start": "A1", "cell_end": "B2"},
        {"kind": "row", "sheet": "S1", "row_start": 1, "row_end": 5},
        {"kind": "column", "sheet": "S1", "col_start": 1, "col_end": 5},
        {"kind": "sheet", "sheet": "OnlySheet"},
        {"kind": "message_id", "message_id": "m1"},
        {"kind": "custom", "descriptor": {"foo": "bar"}},
    ]
    for case in cases:
        # Python side: to_range_dict should not raise.
        py_out = to_range_dict(case)
        assert py_out["kind"] == case["kind"]

        # Rust side: inspect_citation should exit 0.
        result = subprocess.run(
            [str(_binary("inspect_citation")), "-"],
            input=json.dumps([case]), text=True, capture_output=True,
        )
        assert result.returncode == 0, f"Rust rejected {case}: {result.stderr}"
