"""P3 Run Analyzer — ``automation_health`` rollup.

Aggregates ``TaskRun.status`` + ledger failure events for one
``ScheduledTask``. Tests cover:

- rollup shape (status counts / failure rate / duration)
- ``WorkspaceMismatchError`` on cross-workspace or unknown tasks
- ledger failure reason accumulation from ``run.failed`` /
  ``validation.failed`` / ``tool.finished{status: "error"}``
- isolation: a task from another workspace is never folded in
- module wrapper parity
"""

from __future__ import annotations

import pytest

from core.analyzer import (
    Analyzer,
    AutomationHealth,
    WorkspaceMismatchError,
    automation_health,
)
from core.automation.models import Schedule, ScheduledTask, TaskRun
from core.automation.store import TaskStore
from core.ledger import RunEventLedger


def _build(tmp_path, *, task_workspace: str, run_count: int = 5):
    """Build one task with N runs (alternating ok / error).

    Returns ``(ledger, task_store, task)`` ready to feed into an Analyzer.
    The ledger gets a ``run.failed`` event for each error run so the
    failure_reason counter has something to aggregate.
    """
    led = RunEventLedger(tmp_path / "events.db")
    store = TaskStore(tmp_path / "tasks.db")
    task = ScheduledTask(
        title="t",
        instructions="i",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=task_workspace,
    )
    store.save(task)
    for i in range(run_count):
        is_error = i % 2 == 1  # i=1,3,5 are error
        run = TaskRun(
            task_id=task.id,
            run_id=f"run-{i}",
            started_at=1000.0 + i * 10,
            finished_at=1000.0 + i * 10 + 4.0,
            status="error" if is_error else "ok",
            error="boom" if is_error else None,
        )
        store.add_run(run)
        led.append(run.run_id, "run.started")
        if run.status == "error":
            led.append(
                run.run_id,
                "run.failed",
                actor="system",
                payload={"reason": "boom", "error": "TimeoutError"},
            )
            led.append(
                run.run_id,
                "tool.finished",
                actor="engine",
                payload={"tool": "shell", "status": "error", "error": "exit 1"},
            )
        else:
            led.append(run.run_id, "run.completed")
    return led, store, task


def test_rollup_counts_statuses_and_failure_rate(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led, store, task = _build(tmp_path, task_workspace=str(ws), run_count=4)
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led, task_store=store)
        h = analyzer.automation_health(task.id)
    finally:
        led.close()
        store.close()
    assert isinstance(h, AutomationHealth)
    assert h.run_count == 4
    assert h.status_counts == {"ok": 2, "error": 2}
    assert h.failure_rate == 0.5
    # 4 durations of 4.0s each → avg 4.0
    assert h.avg_duration_seconds == pytest.approx(4.0)


def test_failure_reasons_aggregate_across_run_and_tool_events(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    # run_count=4: i=0 ok, i=1 error, i=2 ok, i=3 error → two error runs
    led, store, task = _build(tmp_path, task_workspace=str(ws), run_count=4)
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led, task_store=store)
        h = analyzer.automation_health(task.id)
    finally:
        led.close()
        store.close()
    # Ledger-sourced sub-causes (run.failed / tool.finished{error} /
    # validation.failed) live in failure_reasons. The top-level
    # TaskRun.error label lives in run_error_counts so the same root
    # cause isn't double-counted.
    assert h.failure_reasons.get("boom") == 2  # run.failed × 2 error runs
    assert h.failure_reasons.get("exit 1") == 2  # tool.finished × 2 error runs
    # TaskRun.error = "boom" on each of the two error runs
    assert h.run_error_counts.get("boom") == 2


def test_workspace_mismatch_is_rejected_loudly(tmp_path):
    """A task from a different workspace must NOT be silently included."""
    ws_real = tmp_path / "ws-real"
    ws_real.mkdir()
    ws_other = tmp_path / "ws-other"
    ws_other.mkdir()
    led, store, task = _build(tmp_path, task_workspace=str(ws_other))
    try:
        analyzer = Analyzer(workspace=str(ws_real), ledger=led, task_store=store)
        with pytest.raises(WorkspaceMismatchError):
            analyzer.automation_health(task.id)
    finally:
        led.close()
        store.close()


def test_unknown_task_id_is_workspace_mismatch(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led = RunEventLedger(tmp_path / "events.db")
    store = TaskStore(tmp_path / "tasks.db")
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led, task_store=store)
        with pytest.raises(WorkspaceMismatchError):
            analyzer.automation_health("task-does-not-exist")
    finally:
        led.close()
        store.close()


def test_task_without_workspace_is_rejected(tmp_path):
    """A legacy task with no workspace must not be guessed into one."""
    ws = tmp_path / "ws"
    ws.mkdir()
    led = RunEventLedger(tmp_path / "events.db")
    store = TaskStore(tmp_path / "tasks.db")
    task = ScheduledTask(
        title="t",
        instructions="i",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace="",  # legacy blank
    )
    store.save(task)
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led, task_store=store)
        with pytest.raises(WorkspaceMismatchError, match="has no workspace"):
            analyzer.automation_health(task.id)
    finally:
        led.close()
        store.close()


def test_window_limits_recent_runs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led, store, task = _build(tmp_path, task_workspace=str(ws), run_count=10)
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led, task_store=store)
        h = analyzer.automation_health(task.id, window=4)
    finally:
        led.close()
        store.close()
    assert h.run_count == 4
    assert h.window == 4


def test_zero_window_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led, store, _ = _build(tmp_path, task_workspace=str(ws))
    try:
        analyzer = Analyzer(workspace=str(ws), ledger=led, task_store=store)
        with pytest.raises(ValueError, match="window must be a positive integer"):
            analyzer.automation_health("anything", window=0)
    finally:
        led.close()
        store.close()


def test_automation_health_module_wrapper_parity(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    led, store, task = _build(tmp_path, task_workspace=str(ws), run_count=3)
    try:
        via_class = Analyzer(
            workspace=str(ws), ledger=led, task_store=store
        ).automation_health(task.id, window=3)
        via_module = automation_health(
            workspace=str(ws),
            task_id=task.id,
            task_store=store,
            ledger=led,
            window=3,
        )
    finally:
        led.close()
        store.close()
    assert via_class.to_dict() == via_module.to_dict()
