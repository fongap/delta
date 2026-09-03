"""P2 实用 — Automation convergence (DELTA_BLUEPRINT §7.2).

DELTA_BLUEPRINT §7.2 实用 requires that:

  Automation 不允许形成第二套执行模型
  Automation 触发与手动触发遵循相同权限和运行规则
  手动任务和自动化任务共享同一 Ledger、Validation 和 Artifact 语义

The Reference Task (test_reference_task.py) drives the real automation
runtime with a scripted provider — that's the headline scenario. These
tests are the cheaper, structural guardrails: they assert at the wiring
level that there is ONE build path (no second execution model), that the
runtime carries the same idemlog + audit_sink for both manual and
automation calls, and that a missing field in the run wiring is caught
as a regression rather than silently producing a half-wired runtime.
"""

from __future__ import annotations

import asyncio
import inspect

from core.automation.models import Schedule, ScheduledTask
from providers import AssistantTurn, ModelCapabilities, ProviderClient
from services.server.manager import SessionManager


def _task(tmp_path) -> ScheduledTask:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "data.csv").write_text("a,b\n1,2\n")
    return ScheduledTask(
        title="t",
        instructions="noop",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(ws),
        agent="cowork",
    )


class _NoopProvider(ProviderClient):
    """Provider that does nothing — the test asserts the wiring, not the run."""

    def complete(self, **kwargs):
        return AssistantTurn(text="(noop)", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _mgr(tmp_path) -> SessionManager:
    return SessionManager(data_dir=tmp_path / "data", provider=_NoopProvider())


# -- 1. ONE build path: manual + automation share the same factory ----------


def test_automation_build_path_uses_shared_factory(tmp_path):
    """`_build_task_engine` is the only entry point for both automation and
    manual session runtime construction. A second factory that skipped
    PermissionEngine / idemlog / audit_sink would be a §7.2 violation.
    """
    mgr = _mgr(tmp_path)
    sig = inspect.signature(mgr._build_task_engine)
    # Same shape across both call sites: session_id + optional run_id.
    assert "session_id" in sig.parameters
    assert "run_id" in sig.parameters
    # The factory must take the manager's idem_log + audit_sink (no hidden
    # second model with a different store).
    src = inspect.getsource(mgr._build_task_engine)
    assert "self.idem_log" in src
    assert "self.audit_sink" in src


# -- 2. The automation runtime carries the same audit_sink + idem_log as
#      the manual session runtime -------------------------------------------


def test_automation_runtime_uses_shared_sinks(tmp_path):
    """Two runtimes built by the manager (one manual-shaped, one automation-
    shaped) must point at the SAME idem_log + audit_sink instances — so a
    manual write + an automation write of the same args dedupe and the
    ledger sees one coherent narrative."""
    mgr = _mgr(tmp_path)
    task = _task(tmp_path)

    rt_automation = mgr._build_task_engine(task, session_id="s_auto", run_id="r1")
    rt_manual = mgr._build_task_engine(task, session_id="s_manual")

    auto_engine = rt_automation.engine  # type: ignore[attr-defined]
    manual_engine = rt_manual.engine  # type: ignore[attr-defined]
    # Both engines reference the manager's idem_log (not a private copy).
    assert auto_engine.idem_log is mgr.idem_log
    assert manual_engine.idem_log is mgr.idem_log
    # The audit_sink that funnels events into the ledger is also the same
    # callable on both paths — a divergence would split the narrative.
    assert auto_engine.audit_sink is manual_engine.audit_sink
    assert auto_engine.audit_sink is mgr.audit_sink


# -- 3. Automation emits the same ADR-005 ledger vocabulary ----------------


def test_automation_run_emits_known_event_vocabulary(tmp_path):
    """The mirroring audit_sink only forwards events whose type is in
    ``KNOWN_EVENT_TYPES``. An automation run that produced an unknown event
    would mean a reader/writer forked the vocabulary (the §7.2 "two
    execution models" failure mode)."""
    from core.ledger import KNOWN_EVENT_TYPES

    mgr = _mgr(tmp_path)
    task = _task(tmp_path)
    mgr.task_store.save(task)

    run = asyncio.run(mgr._run_scheduled_task(task, trigger="schedule"))
    types = {e["type"] for e in mgr.run_ledger.events(run.run_id)}
    unknown = types - KNOWN_EVENT_TYPES
    assert not unknown, f"automation produced unknown event types: {unknown}"
    # And the run shared its identity across the three P1 surfaces
    # (TaskRun.run_id == run_ledger.run_id) — not split.
    assert mgr.run_ledger.runs() == [run.run_id]


# -- 4. A run without a passed run_id still ends up in the ledger ---------
# (The manual path doesn't know the run_id until the adapter allocates it
# on the first turn. The automation path pre-allocates it from TaskRun.
# Either way, exactly one ledger run must exist after the call.)


def test_automation_pre_allocated_run_id_lands_in_ledger(tmp_path):
    mgr = _mgr(tmp_path)
    task = _task(tmp_path)
    mgr.task_store.save(task)

    run = asyncio.run(mgr._run_scheduled_task(task, trigger="schedule"))
    # The TaskRun.run_id was the one bound into the runtime.
    assert mgr.run_ledger.runs() == [run.run_id]
    # And it has a run.started event (proving the adapter picked it up).
    started = [
        e for e in mgr.run_ledger.events(run.run_id) if e["type"] == "run.started"
    ]
    assert len(started) == 1

