"""Tests for ADR-005 WS3: Validation gate.

The blueprint requires:

  - "Validation 未通过 → Task 不得进入成功状态"
  - Validation must be deterministic (rule-shaped, not LLM-judged)
  - Failed validation produces `status="validation_failed"`, not `status="error"`

These tests exercise `ValidationCriteria` + `run_validation` + `gate_status`
and the manager integration that records the verdict into the run ledger.
"""

from __future__ import annotations


import pytest

from core.artifact import Artifact
from core.validation import (
    DEFAULT_CRITERIA,
    ValidationCriteria,
    ValidationResult,
    gate_status,
    run_validation,
)


def _mk(path: str, *, incomplete: bool = False, size: int = 100, run_id: str = "r1") -> Artifact:
    return Artifact(
        path=path,
        name=path.rsplit("/", 1)[-1],
        kind="text",
        size=size,
        modified_at=0.0,
        run_id=run_id,
        sha256=None if incomplete else "x" * 64,
        incomplete=incomplete,
    )


def test_default_criteria_is_permissive():
    """No declared contract = at least one artifact isn't required. The floor
    is "if there are artifacts, they must all be readable"; this matches the
    blueprint rule that the per-task author decides what counts as done."""
    result = run_validation([], DEFAULT_CRITERIA)
    assert result.ok is True


def test_min_artifacts_gate_fails_when_no_artifacts():
    """The blueprint's "produce at least one artifact" rule is opt-in through
    `min_artifacts=1` — empty runs are no longer silently accepted."""
    criteria = ValidationCriteria(min_artifacts=1)
    result = run_validation([], criteria)
    assert result.ok is False
    assert any(c.name == "artifact_count" for c in result.checks)


def test_required_paths_gate():
    criteria = ValidationCriteria(required_paths=["report.md", "data.csv"])
    result = run_validation([_mk("report.md")], criteria)
    assert result.ok is False
    fail = next(c for c in result.checks if not c.ok)
    assert "data.csv" in fail.detail


def test_incomplete_artifact_blocks_when_required():
    criteria = ValidationCriteria(require_complete=True)
    result = run_validation([_mk("partial.md", incomplete=True)], criteria)
    assert result.ok is False
    assert any("incomplete" in c.detail for c in result.checks if not c.ok)


def test_incomplete_artifact_passes_when_tolerated():
    """`require_complete=False` is the escape hatch for tasks that explicitly
    accept a partial write (e.g. a long-running producer that flushes
    incrementally). Default stays strict."""
    criteria = ValidationCriteria(require_complete=False)
    result = run_validation([_mk("partial.md", incomplete=True)], criteria)
    assert result.ok is True


def test_min_size_gate():
    criteria = ValidationCriteria(min_size={"report.md": 200})
    result = run_validation([_mk("report.md", size=50)], criteria)
    assert result.ok is False
    fail = next(c for c in result.checks if not c.ok)
    assert fail.name == "min_size:report.md"


def test_max_size_gate():
    criteria = ValidationCriteria(max_size={"report.md": 100})
    result = run_validation([_mk("report.md", size=200)], criteria)
    assert result.ok is False


def test_required_substrings(tmp_path):
    (tmp_path / "report.md").write_text("# Monthly Report\n\nQ3 revenue up 12%.")
    criteria = ValidationCriteria(
        required_paths=["report.md"],
        required_substrings={"report.md": ["Monthly", "Q3 revenue"]},
    )
    artifacts = [_mk("report.md")]
    result = run_validation(artifacts, criteria, workspace=str(tmp_path))
    assert result.ok is True


def test_required_substrings_fail(tmp_path):
    (tmp_path / "report.md").write_text("# Monthly Report\n")
    criteria = ValidationCriteria(
        required_paths=["report.md"],
        required_substrings={"report.md": ["Monthly", "Q4 revenue"]},
    )
    artifacts = [_mk("report.md")]
    result = run_validation(artifacts, criteria, workspace=str(tmp_path))
    assert result.ok is False
    fail = next(c for c in result.checks if not c.ok)
    assert fail.name == "substring:report.md:'Q4 revenue'"


def test_csv_headers_gate(tmp_path):
    (tmp_path / "data.csv").write_text("name,score\nAlice,95\n")
    criteria = ValidationCriteria(
        required_paths=["data.csv"],
        csv_required_headers={"data.csv": ["name", "score"]},
    )
    artifacts = [_mk("data.csv")]
    result = run_validation(artifacts, criteria, workspace=str(tmp_path))
    assert result.ok is True


def test_csv_headers_missing(tmp_path):
    (tmp_path / "data.csv").write_text("name,grade\nAlice,95\n")
    criteria = ValidationCriteria(
        required_paths=["data.csv"],
        csv_required_headers={"data.csv": ["name", "score"]},
    )
    artifacts = [_mk("data.csv")]
    result = run_validation(artifacts, criteria, workspace=str(tmp_path))
    assert result.ok is False
    fail = next(c for c in result.checks if not c.ok)
    assert "score" in fail.detail


