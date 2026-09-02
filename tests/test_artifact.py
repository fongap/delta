"""Tests for ADR-005 WS2: Artifact domain object.

The Artifact replaces the old `list[str]` of file paths. The new contract:

- `Artifact` carries `path, name, kind, size, sha256, run_id, incomplete`
- `register_run_artifacts` walks the workspace and computes sha256 per file
- Files that fail to read (truncated, vanished) are marked `incomplete=True`
- Each artifact emits an `artifact.registered` + `artifact.completed` ledger event
- The walk skips hidden / VCS / build directories
- Old `list[str]` rows in `automation.db` upgrade to bare dicts on load
"""

from __future__ import annotations

import hashlib
import os
import time

import pytest

from core.artifact import Artifact, _sha256_of, register_run_artifacts
from core.automation.models import TaskRun
from core.ledger import RunEventLedger


@pytest.fixture
def ledger(tmp_path_factory) -> RunEventLedger:
    inst = RunEventLedger(tmp_path_factory.mktemp("ledger") / "run-events.db")
    yield inst
    inst.close()


def test_artifact_round_trip_preserves_all_fields():
    a = Artifact(
        path="report.md",
        name="report.md",
        kind="markdown",
        size=42,
        modified_at=1.0,
        run_id="run-1",
        sha256="abc",
    )
    d = a.to_dict()
    b = Artifact.from_dict(d)
    assert a == b


def test_sha256_of_matches_hashlib(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world")
    assert _sha256_of(p) == hashlib.sha256(b"hello world").hexdigest()


def test_register_run_artifacts_emits_ledger_events(tmp_path, ledger):
    (tmp_path / "report.md").write_text("# Hello\n")
    (tmp_path / "data.csv").write_text("a,b\n1,2\n")

    artifacts = register_run_artifacts(
        str(tmp_path), run_id="run-1", since=0.0, ledger=ledger
    )
    assert len(artifacts) == 2
    paths = {a.path for a in artifacts}
    assert paths == {"report.md", "data.csv"}
    # sha256 was computed for both
    for a in artifacts:
        assert a.sha256 and len(a.sha256) == 64
        assert a.incomplete is False
        assert a.size > 0

    # Ledger recorded register + complete for each (4 events total)
    types = [e["type"] for e in ledger.events("run-1")]
    assert types.count("artifact.registered") == 2
    assert types.count("artifact.completed") == 2
    print("DEBUG artifacts:", [a.path for a in artifacts])


def test_register_run_artifacts_skips_hidden_and_build_dirs(tmp_path, ledger):
    (tmp_path / "report.md").write_text("ok")
    (tmp_path / ".delta").mkdir()
    (tmp_path / ".delta" / "core.db").write_text("state")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("git")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("js")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"pyc")

    artifacts = register_run_artifacts(
        str(tmp_path), run_id="run-1", since=0.0, ledger=ledger
    )
    paths = {a.path for a in artifacts}
    assert paths == {"report.md"}


def test_register_run_artifacts_marks_unreadable_files_incomplete(tmp_path, ledger, monkeypatch):
    p = tmp_path / "truncated.md"
    p.write_text("partial")
    # Force _sha256_of to raise on this one file so incomplete=True is set.
    from core import artifact

    real = artifact._sha256_of

    def flaky(path):
        if path.name == "truncated.md":
            raise OSError("simulated truncation")
        return real(path)

    monkeypatch.setattr(artifact, "_sha256_of", flaky)
    artifacts = register_run_artifacts(
        str(tmp_path), run_id="run-1", since=0.0, ledger=ledger
    )
    by_path = {a.path: a for a in artifacts}
    assert by_path["truncated.md"].incomplete is True
    assert by_path["truncated.md"].sha256 is None


def test_register_run_artifacts_respects_since(tmp_path, ledger):
    old = tmp_path / "old.md"
    old.write_text("old")
    past = time.time() - 60
    os.utime(old, (past, past))
    (tmp_path / "new.md").write_text("new")

    artifacts = register_run_artifacts(
        str(tmp_path), run_id="run-1", since=time.time() - 5
    )
    paths = {a.path for a in artifacts}
    assert paths == {"new.md"}


def test_register_run_artifacts_works_without_ledger(tmp_path):
    (tmp_path / "report.md").write_text("# x")
    artifacts = register_run_artifacts(
        str(tmp_path), run_id="run-1", since=0.0, ledger=None
    )
    assert len(artifacts) == 1
    assert artifacts[0].sha256


def test_task_run_from_dict_upgrades_legacy_list_str_artifacts():
    """Pre-WS2 rows in automation.db stored `artifacts: list[str]`. Loading
    them must succeed and produce dict-shaped entries flagged for upgrade."""
    old = {
        "task_id": "t1",
        "run_id": "r1",
        "started_at": 0.0,
        "finished_at": 0.0,
        "status": "ok",
        "result_text": None,
        "artifacts": ["report.md", "data.csv"],
        "error": None,
        "trigger": "schedule",
        "session_id": "__run__r1",
    }
    run = TaskRun.from_dict(old)
    assert run.artifacts == [
        {
            "path": "report.md",
            "name": "report.md",
            "kind": "text",
            "size": 0,
            "modified_at": 0.0,
            "run_id": "r1",
            "sha256": None,
            "incomplete": True,
        },
        {
            "path": "data.csv",
            "name": "data.csv",
            "kind": "text",
            "size": 0,
            "modified_at": 0.0,
            "run_id": "r1",
            "sha256": None,
            "incomplete": True,
        },
    ]


def test_task_run_from_dict_accepts_new_dict_artifacts():
    run = TaskRun.from_dict(
        {
            "task_id": "t1",
            "run_id": "r1",
            "started_at": 0.0,
            "finished_at": 0.0,
            "status": "ok",
            "result_text": None,
            "artifacts": [
                {
                    "path": "x.md",
                    "name": "x.md",
                    "kind": "markdown",
                    "size": 10,
                    "modified_at": 0.0,
                    "run_id": "r1",
                    "sha256": "deadbeef",
                    "incomplete": False,
                }
            ],
            "error": None,
            "trigger": "schedule",
            "session_id": "__run__r1",
        }
    )
    assert run.artifacts[0]["kind"] == "markdown"
    assert run.artifacts[0]["sha256"] == "deadbeef"
