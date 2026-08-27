"""RuntimePort / TurnEngineAdapter: the application layer must drive the runtime
only through the port surface (bind/run/resume/retry/steer/interrupt). These tests
pin the delegation contract — the adapter is thin by design, and any behavior that
leaks around it belongs in engine tests instead."""

import asyncio

import pytest

from delta.runtime import RuntimePort, TurnEngineAdapter


class FakeEngine:
    """Duck-typed TurnEngine stand-in recording the calls it receives."""

    def __init__(self):
        self.approver = None
        self.directory_requester = None
        self.plan_approver = None
        self.question_asker = None
        self.steered = []
        self.interrupted = 0

    def queue_steering(self, text, source=None):
        self.steered.append((text, source))

    def request_interrupt(self):
        self.interrupted += 1

    async def run(self, user_input, *, source=None, display=None):
        yield ("turn_start", {"input": user_input})
        yield ("turn_done", {})

    async def resume(self):
        yield ("turn_start", {"resumed": True})

    async def retry(self):
        yield ("turn_start", {"retried": True})


def test_adapter_satisfies_the_port_protocol():
    assert isinstance(TurnEngineAdapter(FakeEngine()), RuntimePort)


def test_bind_attaches_only_provided_callbacks():
    eng = FakeEngine()

    async def approver(*a, **k):
        return None

    TurnEngineAdapter(eng).bind(approver=approver)
    assert eng.approver is approver
    # Untouched callbacks stay as the engine initialized them (None) — bind must not
    # stomp existing decisions with defaults.
    assert eng.directory_requester is None
    assert eng.plan_approver is None
    assert eng.question_asker is None


def test_steer_and_interrupt_delegate():
    eng = FakeEngine()
    rt = TurnEngineAdapter(eng)
    rt.steer("先别剔除异常值", source={"connector": "slack"})
    rt.steer("plain")
    rt.interrupt()
    assert eng.steered == [
        ("先别剔除异常值", {"connector": "slack"}),
        ("plain", None),
    ]
    assert eng.interrupted == 1


@pytest.mark.asyncio
async def test_run_streams_engine_events_unchanged():
    seen = []

    async def consume(gen):
        async for ev in gen:
            seen.append(ev)

    rt = TurnEngineAdapter(FakeEngine())
    await asyncio.wait_for(consume(rt.run("hello")), timeout=5)
    assert [ev[0] for ev in seen] == ["turn_start", "turn_done"]
    assert seen[0][1] == {"input": "hello"}


@pytest.mark.asyncio
async def test_resume_and_retry_delegate():
    rt = TurnEngineAdapter(FakeEngine())

    async def first(gen):
        return [ev async for ev in gen]

    resumed = await asyncio.wait_for(first(rt.resume()), timeout=5)
    retried = await asyncio.wait_for(first(rt.retry()), timeout=5)
    assert resumed[0][1] == {"resumed": True}
    assert retried[0][1] == {"retried": True}
