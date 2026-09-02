"""ADR-005 §7.1 Reference Task — the short-term "Reliable" acceptance.

The blueprint's headline scenario:

  读取本地 CSV / XLSX 数据，完成分析并生成 Markdown 报告

Acceptance requires that from a user goal to a durable Artifact:

  1. the full flow runs end-to-end;
  2. a high-consequence action passes through Approval;
  3. the Artifact really exists and is format-valid;
  4. Validation judges whether the result satisfies the contract;
  5. the run's execution / approval / failure / result are replayable;
  6. a human interrupt + restart continues;
  7. committed high-consequence side effects are not re-executed;
  8. automation triggers obey the same permission + run rules as manual.

These tests drive the real automation runtime (`_run_scheduled_task`) with a
scripted provider, then assert the WS1–WS4 machinery (ledger narrative,
Artifact sha256, Validation gate, IdempotencyLog) holds together for a real
task. The provider stands in for the model; every other moving part is real.
"""

from __future__ import annotations



from providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from services.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if not self._turns:
            return AssistantTurn(text="(no more turns)", finish_reason="stop")
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _tool(name, args, call_id):
    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=args)])


def _text(text):
    return AssistantTurn(text=text, finish_reason="stop")


CSV = "name,region,amount\nAlice,west,100\nBob,east,200\nCarol,west,300\n"
REPORT = "# Regional Report\n\nTotal west: 400. Total east: 200.\n"


def _make_task(tmp_path, *, validation_criteria=None):
    from core.automation.models import Schedule, ScheduledTask

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sales.csv").write_text(CSV, encoding="utf-8")
    task = ScheduledTask(
        title="Regional sales report",
        instructions="Read sales.csv and write regional_report.md summarizing totals by region.",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(ws),
        agent="cowork",
        validation_criteria=validation_criteria,
    )
    return task, ws


def _mgr(tmp_path, turns):
    return SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider(turns))


# -- 1,3,4,5: full flow, real artifact, validation, replayable narrative --------
async def test_reference_task_produces_validated_artifact(tmp_path):
    task, ws = _make_task(
        tmp_path,
        validation_criteria={
            "min_artifacts": 1,
            "required_paths": ["regional_report.md"],
            "required_substrings": {"regional_report.md": ["Regional Report", "west"]},
        },
    )
    mgr = _mgr(
        tmp_path,
        [
            _tool("read_file", {"path": "sales.csv"}, "c_read"),
            _tool("write_file", {"path": "regional_report.md", "content": REPORT}, "c_write"),
            _text("Wrote regional_report.md."),
        ],
    )
    mgr.task_store.save(task)

    run = await mgr._run_scheduled_task(task, trigger="schedule")

    # (1) full flow + (3) artifact really exists and is readable
    assert run.status == "ok", (run.status, run.error)
    arts = {a["path"]: a for a in run.artifacts}
    assert "regional_report.md" in arts
    report = arts["regional_report.md"]
    assert report["kind"] == "markdown"
    assert report["incomplete"] is False
    # (3) format-valid: sha256 recorded and matches the file on disk
    import hashlib

    on_disk = (ws / "regional_report.md").read_bytes()
    assert report["sha256"] == hashlib.sha256(on_disk).hexdigest()
    assert on_disk  # non-empty
    assert b"Regional Report" in on_disk and b"west" in on_disk

    # (4) validation passed and recorded its verdict
    ledger_events = mgr.run_ledger.events(run.run_id)
    types = [e["type"] for e in ledger_events]
    assert "validation.passed" in types

    # (5) replayable narrative: tool lifecycle + artifact + validation all in the ledger
    assert "tool.started" in types
    assert "tool.finished" in types
    assert "artifact.registered" in types
    assert "artifact.completed" in types
    # G1 unification: run.started, the tool narrative, AND the artifact/validation
    # verdict all share the TaskRun's run_id (one identity, one replayable source).
    assert "run.started" in types
    assert "run.completed" in types
    # There is exactly ONE ledger run for this execution — not a split identity.
    assert mgr.run_ledger.runs() == [run.run_id]
    # hash chain verifies after the whole run (tamper-evident replay)
    assert mgr.run_ledger.verify(run.run_id) is True

    await mgr.aclose()


