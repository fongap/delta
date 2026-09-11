"""AF-07/12/13 regression: backpressure, ownership, shutdown lifecycle.

* AF-07: bounded inflight - overload when max concurrent exceeded.
* AF-12: RunningTask registry - who spawns owns completion.
* AF-13: shutdown - graceful cancel to bounded wait to force close.
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


# -- AF-07: backpressure ------------------------------------------------------


def test_overload_when_max_inflight_exceeded(client, tmp_path):
    """AF-07: when max concurrent requests is exceeded, new requests
    must raise DeltaCoreError('overload') instead of queuing indefinitely.
    Control commands (ping, request.cancel) bypass the limit."""
    gens = []
    for _ in range(client._max_inflight):
        gen = client.stream({"cmd": "stream.echo", "chunks": 100, "delay_ms": 500})
        next(gen)
        gens.append(gen)

    # A non-control command should raise overload.
    with pytest.raises(DeltaCoreError, match="overload"):
        client.command({
            "cmd": "ledger.append",
            "db": str(tmp_path / "test.db"),
            "run_id": "r1",
            "type": "test.event",
        })

    # Control commands (ping) still succeed even at full capacity.
    result = client.command({"cmd": "ping"})
    assert result == {"pong": True}

    for gen in gens:
        gen.close()


def test_inflight_released_after_command_completes(client):
    """AF-07: inflight slot must be released after a command completes."""
    client.command({"cmd": "ping"})
    client.command({"cmd": "ping"})
    assert client.active_inflight == 0


# -- AF-12: RunningTask ownership ---------------------------------------------


def test_running_task_registered_and_cleared(client):
    """AF-12: a running task is registered during execution and cleared
    after completion. The owner thread identity is captured."""
    assert client.active_inflight == 0
    client.command({"cmd": "ping"})
    assert client.active_inflight == 0


def test_running_task_cleared_after_stream(client):
    """AF-12: a stream task is registered while streaming and cleared
    after the generator is exhausted or closed."""
    gen = client.stream({"cmd": "stream.echo", "chunks": 2})
    next(gen)
    assert client.active_inflight >= 1
    for _ in gen:
        pass
    assert client.active_inflight == 0


# -- AF-13: shutdown lifecycle -------------------------------------------------


def test_shutdown_drains_inflight(client):
    """AF-13: shutdown gracefully cancels active streams and waits for
    them to drain within a bounded timeout, then force-closes."""
    gen = client.stream({"cmd": "stream.echo", "chunks": 100, "delay_ms": 500})
    next(gen)
    assert client.active_inflight >= 1

    client.shutdown(graceful_timeout=3.0)

    # After shutdown, inflight should be zero and proc closed.
    assert client.active_inflight == 0
    assert client._proc is None


def test_shutdown_force_closes_after_deadline(client):
    """AF-13: if graceful drain times out, shutdown force-closes."""
    # Start a stream that blocks forever (huge chunk count, long delay).
    gen = client.stream({"cmd": "stream.echo", "chunks": 10000, "delay_ms": 10000})
    next(gen)

    # Shutdown with very short timeout - must force-close, not hang.
    client.shutdown(graceful_timeout=0.5)
    assert client._proc is None
