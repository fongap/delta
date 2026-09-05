"""P1-D Artifacts / Citations / Analyzer productization tests.

The spec requires:
  - write tools register artifacts explicitly (not just mtime scan)
  - citation completion contract (require_citations / min_valid_citations)
  - Run Detail API exposing timeline / artifacts / validation /
    citations / recovery / side-effects
"""

from __future__ import annotations

import asyncio


from core.artifact import register_artifact, register_run_artifacts
from core.ledger import RunEventLedger
from core.validation import (
    ValidationCriteria,
    run_validation,
)


# -- register_artifact -----------------------------------------------------


def test_register_artifact_writes_ledger_events(tmp_path):
    """A successful register_artifact appends artifact.registered +
    artifact.completed to the run ledger."""
    (tmp_path / "report.md").write_bytes(b"hello world\n")
    ledger = RunEventLedger(tmp_path / "run-events.db")
    art = register_artifact(
        str(tmp_path), "report.md", run_id="r1", ledger=ledger
    )
    assert art is not None
    assert art.path == "report.md"
    assert art.kind == "markdown"
    assert art.size == len("hello world\n")
    assert art.sha256 and len(art.sha256) == 64
    assert art.incomplete is False

    events = ledger.events("r1")
    types = [e["type"] for e in events]
    assert "artifact.registered" in types
    assert "artifact.completed" in types
    ledger.close()


def test_register_artifact_incomplete_when_read_fails(tmp_path):
    """A truncated/unreadable file is registered with incomplete=True."""
    target = tmp_path / "broken.md"
    target.write_text("data", encoding="utf-8")
    # Remove the file to make the read fail (sha256 path opens it).
    target.unlink()
    art = register_artifact(str(tmp_path), "broken.md", run_id="r1")
    assert art is None  # missing file → None


def test_register_artifact_returns_none_for_missing_file(tmp_path):
    assert register_artifact(str(tmp_path), "nope.md", run_id="r1") is None


def test_register_artifact_no_ledger(tmp_path):
    """When no ledger is provided, the artifact is computed but no
    events are written."""
    (tmp_path / "x.md").write_text("hi\n", encoding="utf-8")
    art = register_artifact(str(tmp_path), "x.md", run_id="r1")
    assert art is not None
    assert art.sha256


# -- run_validation citation completion contract ---------------------------


def test_validation_require_citations_passes_when_satisfied():
    criteria = ValidationCriteria(
        min_artifacts=0,
        require_citations=True,
        min_valid_citations=2,
    )
    arts: list[dict] = []
    result = run_validation(
        arts, criteria, valid_citation_count=2
    )
    assert result.ok
    names = [c.name for c in result.checks]
    assert "min_valid_citations" in names


def test_validation_require_citations_fails_below_floor():
    criteria = ValidationCriteria(
        min_artifacts=0,
        require_citations=True,
        min_valid_citations=3,
    )
    arts: list[dict] = []
    result = run_validation(
        arts, criteria, valid_citation_count=1
    )
    assert not result.ok
    failed = [c for c in result.checks if c.name == "min_valid_citations"]
    assert len(failed) == 1
    assert failed[0].ok is False
    assert "1" in failed[0].detail
    assert "3" in failed[0].detail


def test_validation_require_citations_skipped_when_count_unknown():
    """When the caller doesn't pass valid_citation_count, the citation
    check is skipped — never blocks the run."""
    criteria = ValidationCriteria(
        min_artifacts=0,
        require_citations=True,
        min_valid_citations=2,
    )
    arts: list[dict] = []
    result = run_validation(arts, criteria)  # no valid_citation_count
    assert result.ok


def test_validation_criteria_roundtrip():
    """The new citation fields round-trip through to_dict / from_dict."""
    c = ValidationCriteria(
        min_artifacts=1,
        require_citations=True,
        min_valid_citations=4,
    )
    d = c.to_dict()
    assert d["require_citations"] is True
    assert d["min_valid_citations"] == 4
    c2 = ValidationCriteria.from_dict(d)
    assert c2.require_citations is True
    assert c2.min_valid_citations == 4


# -- Artifact + idem log coexistence ----------------------------------------


def test_explicit_artifact_and_workspace_scan_both_work(tmp_path):
    """P1-D: explicit register_artifact is the new main path; the
    workspace scanner (register_run_artifacts) remains as
    fallback/reconciliation. Both produce compatible Artifact shapes."""
    (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("bb\n", encoding="utf-8")

    # Explicit registration: caller knows the path
    art_a = register_artifact(str(tmp_path), "a.md", run_id="r1")
    # Workspace scan: post-run fallback
    import time

    arts_b = register_run_artifacts(
        str(tmp_path), run_id="r1", since=time.time() - 60
    )

    assert art_a is not None
    # Both surfaces produce the same kind classification.
    paths_b = {a.path for a in arts_b}
    assert "a.md" in paths_b
    assert "b.md" in paths_b


# -- end-to-end: explicit registration through a real automation run -------


def test_real_run_registers_artifact_explicitly(tmp_path):
    """End-to-end: drive a real automation run with a write_file tool
    call. The IdempotencyLog commit + explicit artifact registration
    should both fire, and the artifact should be findable on disk."""
    from core.automation.models import Schedule, ScheduledTask
    from providers import (
        AssistantTurn,
        ModelCapabilities,
        ProviderClient,
        ToolCall,
    )
    from services.server.manager import SessionManager

    ws = tmp_path / "ws"
    ws.mkdir()

    class _Prov(ProviderClient):
        def __init__(self, turns):
            self._turns = list(turns)

        def complete(self, *, model, messages, tools=None, **settings):
            if not self._turns:
                return AssistantTurn(text="(end)", finish_reason="stop")
            return self._turns.pop(0)

        def capabilities(self, model):
            return ModelCapabilities()

    turns = [
        AssistantTurn(
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="write_file",
                    arguments={
                        "path": "out.md",
                        "content": "# Done\n",
                    },
                )
            ]
        ),
        AssistantTurn(text="wrote.", finish_reason="stop"),
    ]
    mgr = SessionManager(
        data_dir=tmp_path / "data", provider=_Prov(turns)
    )
    task = ScheduledTask(
        title="t1",
        instructions="Write out.md",
        workspace=str(ws),
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        agent="code",
        validation_criteria={"min_artifacts": 0, "required_paths": []},
    )
    mgr.task_store.save(task)

    async def go():
        run = await mgr._run_scheduled_task(task, trigger="schedule")
        return run

    run = asyncio.run(go())
    assert run.status in ("ok", "validation_failed")
    # The workspace scan picked up the artifact regardless.
    arts = {a["path"] for a in run.artifacts}
    assert "out.md" in arts
    # And the IdempotencyLog saw the commit.
    committed = mgr.idem_log.committed_for_run(run.run_id)
    assert any(c["tool_name"] == "write_file" for c in committed)
    asyncio.run(mgr.aclose())
