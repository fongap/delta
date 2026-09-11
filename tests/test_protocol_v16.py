"""AF-04/05/06 regression: multiplexed protocol v16.

Verifies the R5.1 hard acceptance criteria:

* AF-04: a long-running stream does not block ping/ledger commands.
* AF-05: request.cancel actually interrupts a running stream.
* AF-06: concurrent requests are safe; each gets its own response.

These tests use the real delta_core binary with the v16 protocol.
"""

from __future__ import annotations

import threading
import time

import pytest

from packages.delta_core_client import (
    DeltaCoreClient,
    DeltaCoreError,
    _find_delta_core_binary,
)

binary = _find_delta_core_binary()
if binary is None:
    pytest.skip("delta_core binary not found", allow_module_level=True)


@pytest.fixture()
def client():
    c = DeltaCoreClient(binary_path=binary)
    yield c
    c.close()


# -- AF-04: no head-of-line blocking -----------------------------------------


def test_ping_returns_during_stream(client):
    """AF-04: while a stream.echo stream is running, a ping command
    from another thread must return immediately — not block behind
    the stream."""
    gen = client.stream({"cmd": "stream.echo", "chunks": 10, "delay_ms": 200})

    # Consume the first delta to confirm the stream is in-flight.
    first = next(gen)
    assert first is not None

    # From another thread, issue ping — it must succeed within 5s.
    result_box: list = []
    error_box: list = []

    def ping():
        try:
            r = client.command({"cmd": "ping"})
            result_box.append(r)
        except Exception as e:
            error_box.append(e)

    t = threading.Thread(target=ping)
    t.start()
    t.join(timeout=5.0)

    assert not error_box, f"ping raised: {error_box}"
    assert result_box and result_box[0] == {"pong": True}
    assert not t.is_alive(), "ping thread did not finish in time"

    # Clean up the stream.
    for _ in gen:
        pass


def test_ledger_command_during_stream(client, tmp_path):
    """AF-04: ledger.append must succeed while a stream is running.
    In v15, the stream held the lock and ledger would block."""
    gen = client.stream({"cmd": "stream.echo", "chunks": 5, "delay_ms": 100})
    next(gen)  # stream is now in-flight

    # Append a ledger event — must not block.
    result = client.command({
        "cmd": "ledger.append",
        "db": str(tmp_path / "test.db"),
        "run_id": "r1",
        "type": "test.event",
        "actor": "test",
        "ts": time.time(),
        "payload": {"msg": "hello"},
        "workspace": "ws",
    })
    assert result is not None

    # Clean up.
    for _ in gen:
        pass


# -- AF-05: real cancellation --------------------------------------------------


def test_cancel_interrupts_stream(client):
    """AF-05: request.cancel must interrupt a running stream.
    The stream must stop early — not wait for all chunks to complete."""
    # Start a 50-chunk stream with 100ms delay = ~5s total.
    # We need the request_id to cancel it.
    # The client auto-assigns request_id; we can access it via the
    # _next_id counter before calling stream (it will be the next id).
    next_id = client._next_id + 1  # the id that stream() will allocate

    gen = client.stream({"cmd": "stream.echo", "chunks": 50, "delay_ms": 100})

    # Consume one delta to confirm stream is running.
    first = next(gen)
    assert first is not None

    # Cancel the stream.
    cancel_result = client.stream_cancel(next_id)
    assert cancel_result is not None
    assert cancel_result.get("cancelled") is True

    # The stream generator should stop (done/cancelled frame).
    # It should NOT yield all 50 chunks.
    remaining = 0
    try:
        for _ in gen:
            remaining += 1
    except (StopIteration, DeltaCoreError):
        pass

    # We should have received far fewer than 50 chunks.
    assert remaining < 45, f"stream was not cancelled (got {remaining} more chunks)"


# -- AF-06: concurrent requests safe -------------------------------------------


def test_concurrent_commands_each_get_response(client):
    """AF-06: multiple commands from different threads must each
    receive their own response — no cross-talk."""
    results: list = []
    errors: list = []
    n = 5
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        try:
            r = client.command({"cmd": "ping"})
            results.append((i, r))
        except Exception as e:
            errors.append((i, e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"concurrent errors: {errors}"
    assert len(results) == n
    for i, r in results:
        assert r == {"pong": True}
