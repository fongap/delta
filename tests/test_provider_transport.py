"""Tests for R5 Phase 1 provider transport (ADR-047).

Tests provider.complete and provider.stream (OpenAI Chat Completions)
using a local mock HTTP server that returns canned responses.
"""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytest

from packages.delta_core_client import (
    DeltaCoreClient,
    _find_delta_core_binary,
)

binary = _find_delta_core_binary()
if binary is None:
    pytest.skip("delta_core binary not found", allow_module_level=True)


class _MockHandler(BaseHTTPRequestHandler):
    """Returns canned OpenAI Chat Completions responses."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        req = json.loads(body) if body else {}

        if req.get("stream"):
            self._stream_response()
        else:
            self._non_stream_response()

    def _non_stream_response(self):
        resp = {
            "id": "chatcmpl-test",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello from mock!",
                        "reasoning_content": "thinking about it",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
        }
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream_response(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}, "index": 0}]},
            {"choices": [{"delta": {"content": " world"}, "index": 0}]},
            {"choices": [{"delta": {"reasoning_content": "thinking"}, "index": 0}]},
            {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]},
            {"usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ]
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args, **kwargs):
        pass


@pytest.fixture()
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def client():
    c = DeltaCoreClient(binary_path=binary)
    yield c
    c.close()


def test_provider_complete_openai_chat(client, mock_server):
    """provider.complete returns parsed AssistantTurn for OpenAI Chat."""
    result = client.command({
        "cmd": "provider.complete",
        "protocol": "openai_chat",
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hi"}],
        "api_key": "test-key",
        "base_url": mock_server,
    })
    assert result["text"] == "Hello from mock!"
    assert result["finish_reason"] == "stop"
    assert result["reasoning"] == "thinking about it"
    assert result["usage"]["input"] == 8
    assert result["usage"]["output"] == 5
    assert result["usage"]["cache_read"] == 2
    assert result["usage"]["cache_write"] == 0


def test_provider_stream_openai_chat(client, mock_server):
    """provider.stream yields text/reasoning deltas then done."""
    deltas = []
    gen = client.stream({
        "cmd": "provider.stream",
        "protocol": "openai_chat",
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hi"}],
        "api_key": "test-key",
        "base_url": mock_server,
    })
    for data in gen:
        deltas.append(data)

    text_deltas = [d for d in deltas if "text_delta" in d]
    reasoning_deltas = [d for d in deltas if "reasoning_delta" in d]
    assert len(text_deltas) == 2
    assert text_deltas[0]["text_delta"] == "Hello"
    assert text_deltas[1]["text_delta"] == " world"
    assert len(reasoning_deltas) == 1
    assert reasoning_deltas[0]["reasoning_delta"] == "thinking"


def test_provider_complete_unknown_protocol(client, mock_server):
    """Unknown protocol returns error."""
    with pytest.raises(Exception):
        client.command({
            "cmd": "provider.complete",
            "protocol": "unknown",
            "model": "test",
            "messages": [],
            "api_key": "k",
            "base_url": mock_server,
        })