def test_gate_status_mapping():
    """The blueprint's two rules — engine error → "error", validation failure →
    "validation_failed" — are the only non-negotiable mappings."""
    ok = ValidationResult(ok=True)
    bad = ValidationResult(ok=False)
    assert gate_status(ok, engine_succeeded=True) == "ok"
    assert gate_status(bad, engine_succeeded=True) == "validation_failed"
    # Engine error wins over a (vacuously) passing validation.
    assert gate_status(ok, engine_succeeded=False) == "error"
    # Both failure modes map to "error" (the engine didn't finish).
    assert gate_status(bad, engine_succeeded=False) == "error"


def test_criteria_round_trip():
    c = ValidationCriteria(
        min_artifacts=2,
        max_artifacts=10,
        required_paths=["a.md"],
        required_substrings={"a.md": ["x"]},
        min_size={"a.md": 50},
        max_size={"a.md": 5000},
        require_complete=False,
        csv_required_headers={"b.csv": ["id"]},
    )
    d = c.to_dict()
    assert ValidationCriteria.from_dict(d) == c


def test_validation_result_round_trip():
    from core.validation import ValidationCheck

    r = ValidationResult(
        ok=False,
        checks=[ValidationCheck(name="artifact_count", ok=False, detail="0 < 1")],
        evidence={"artifact_count": 0},
    )
    d = r.to_dict()
    r2 = ValidationResult.from_dict(d)
    assert r2.ok is False
    assert r2.checks[0].name == "artifact_count"
    assert r2.evidence == {"artifact_count": 0}


# -- manager integration ---------------------------------------------------------


@pytest.fixture
def manager(tmp_path, monkeypatch):
    from providers import AssistantTurn, ModelCapabilities, ProviderClient
    from services.server.manager import SessionManager

    class ScriptedProvider(ProviderClient):
        def __init__(self, turns):
            self._turns = list(turns)

        def complete(self, *, model, messages, tools=None, **settings):
            return self._turns.pop(0)

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    provider = ScriptedProvider(
        [AssistantTurn(text="done", finish_reason="stop")] * 5
    )
    return SessionManager(data_dir=tmp_path / "data", provider=provider)


async def test_validation_failure_blocks_status_ok(tmp_path, manager):
    """Engine returns successfully but the task's `min_artifacts=1` is not met
    → status must be `validation_failed`, not `ok`."""
    from core.automation.models import Schedule, ScheduledTask

    ws = tmp_path / "ws"
    ws.mkdir()
    task = ScheduledTask(
        title="T",
        instructions="i",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(ws),
        agent="cowork",
        validation_criteria={"min_artifacts": 1},
    )
    manager.task_store.save(task)
    run = await manager._run_scheduled_task(task, trigger="manual")
    assert run.status == "validation_failed"

    # Ledger recorded the failed verdict
    types = [e["type"] for e in manager.run_ledger.events(run.run_id)]
    assert "validation.failed" in types


async def test_validation_passes_when_criteria_met(tmp_path, manager):
    """Engine returns successfully and the artifact matches the criteria → status
    is `ok`."""
    from core.automation.models import Schedule, ScheduledTask

    ws = tmp_path / "ws"
    ws.mkdir()
    task = ScheduledTask(
        title="T",
        instructions="i",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(ws),
        agent="cowork",
        validation_criteria={
            "min_artifacts": 1,
            "required_paths": ["report.md"],
            "required_substrings": {"report.md": ["Report"]},
        },
    )
    manager.task_store.save(task)
    # The provider's scripted turn writes nothing — pre-create the expected
    # artifact before the run so validation can see it.
    (ws / "report.md").write_text("# Report\nBody.")
    # mtime the file to NOW so the artifact walk picks it up.
    import time as _t

    now = _t.time()
    import os as _os

    _os.utime(ws / "report.md", (now, now))
    run = await manager._run_scheduled_task(task, trigger="manual")
    assert run.status == "ok"
    types = [e["type"] for e in manager.run_ledger.events(run.run_id)]
    assert "validation.passed" in types


async def test_validation_skipped_for_default_criteria(tmp_path, manager):
    """A task with no `validation_criteria` set passes the floor (engine
    success → status ok)."""
    from core.automation.models import Schedule, ScheduledTask

    ws = tmp_path / "ws"
    ws.mkdir()
    task = ScheduledTask(
        title="T",
        instructions="i",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(ws),
        agent="cowork",
    )
    manager.task_store.save(task)
    run = await manager._run_scheduled_task(task, trigger="manual")
    assert run.status == "ok"
