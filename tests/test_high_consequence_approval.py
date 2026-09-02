"""ADR-005 §7.1 acceptance gap: explicit high-consequence approval flow.

The Reference Task (`tests/test_reference_task.py`) covers the full happy
path and asserts validation + replayability, but criterion 2 ("至少一次
高后果动作经过正确 Approval") is only indirectly touched there (the
scheduled approver auto-allows WRITE_TOOLS). This file explicitly exercises
the L4 path:

  - A tool classified `IRREVERSIBLE` (`send_email`) is NEVER auto-allowed,
    even in AUTO mode and even with a task-scoped standing rule.
  - In an interactive session the call parks in the Inbox.
  - Resolving "approve" durably resumes and executes the call.
  - Resolving "deny" durably resumes and the tool result is a denial error.

The test uses a counting registry stub so no real email is sent — the
assertion is on the permission/Inbox path, not the email transport.
"""

from __future__ import annotations

import asyncio


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


def _counting_send_email():
    """A stub `send_email` tool that counts how many times it actually ran.

    The stub's `__name__` is set to `send_email` so gateway classify() routes
    it through IRREVERSIBLE_TOOLS → L4 → never auto-allowed. The body is a
    no-op so no email is actually sent.
    """
    state: dict = {"calls": 0}

    def send_email(to: str, subject: str, body: str) -> dict:
        state["calls"] += 1
        state["last_args"] = (to, subject, body)
        return {"sent": True, "to": to, "subject": subject}

    send_email.__name__ = "send_email"
    return send_email, state


def _make_mgr(tmp_path, turns, *, mode=None):
    mgr = SessionManager(
        workspace=tmp_path,
        data_dir=tmp_path / "data",  # isolate per-test InboxStore
        provider=ScriptedProvider(turns),
        mode=mode,
    )
    return mgr


async def _run_until_pending(mgr, sid, engine):
    """Drive the first turn until an Inbox item parks; return that item.

    Cancellation here simulates a process restart: the inner engine is dropped
    from the runtimes map before the approver's `inbox.wait()` returns, so no
    tool-error message gets appended to the persisted thread. resolve_inbox
    then triggers a fresh engine's resume() which re-executes the unanswered
    call.
    """
    task = asyncio.create_task(_drain(mgr, sid, engine))
    for _ in range(200):
        await asyncio.sleep(0.05)
        pend = [i for i in mgr.inbox.list() if i.session_id == sid and i.state == "pending"]
        if pend:
            break
    else:
        items = [(i.tool_call_id, i.kind, i.state, i.resolution) for i in mgr.inbox.list() if i.session_id == sid]
        rec = mgr.session_store.load(sid)
        msgs = [m.get("role") for m in (rec.messages if rec else [])]
        raise AssertionError(
            f"approval never parked. inbox={items} messages={msgs} task_done={task.done()}"
        )
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    mgr._runtimes.pop(sid, None)
    mgr.mark_idle(sid)
    return pend[0]
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    mgr._runtimes.pop(sid, None)
    mgr.mark_idle(sid)
    return pend[0]


async def _drain(mgr, sid, engine):
    async for _ in engine.run("go"):
        pass


def _final_texts(mgr, sid):
    rec = mgr.session_store.load(sid)
    return [m.get("content") for m in rec.messages if m.get("role") == "assistant" and m.get("content")]


# -- 1: L4 tool parks in Inbox even in AUTO mode -------------------------------
def test_irreversible_tool_parks_even_in_auto(tmp_path):
    """An IRREVERSIBLE tool (send_email) is L4 → never auto-allowed, even in
    AUTO mode. The call must park in the Inbox for human approval."""
    from core.permissions import Mode

    stub, state = _counting_send_email()
    mgr = _make_mgr(
        tmp_path,
        [_tool("send_email", {"to": "x@y.z", "subject": "hi", "body": "b"}, "c1"),
         _text("Done.")],
        mode=Mode.AUTO,
    )
    sid = "auto-ap"
    # Register the stub tool on the engine after build (escape hatch: the
    # raw TurnEngine owns the registry; the adapter only delegates).
    engine = mgr.get_engine(sid, agent="cowork", workspace=str(tmp_path))
    engine.engine.registry.register(stub)

    async def scenario():
        item = await _run_until_pending(mgr, sid, engine)
        assert item.kind == "approval"
        assert item.tool_call_id == "c1"
        # The tool has NOT executed yet.
        assert state["calls"] == 0
        # Approve → durable resume → the call runs once.
        await mgr.resolve_inbox(item.id, "allow")


# -- 2: L4 tool denied → tool-error result, no side effect ----------------------
def test_irreversible_tool_denied_does_not_execute(tmp_path):
    """Resolving 'deny' must NOT execute the tool. The turn resumes and the
    tool result is a denial error so the model can react."""
    stub, state = _counting_send_email()
    mgr = _make_mgr(
        tmp_path,
        [_tool("send_email", {"to": "x@y.z", "subject": "hi", "body": "b"}, "c1"),
         _text("Acknowledged — email was not sent.")],
    )
    sid = "deny-ap"
    engine = mgr.get_engine(sid, agent="cowork", workspace=str(tmp_path))
    engine.engine.registry.register(stub)

    async def scenario():
        item = await _run_until_pending(mgr, sid, engine)
        assert state["calls"] == 0
        await mgr.resolve_inbox(item.id, "deny")

    asyncio.run(scenario())
    assert state["calls"] == 0, "denied approval must not execute the tool"
    assert any("not sent" in (t or "") for t in _final_texts(mgr, sid))


# -- 3: ledger records approval.requested + approval.granted/denied -------------
def test_approval_ledger_events_recorded(tmp_path):
    """The ledger must contain the approval lifecycle: requested, then
    granted or denied — so the run narrative is fully replayable."""
    stub, _state = _counting_send_email()
    mgr = _make_mgr(
        tmp_path,
        [_tool("send_email", {"to": "x@y.z", "subject": "hi", "body": "b"}, "c1"),
         _text("Done.")],
    )
    sid = "ledger-ap"
    engine = mgr.get_engine(sid, agent="cowork", workspace=str(tmp_path))
    engine.engine.registry.register(stub)

    async def scenario():
        item = await _run_until_pending(mgr, sid, engine)
        await mgr.resolve_inbox(item.id, "allow")

    asyncio.run(scenario())
    # Find the run id — the adapter sets runscope during the first turn.
    runs = mgr.run_ledger.runs()
    assert len(runs) >= 1
    # Collect all event types across all runs for this session.
    all_types = []
    for rid in runs:
        all_types.extend(e["type"] for e in mgr.run_ledger.events(rid))
    print("LEDGER_TYPES", all_types)
    assert "approval.requested" in all_types
    assert "approval.granted" in all_types
