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


async def _drain(agen):
    async for _ in agen:
        pass
