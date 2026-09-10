"""Engine-level SideEffect ↔ Ledger production contract verification.

P0-B: These integration tests verify that the real production path
``TurnEngine._execute_sync`` produces the expected ``side_effect.*``
ledger events in ``run_events.db``.

The Public Contract (ADR-005 WS4 / runtime-public-contract.md) requires:

    side_effect.planned     — before tool execution
    side_effect.committed   — after successful tool execution
    side_effect.failed      — after tool failure
    side_effect.uncertain   — after crash recovery sweep

Cases:

  1. Successful side effect → planned + committed in ledger
  2. Execution failure      → planned + failed in ledger
  3. Crash recovery sweep   → uncertain in ledger
  4. Resume replay          → committed side effect NOT re-executed
"""

from __future__ import annotations

import aisuite as ai
import pytest

from core.engine import TurnEngine
from core.idemlog import IdempotencyLog
from core.ledger import RunEventLedger
from core.permissions import PermissionEngine
from core.runscope import reset, set_current
from integrations.tools import ToolRegistry
from providers import ModelCapabilities, ProviderClient, ToolCall


# -- fixtures ----------------------------------------------------------------

@pytest.fixture
def idem(tmp_path) -> IdempotencyLog:
    inst = IdempotencyLog(tmp_path / "side_effects.db")
    yield inst
    inst.close()


@pytest.fixture
def ledger(tmp_path) -> RunEventLedger:
    inst = RunEventLedger(tmp_path / "run_events.db")
    yield inst
    inst.close()


# -- helpers -----------------------------------------------------------------

RUN_ID = "run-contract-1"
SESSION_ID = "sess-contract-1"
CALL_ID = "call-write-1"


class _NoopProvider(ProviderClient):
    """Minimal provider — _execute_sync never calls it."""

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("provider should not be called by _execute_sync")

    def capabilities(self, model):
        return ModelCapabilities()


def _build_engine(tmp_path, idem_log, ledger, *, extra_tools=None):
    """Build a TurnEngine with idem_log and ledger wired in."""
    provider = _NoopProvider()
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    if extra_tools:
        registry.register_all(extra_tools)
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-test",
        idem_log=idem_log,
        ledger=ledger,
    )
    return engine


def _side_effect_events(ledger: RunEventLedger, run_id: str) -> list[dict]:
    return [
        e for e in ledger.events(run_id) if e["type"].startswith("side_effect.")
    ]


# -- Case 1: successful side effect ------------------------------------------


def test_case1_successful_side_effect_produces_planned_and_committed_events(
    idem, ledger, tmp_path
):
    """A successful tool call through _execute_sync must produce
    side_effect.planned and side_effect.committed events in the run ledger."""
    engine = _build_engine(tmp_path, idem, ledger)

    tc = ToolCall(
        id=CALL_ID,
        name="write_file",
        arguments={"path": "report.md", "content": "# Hello\n"},
    )
    token = set_current(RUN_ID, SESSION_ID)
    try:
        result, status = engine._tool_lifecycle._execute_sync(tc)
    finally:
        reset(token)

    assert status == "ok"
    assert result is not None

    events = _side_effect_events(ledger, RUN_ID)
    types = {e["type"] for e in events}
    assert "side_effect.planned" in types, (
        "side_effect.planned must appear in run_events.db when the engine "
        "records intent before tool execution"
    )
    assert "side_effect.committed" in types, (
        "side_effect.committed must appear in run_events.db when the engine "
        "commits a successful side effect"
    )


# -- Case 2: execution failure -----------------------------------------------


def test_case2_execution_failure_produces_planned_and_failed_events(
    idem, ledger, tmp_path
):
    """A tool that raises must produce side_effect.planned and
    side_effect.failed events in the run ledger."""

    def boom(path: str = "") -> dict:
        raise RuntimeError("simulated tool failure")

    engine = _build_engine(tmp_path, idem, ledger, extra_tools=[boom])

    tc = ToolCall(id=CALL_ID, name="boom", arguments={})
    token = set_current(RUN_ID, SESSION_ID)
    try:
        result, status = engine._tool_lifecycle._execute_sync(tc)
    finally:
        reset(token)

    assert status == "error"
    assert "error" in result

    events = _side_effect_events(ledger, RUN_ID)
    types = {e["type"] for e in events}
    assert "side_effect.planned" in types, (
        "side_effect.planned must appear even when the tool subsequently fails"
    )
    assert "side_effect.failed" in types, (
        "side_effect.failed must appear when a tool raises an exception"
    )


# -- Case 3: crash recovery sweep --------------------------------------------


def test_case3_crash_recovery_produces_uncertain_event(idem, ledger, tmp_path):
    """A Planned side effect interrupted by a crash must transition to
    Uncertain on cold-start sweep, producing side_effect.uncertain in the
    run ledger."""
    idem.record_planned(RUN_ID, CALL_ID, "write_file", {"path": "out.md"})
    idem.close()

    reopened = IdempotencyLog(tmp_path / "side_effects.db")
    try:
        stale = reopened.uncommitted_for_run(RUN_ID)
        assert len(stale) == 1

        swept = reopened.sweep_stale([RUN_ID], ledger=ledger)
        assert len(swept) == 1
        assert swept[0]["tool_call_id"] == CALL_ID
    finally:
        reopened.close()

    events = _side_effect_events(ledger, RUN_ID)
    types = {e["type"] for e in events}
    assert "side_effect.uncertain" in types, (
        "side_effect.uncertain must appear in run_events.db when sweep_stale "
        "transitions an interrupted side effect to Uncertain"
    )


# -- Case 4: resume does not re-execute committed side effect ----------------


def test_case4_resume_replays_committed_side_effect_without_recalling_tool(
    idem, ledger, tmp_path
):
    """A committed side effect must be replayed on resume — the tool
    must NOT be re-executed and no new side_effect.committed event must
    be produced."""
    stored_result = {"ok": True, "written": "report.md"}
    idem.commit(
        RUN_ID,
        CALL_ID,
        "write_file",
        {"path": "report.md", "content": "# Hello\n"},
        stored_result,
        ledger=ledger,
    )
    original_events = _side_effect_events(ledger, RUN_ID)
    original_committed = [
        e for e in original_events if e["type"] == "side_effect.committed"
    ]
    assert len(original_committed) == 1

    engine = _build_engine(tmp_path, idem, ledger)

    tc = ToolCall(
        id=CALL_ID,
        name="write_file",
        arguments={"path": "report.md", "content": "# Hello\n"},
    )
    token = set_current(RUN_ID, SESSION_ID)
    try:
        result, status = engine._tool_lifecycle._execute_sync(tc)
    finally:
        reset(token)

    assert status == "replayed"
    assert result == stored_result

    new_events = _side_effect_events(ledger, RUN_ID)
    new_committed = [
        e for e in new_events if e["type"] == "side_effect.committed"
    ]
    assert len(new_committed) == len(original_committed), (
        "resume must not produce a duplicate side_effect.committed event"
    )
