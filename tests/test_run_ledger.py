"""Run Event Ledger: hash-chained durable events per run + cold-start recovery.

Contract (docs/run-ledger-adr.md):
- append-only, hash = sha256(prev_hash | seq | type | actor | ts | canonical payload)
- terminal events close a run; open runs get synthetic `run.interrupted` on recovery
- the TurnEngineAdapter turns every driven turn into a ledgered run
"""

import asyncio

import pytest

from coworker.ledger import RunEventLedger
from coworker.runtime import RuntimePort, TurnEngineAdapter


class FakeEngine:
    def queue_steering(self, text, source=None):
        pass

    def request_interrupt(self):
        pass

    async def run(self, user_input, *, source=None, display=None):
        yield ("turn_start", {"input": user_input})
        yield ("turn_done", {})

    async def resume(self):
        yield ("turn_start", {})
        raise RuntimeError("boom mid-resume")

    async def retry(self):
        yield ("turn_start", {})


def test_append_chains_hashes_per_run(tmp_path):
    led = RunEventLedger(tmp_path / "events.db")
    led.append("r1", "run.started")
    led.append("r1", "tool.completed", payload={"artifact": "a1"})
    led.append("r2", "run.started")  # separate chain
    e1 = led.events("r1")
    e2 = led.events("r2")
    assert [e["seq"] for e in e1] == [1, 2]
    assert e2[0]["seq"] == 1 and e2[0]["prev_hash"] == ""
    assert e1[1]["prev_hash"] == e1[0]["hash"]
    assert led.verify("r1") and led.verify("r2")


def test_verify_detects_tampering(tmp_path):
    led = RunEventLedger(tmp_path / "events.db")
    led.append("r1", "run.started")
    led.append("r1", "run.completed", payload={"status": "ok"})
    # Tamper with the middle of the chain directly in SQLite.
    led._conn.execute(
        "UPDATE run_events SET type = 'run.failed' WHERE seq = 1"
    )
    led._conn.commit()
    assert not led.verify("r1")


def test_open_runs_and_recovery(tmp_path):
    led = RunEventLedger(tmp_path / "events.db")
    led.append("done", "run.started")
    led.append("done", "run.completed")
    led.append("stale", "run.started")

    assert led.open_runs() == ["stale"]
    recovered = led.recover_stale()
    assert len(recovered) == 1
    ev = recovered[0]
    assert ev["type"] == "run.interrupted"
    assert ev["payload"]["reason"] == "crashed"
    assert led.verify("stale")
    assert led.open_runs() == []
    # Recovery is idempotent.
    assert led.recover_stale() == []


@pytest.mark.asyncio
async def test_adapter_ledgers_a_completed_run(tmp_path):
    led = RunEventLedger(tmp_path / "events.db")
    rt = TurnEngineAdapter(FakeEngine(), ledger=led, session_id="s1")
    assert isinstance(rt, RuntimePort)
    seen = [ev async for ev in rt.run("hello")]
    assert len(seen) == 2
    runs = led.runs()
    assert len(runs) == 1
    types = [e["type"] for e in led.events(runs[0])]
    assert types == ["run.started", "run.completed"]
    started = led.events(runs[0])[0]
    assert started["actor"] == "user"
    assert started["payload"]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_adapter_ledgers_a_failed_resume(tmp_path):
    led = RunEventLedger(tmp_path / "events.db")
    rt = TurnEngineAdapter(FakeEngine(), ledger=led, session_id="s1")
    with pytest.raises(RuntimeError):
        async for _ in rt.resume():
            pass
    events = led.events(led.runs()[0])
    assert events[-1]["type"] == "run.failed"
    assert "boom" in events[-1]["payload"]["reason"]
    # A failed run is terminal — nothing left open.
    assert led.open_runs() == []


@pytest.mark.asyncio
async def test_adapter_without_ledger_still_streams(tmp_path):
    rt = TurnEngineAdapter(FakeEngine())
    seen = [ev async for ev in rt.run("hi")]
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_concurrent_runs_get_distinct_chains(tmp_path):
    led = RunEventLedger(tmp_path / "events.db")
    rt = TurnEngineAdapter(FakeEngine(), ledger=led, session_id="s1")
    await asyncio.gather(*[_drain(rt.run("a")), _drain(rt.run("b"))])
    assert len(led.runs()) == 2
    for r in led.runs():
        assert led.verify(r)


# -- run scope + process events (spawn/kill land in the run's chain) -------------

def test_run_scope_is_empty_outside_a_turn():
    from coworker import runscope

    assert runscope.current() is None
    token = runscope.set_current("run-x", "sess-1")
    try:
        assert runscope.current() == ("run-x", "sess-1")
    finally:
        runscope.reset(token)
    assert runscope.current() is None


@pytest.mark.asyncio
async def test_scope_is_visible_inside_the_driven_turn_and_reset_after(tmp_path):
    from coworker import runscope

    led = RunEventLedger(tmp_path / "events.db")
    seen_in_turn = []
    run_id_holder = []

    class ScopeSpyEngine(FakeEngine):
        async def run(self, user_input, *, source=None, display=None):
            scope = runscope.current()
            if scope is not None:
                seen_in_turn.append(scope)
                run_id_holder.append(scope[0])
            yield ("turn_start", {})
            yield ("turn_done", {})

    rt = TurnEngineAdapter(ScopeSpyEngine(), ledger=led, session_id="sess-9")
    await _drain(rt.run("go"))
    assert seen_in_turn and seen_in_turn[0][1] == "sess-9"
    # The scope's run id matches the ledger's chain for this run.
    assert run_id_holder[0] in led.runs()
    # ...and the scope is gone once the turn ends.
    assert runscope.current() is None


@pytest.mark.asyncio
async def test_process_spawn_kill_events_land_in_the_run_chain(tmp_path):
    """Background spawn + kill facts reported while a turn is driven become durable
    process events attributed to the run that caused them (the manager's recorder
    reads the ambient run scope) — no signature threading through build_engine."""
    from coworker import runscope
    from coworker.sanitize import sanitize_payload

    led = RunEventLedger(tmp_path / "events.db")

    def record(event):
        # The manager's _record_process_event: ledger inside a run's scope.
        scope = runscope.current()
        assert scope is not None, "process event observed outside any run"
        led.append(scope[0], event["event"], actor="tool", payload=sanitize_payload(
            {k: v for k, v in event.items() if k != "event"}
        ))

    COMMAND = "python worker.py --token=supersecret"

    class SpawnEngine(FakeEngine):
        async def run(self, user_input, *, source=None, display=None):
            record(
                {
                    "event": "process.spawned",
                    "task_id": "bg-1",
                    "pid": 4242,
                    "command": COMMAND,
                    "detach": False,
                }
            )
            record(
                {
                    "event": "process.killed",
                    "task_id": "bg-1",
                    "pid": 4242,
                    "command": COMMAND,
                    "detach": False,
                }
            )
            yield ("turn_start", {})
            yield ("turn_done", {})

    rt = TurnEngineAdapter(SpawnEngine(), ledger=led, session_id="s1")
    await _drain(rt.run("start the worker"))

    run_id = led.runs()[0]
    types = [e["type"] for e in led.events(run_id)]
    assert types == [
        "run.started",
        "process.spawned",
        "process.killed",
        "run.completed",
    ]
    assert led.verify(run_id)
    spawned = led.events(run_id)[1]
    assert spawned["actor"] == "tool"
    assert spawned["payload"]["task_id"] == "bg-1"
    # No scope leaks after the run.
    assert runscope.current() is None


async def _drain(agen):
    async for _ in agen:
        pass
