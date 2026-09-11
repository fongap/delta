"""R5.1 PR5 regression: live steering control channel.

Verifies:
* Steer modifies current run direction without creating a new run.
* Follow-up queues work for after run completion.
* Cancel stops the run.
* Ledger records steering events.
* Safe-point application works.
* Side-effect awareness: reject steer if action already occurred.
"""

from __future__ import annotations

import threading
import time

import pytest

from core.steering import (
    FollowUpQueue,
    RunControlChannel,
    SteerSafePoint,
    SteerState,
    create_control_channel,
    get_control_channel,
    remove_control_channel,
)


def test_steer_submitted_and_applied():
    """B3/B5: steer goes requested → applied, same run_id."""
    ch = RunControlChannel("run_steer_1")
    steer = ch.steer("Don't overwrite the original file.")
    assert steer.state == SteerState.REQUESTED
    assert steer.run_id == "run_steer_1"

    # Accept then apply at a safe point
    ch.steers.accept(steer.steer_id)
    ok = ch.apply_steer(steer.steer_id, safe_point=SteerSafePoint.DEFER_TO_SAFE_POINT)
    assert ok

    updated = ch.steers.list()[0]
    assert updated.state == SteerState.APPLIED
    assert updated.applied_at is not None
    # Same run_id — steering does not create a new run (B5)
    assert updated.run_id == "run_steer_1"


def test_follow_up_separate_from_steer():
    """B8: follow-up is queued separately and doesn't affect current run."""
    ch = RunControlChannel("run_fu_1")
    # Steer should modify current run
    ch.steer("Change format to PDF")
    # Follow-up should queue for later
    fu = ch.follow_up("After that, generate a summary")
    assert fu.content == "After that, generate a summary"
    assert ch.followups.list()[0] == fu
    # Steer is still pending (follow-up didn't affect it)
    assert ch.steers.has_pending()


def test_follow_up_queue_operations():
    """B8: follow-up queue supports add, remove, reorder."""
    q = FollowUpQueue("run_q")
    a = q.add("Task A")
    b = q.add("Task B")
    c = q.add("Task C")
    assert [i.content for i in q.list()] == ["Task A", "Task B", "Task C"]

    # Reorder: move C to position 0
    q.reorder(c.followup_id, 0)
    assert [i.content for i in q.list()] == ["Task C", "Task A", "Task B"]

    # Remove B
    q.remove(b.followup_id)
    assert [i.content for i in q.list()] == ["Task C", "Task A"]

    # Drain
    items = q.drain()
    assert len(items) == 2
    assert q.list() == []


def test_cancel_stops_run():
    """Cancel: sets cancelled flag."""
    ch = RunControlChannel("run_cancel_1")
    assert not ch.is_cancelled
    ch.cancel()
    assert ch.is_cancelled


def test_steer_rejected_because_action_occurred():
    """B7: if the action already happened, steer is rejected."""
    ch = RunControlChannel("run_reject_1")
    steer = ch.steer("Don't send the email")
    # Simulate: email was already sent
    ok = ch.reject_steer(steer.steer_id, reason="action already occurred at 14:32")
    assert ok
    updated = ch.steers.list()[0]
    assert updated.state == SteerState.REJECTED
    assert "already occurred" in updated.reason


def test_steer_deferred_to_safe_point():
    """B4: steer can be deferred until a safe point is reached."""
    ch = RunControlChannel("run_defer_1")
    steer = ch.steer("Switch to bullet points")
    ch.steers.accept(steer.steer_id)
    ch.steers.defer(steer.steer_id, safe_point=SteerSafePoint.PAUSE_BEFORE_CONSEQUENCE)
    updated = ch.steers.list()[0]
    assert updated.state == SteerState.DEFERRED
    assert updated.safe_point == SteerSafePoint.PAUSE_BEFORE_CONSEQUENCE

    # Later, at the safe point, apply
    ch.apply_steer(steer.steer_id, safe_point=SteerSafePoint.PAUSE_BEFORE_CONSEQUENCE)
    updated = ch.steers.list()[0]
    assert updated.state == SteerState.APPLIED


def test_ledger_records_steering_events():
    """B6: steering events are recorded in the ledger."""
    events: list = []

    class FakeLedger:
        def append(self, run_id, event_type, actor, ts, payload, workspace):
            events.append((event_type, payload))

    ch = RunControlChannel("run_ledger_1", ledger=FakeLedger(), workspace="ws")
    steer = ch.steer("Use bullet points")
    ch.apply_steer(steer.steer_id, safe_point=SteerSafePoint.APPLY_NOW)

    types = [e[0] for e in events]
    assert "user.steer.requested" in types
    assert "user.steer.applied" in types

    applied_event = next(p for t, p in events if t == "user.steer.applied")
    assert applied_event["steer_id"] == steer.steer_id
    assert applied_event["safe_point"] == "apply_now"


def test_global_registry():
    """Control channels can be created and retrieved globally."""
    create_control_channel("run_global_1")
    ch = get_control_channel("run_global_1")
    assert ch is not None
    assert ch.run_id == "run_global_1"
    remove_control_channel("run_global_1")
    assert get_control_channel("run_global_1") is None


def test_steer_thread_safe():
    """Multiple threads can submit steers concurrently."""
    ch = RunControlChannel("run_ts_1")
    n = 10
    barrier = threading.Barrier(n)
    steers: list = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        s = ch.steer(f"steer from thread")
        with lock:
            steers.append(s)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert len(steers) == n
    ids = {s.steer_id for s in steers}
    assert len(ids) == n  # all unique


def test_steer_injected_into_engine_at_safe_point(tmp_path):
    """R5.1 B3/B4 integration: a steer submitted to the run's control
    channel is polled at the engine's safe points and injected as a
    user message so the model sees the mid-execution direction change."""
    import asyncio

    from core.engine import TurnEngine
    from core.permissions import PermissionEngine
    from core.runscope import reset, set_current
    from core.steering import (
        create_control_channel,
        remove_control_channel,
    )
    from integrations.tools import ToolRegistry
    from providers import AssistantTurn, ProviderClient

    # --- control channel bound to a run_id ---
    run_id = "run_steer_engine"
    ch = create_control_channel(run_id, session_id="sess")
    ch.steer("actually, also do this now")

    class Scripted2(ProviderClient):
        def __init__(self, turns):
            self.turns = list(turns)
            self.calls = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            return self.turns.pop(0)

        def capabilities(self, model):
            from providers import ModelCapabilities

            return ModelCapabilities()

    provider = Scripted2([
        AssistantTurn(text="first", finish_reason="stop"),
        AssistantTurn(text="second", finish_reason="stop"),
    ])
    registry = ToolRegistry()
    registry.register_all(
        __import__("aisuite").toolkits.files(root=str(tmp_path), allow_write=True)
    )
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
    )

    async def run_under_scope():
        token = set_current(run_id, "sess")
        try:
            return [ev async for ev in engine.run("do the things")]
        finally:
            reset(token)

    events = asyncio.run(run_under_scope())

    # The steer should have been injected as a user message with source steer.
    injected = [
        m for m in engine.messages
        if m.get("role") == "user" and m.get("content") == "actually, also do this now"
        and m.get("source") == {"source": "steer"}
    ]
    assert injected, "steer was not injected into engine messages"

    # The steer in the control channel should be APPLIED.
    updated = ch.steers.list()
    assert any(s.state == SteerState.APPLIED for s in updated)

    remove_control_channel(run_id)
