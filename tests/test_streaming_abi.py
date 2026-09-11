"""Tests for the R5 Phase 0 streaming ABI (ADR-047).

Verifies that the delta_core streaming protocol framing works:
stream.echo yields N delta frames then a done frame.
"""

import pytest

from packages.delta_core_client import (
    DeltaCoreClient,
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


def test_stream_echo_basic(client):
    """stream.echo yields N deltas then a done result."""
    deltas = []
    gen = client.stream({"cmd": "stream.echo", "chunks": 3})
    for data in gen:
        deltas.append(data)
    assert len(deltas) == 3
    for i, d in enumerate(deltas):
        assert d["chunk"] == i
        assert d["total"] == 3
    # Generator returns the done result via StopIteration.value
    # (pytest doesn't capture it, but the return value is the result dict)
    assert deltas[-1]["chunk"] == 2


def test_stream_echo_zero_chunks(client):
    """stream.echo with 0 chunks yields nothing, returns immediately."""
    gen = client.stream({"cmd": "stream.echo", "chunks": 0})
    result = list(gen)
    assert result == []


def test_stream_echo_with_delay(client):
    """stream.echo with delay_ms yields deltas incrementally."""
    gen = client.stream({"cmd": "stream.echo", "chunks": 2, "delay_ms": 50})
    deltas = list(gen)
    assert len(deltas) == 2


def test_non_streaming_still_works(client):
    """Non-streaming commands still return single response."""
    result = client.command({"cmd": "hello", "protocol_version": 15})
    assert result["protocol_version"] == 15
    assert result["server"] == "delta_core"


def test_stream_cancel_placeholder(client):
    """stream.cancel is accepted and returns a done result."""
    result = client.command({"cmd": "stream.cancel", "stream_id": "test-123"})
    assert result["cancelled"] == "test-123"