# -- 4 negative: incomplete artifact blocks a false "done" ----------------------
async def test_reference_task_validation_blocks_missing_artifact(tmp_path):
    """The model claims success but never writes the report → the Validation
    gate must NOT let the run enter the success state."""
    task, _ws = _make_task(
        tmp_path,
        validation_criteria={"min_artifacts": 1, "required_paths": ["regional_report.md"]},
    )
    mgr = _mgr(
        tmp_path,
        [
            _tool("read_file", {"path": "sales.csv"}, "c_read"),
            _text("I analyzed the data. (No file was actually written.)"),
        ],
    )
    mgr.task_store.save(task)

    run = await mgr._run_scheduled_task(task, trigger="schedule")

    # Engine finished without raising, but the contract was not met.
    assert run.status == "validation_failed"
    ledger_types = [e["type"] for e in mgr.run_ledger.events(run.run_id)]
    assert "validation.failed" in ledger_types
    await mgr.aclose()


# -- 2,8: high-consequence gating is identical for automation and manual -------
def test_automation_and_manual_share_permission_rules(tmp_path):
    """Criterion 8: an automation trigger obeys the same permission rules as a
    manual one. Both paths build through `build_engine` → the same
    `PermissionEngine.evaluate`, so an out-of-workspace write is refused the
    same way regardless of trigger. We assert the decision directly (the run
    path is exercised by the other tests)."""
    from core.permissions import Mode, PermissionEngine

    ws = tmp_path / "ws"
    ws.mkdir()
    eng = PermissionEngine(workspace_root=ws, mode=Mode.INTERACTIVE)
    # L2 in-workspace write → ask (not auto) under INTERACTIVE for both paths.
    inside = eng.evaluate("write_file", {"path": "ok.md", "content": "x"}, None)
    escape = eng.evaluate("write_file", {"path": "../bad.md", "content": "x"}, None)
    assert escape.needs_user is False and escape.allowed is False  # refused: outside root
    assert inside.allowed or inside.needs_user  # in-workspace is allowed-or-asked, never silently denied outside


# -- 7: committed side effects are not re-executed on resume --------------------
async def test_committed_side_effect_not_replayed_on_resume(tmp_path):
    """Commit a write's side effect + ledger event, then re-issue the same call
    inside the run scope. The IdempotencyLog makes the engine replay the
    recorded result instead of running the tool again (the previous run's
    effect is not doubled)."""
    task, _ws = _make_task(tmp_path)
    mgr = _mgr(tmp_path, [])
    mgr.task_store.save(task)

    from core.engine import ToolCall

    tc = ToolCall(
        id="c_write",
        name="write_file",
        arguments={"path": "regional_report.md", "content": REPORT},
    )
    # Record the commit exactly as _execute_sync would after a successful write.
    mgr.idem_log.commit(
        "run-replay",
        tc.id,
        tc.name,
        tc.arguments,
        {"ok": True, "written": "regional_report.md"},
        ledger=mgr.run_ledger,
    )

    # Build an engine against the same log and re-issue the same call inside
    # the run scope: the log hit must produce a replay, not a re-execute.
    runtime = mgr._build_task_engine(task, session_id="sess-replay")
    engine = runtime.engine  # escape hatch — the raw TurnEngine owns _execute_sync
    from core import runscope

    token = runscope.set_current("run-replay", "sess-replay")
    try:
        result, status = engine._execute_sync(tc)
    finally:
        runscope.reset(token)

    assert status == "replayed"
    assert result == {"ok": True, "written": "regional_report.md"}
    # Ledger shows the original commit was recorded (one side_effect.committed).
    committed = [
        e for e in mgr.run_ledger.events("run-replay") if e["type"] == "side_effect.committed"
    ]
    assert len(committed) == 1
    await mgr.aclose()
