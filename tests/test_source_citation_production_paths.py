"""Production-path regressions for Source/Citation consumers.

These tests deliberately enter through the Automation completion gate and
the Run Detail HTTP endpoint.  Unit coverage of ``SourceStore`` alone would
not catch a consumer calling a nonexistent store API and swallowing the
result as "zero citations".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.automation.models import Schedule, ScheduledTask, TaskRun
from core.sources import CitationRange, KIND_LINES
from services.server.app import create_app
from services.server.manager import SessionManager


def _cited_run(manager: SessionManager, workspace, *, run_id: str) -> str:
    source = workspace / "研究 数据.txt"
    source.write_text("第一行\n第二行\n", encoding="utf-8")
    store = manager.source_store_for(str(workspace), run_id=run_id)
    assert store is not None
    ref = store.capture_file(source)
    assert store.add_citation(
        ref.id,
        run_id,
        CitationRange(kind=KIND_LINES, start=1, end=2),
    )
    return ref.id


def test_automation_completion_gate_counts_persisted_valid_citation(tmp_path):
    workspace = tmp_path / "材料 空间"
    workspace.mkdir()
    manager = SessionManager(data_dir=tmp_path / "data")
    run = TaskRun(task_id="task-citations", workspace=str(workspace))
    source_id = _cited_run(manager, workspace, run_id=run.run_id)
    task = ScheduledTask(
        title="citation gate",
        instructions="Produce a cited result",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(workspace),
        validation_criteria={
            "min_artifacts": 0,
            "require_citations": True,
            "min_valid_citations": 1,
        },
    )

    assert manager._count_valid_citations(str(workspace), run.run_id) == 1
    assert manager._validate_run(run, task, []) == "ok"
    events = manager.run_ledger.events(run.run_id)
    assert events[-1]["type"] == "validation.passed"
    assert events[-1]["payload"]["evidence"]["valid_citation_count"] == 1
    assert source_id


def test_automation_completion_gate_does_not_hide_source_store_failure(
    tmp_path, monkeypatch
):
    manager = SessionManager(data_dir=tmp_path / "data")

    class BrokenSourceStore:
        def list(self):
            raise RuntimeError("citation store unavailable")

    monkeypatch.setattr(
        manager,
        "source_store_for",
        lambda workspace, *, run_id: BrokenSourceStore(),
    )

    with pytest.raises(RuntimeError, match="citation store unavailable"):
        manager._count_valid_citations(str(tmp_path), "run-broken")


def test_run_detail_returns_citations_from_public_source_store_api(tmp_path):
    workspace = tmp_path / "材料 空间"
    workspace.mkdir()
    manager = SessionManager(data_dir=tmp_path / "data")
    run_id = "run-detail-citations"
    source_id = _cited_run(manager, workspace, run_id=run_id)
    manager.run_ledger.append(
        run_id,
        "run.started",
        actor="system",
        payload={"kind": "automation"},
        workspace=str(workspace),
    )

    with TestClient(create_app(manager)) as client:
        response = client.get(f"/v1/runs/{run_id}/detail")

    assert response.status_code == 200
    citations = response.json()["citations"]
    assert citations == [
        {
            "source_id": source_id,
            "location": "研究 数据.txt",
            "captured_at": citations[0]["captured_at"],
            "citation": {
                "run_id": run_id,
                "ranges": [{"kind": "lines", "start": 1, "end": 2}],
            },
        }
    ]


def test_run_detail_does_not_hide_source_store_failure(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = SessionManager(data_dir=tmp_path / "data")
    run_id = "run-detail-broken-source"
    manager.run_ledger.append(
        run_id,
        "run.started",
        workspace=str(workspace),
    )

    class BrokenSourceStore:
        def list(self):
            raise RuntimeError("citation store unavailable")

    monkeypatch.setattr(
        manager,
        "source_store_for",
        lambda workspace, *, run_id: BrokenSourceStore(),
    )

    with TestClient(create_app(manager), raise_server_exceptions=False) as client:
        response = client.get(f"/v1/runs/{run_id}/detail")

    assert response.status_code == 500
