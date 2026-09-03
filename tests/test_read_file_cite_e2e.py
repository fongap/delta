"""P2 实用 — read_file auto-cite e2e (DELTA_BLUEPRINT §7.2).

The unit tests in test_source_citation.py prove the ``cite`` helper +
``file_tools(..., source_store=, run_id=)`` round-trip. This test proves
the same hook fires when the call goes through the REAL engine path —
i.e. ``_run_scheduled_task`` → ``build_engine`` → registry.execute →
``read_file`` → citation appended. The only scripted piece is the model
(provider stands in); every other moving part is real.

The test drives the **code** agent, whose ``code_files`` capability is the
one that wires our line-numbered ``read_file`` (which carries the
auto-cite hook). Knowledge-work surfaces (cowork/ops) keep aisuite's
multi-root ``read_file``; their cite hook is a follow-up.

The test is intentionally cheap: one ``read_file`` call, one assertion on
the per-workspace Source ledger. It complements the Reference Task
(test_reference_task.py) by pinning §7.2's first concrete requirement —
"a successful file read lands a citation".
"""

from __future__ import annotations

from pathlib import Path

from core.automation.models import Schedule, ScheduledTask
from providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from services.server.manager import SessionManager


class _ReadFileProvider(ProviderClient):
    """One-turn scripted provider: read_file once, then a final text reply."""

    def __init__(self, args: dict):
        self._args = args
        self._consumed = False

    def complete(self, **kwargs):
        if not self._consumed:
            self._consumed = True
            return AssistantTurn(
                tool_calls=[
                    ToolCall(id="c_read", name="read_file", arguments=self._args)
                ]
            )
        return AssistantTurn(text="read it", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _task(tmp_path, ws_name: str) -> ScheduledTask:
    ws = tmp_path / ws_name
    ws.mkdir()
    return ScheduledTask(
        title="read",
        instructions="read the file",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(ws),
        # `code` is the agent whose ``code_files`` capability wires our
        # line-numbered ``read_file`` (carries the auto-cite hook).
        # Cowork/ops keep aisuite's multi-root reader; cite there is a
        # follow-up.
        agent="code",
    )


def _write_log(ws: Path) -> None:
    (ws / "data.log").write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")


def _manager(tmp_path, provider):
    return SessionManager(data_dir=tmp_path / "data", provider=provider)


# -- 1. Automation path: pre-allocated run id flows into the citation ------


async def test_read_file_in_automation_run_auto_cites(tmp_path):
    task = _task(tmp_path, "ws_auto")
    _write_log(Path(task.workspace))
    mgr = _manager(
        tmp_path,
        _ReadFileProvider(args={"path": "data.log", "start_line": 2, "max_lines": 2}),
    )
    mgr.task_store.save(task)

    run = await mgr._run_scheduled_task(task, trigger="schedule")

    # The Source ledger lives next to the run ledger for the same workspace.
    store = mgr.source_store_for(task.workspace, run_id=None)  # type: ignore[attr-defined]
    assert store is not None
    refs = store.list()
    assert len(refs) == 1, refs
    ref = refs[0]
    assert ref.location == "data.log"
    # The citation joins the TaskRun's G1 run id (single identity).
    assert ref.cited_ranges == [
        {
            "run_id": run.run_id,
            "ranges": [{"kind": "lines", "start": 2, "end": 3}],
        }
    ]
    await mgr.aclose()


# -- 2. Two reads of the same file accumulate one citation per window ----


async def test_read_file_accumulates_citations_across_windows(tmp_path):
    task = _task(tmp_path, "ws_paged")
    _write_log(Path(task.workspace))

    class _PagedProvider(_ReadFileProvider):
        def __init__(self):
            self._calls = 0

        def complete(self, **kwargs):
            if self._calls == 0:
                self._calls += 1
                return AssistantTurn(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="read_file",
                            arguments={"path": "data.log", "start_line": 1, "max_lines": 2},
                        )
                    ]
                )
            if self._calls == 1:
                self._calls += 1
                return AssistantTurn(
                    tool_calls=[
                        ToolCall(
                            id="c2",
                            name="read_file",
                            arguments={"path": "data.log", "start_line": 3, "max_lines": 2},
                        )
                    ]
                )
            return AssistantTurn(text="done", finish_reason="stop")

    mgr = _manager(tmp_path, _PagedProvider())
    mgr.task_store.save(task)

    run = await mgr._run_scheduled_task(task, trigger="schedule")

    store = mgr.source_store_for(task.workspace, run_id=None)  # type: ignore[attr-defined]
    assert store is not None
    ref = store.list()[0]
    starts = sorted(
        r["start"]
        for entry in ref.cited_ranges
        for r in entry["ranges"]
        if r.get("kind") == "lines"
    )
    assert starts == [1, 3], starts
    # All citations belong to the same run.
    assert {entry["run_id"] for entry in ref.cited_ranges} == {run.run_id}
    await mgr.aclose()


# -- 3. A read error (missing file) does NOT leave a phantom citation ----


async def test_read_file_error_does_not_cite(tmp_path):
    task = _task(tmp_path, "ws_err")
    Path(task.workspace).mkdir(exist_ok=True)
    mgr = _manager(
        tmp_path, _ReadFileProvider(args={"path": "missing.log"})
    )
    mgr.task_store.save(task)

    await mgr._run_scheduled_task(task, trigger="schedule")

    store = mgr.source_store_for(task.workspace, run_id=None)  # type: ignore[attr-defined]
    assert store is not None
    assert store.list() == []
    await mgr.aclose()
