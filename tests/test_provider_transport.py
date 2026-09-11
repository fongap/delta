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


# -- Anthropic Messages tests -------------------------------------------------


class _MockAnthropicHandler(BaseHTTPRequestHandler):
    """Returns canned Anthropic Messages API responses."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        req = json.loads(body) if body else {}

        if req.get("stream"):
            self._stream_response()
        else:
            self._non_stream_response()

    def _non_stream_response(self):
        resp = {
            "id": "msg_test",
            "content": [
                {"type": "thinking", "thinking": "I should greet.", "signature": "sig"},
                {"type": "text", "text": "Hello from Anthropic!"},
                {
                    "type": "tool_use",
                    "id": "tool_123",
                    "name": "get_weather",
                    "input": {"city": "SF"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {
                "input_tokens": 50,
                "output_tokens": 20,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 3,
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
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 50, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " world"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "thinking_delta", "thinking": "reasoning"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "tool_1", "name": "search", "input": {}}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"q":"test'}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"}'}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 15}},
            {"type": "message_stop"},
        ]
        for evt in events:
            line = f"event: {evt['type']}\ndata: {json.dumps(evt)}\n\n"
            self.wfile.write(line.encode())
        self.wfile.flush()

    def log_message(self, *args, **kwargs):
        pass


@pytest.fixture()
def mock_anthropic_server():
    server = HTTPServer(("127.0.0.1", 0), _MockAnthropicHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_provider_complete_anthropic(client, mock_anthropic_server):
    """provider.complete returns parsed AssistantTurn for Anthropic Messages."""
    result = client.command({
        "cmd": "provider.complete",
        "protocol": "anthropic",
        "model": "claude-test",
        "messages": [{"role": "user", "content": "hi"}],
        "settings": {"max_tokens": 1024},
        "api_key": "test-key",
        "base_url": mock_anthropic_server,
    })
    assert result["text"] == "Hello from Anthropic!"
    assert result["finish_reason"] == "tool_calls"
    assert result["reasoning"] == "I should greet."
    assert result["tool_calls"][0]["id"] == "tool_123"
    assert result["tool_calls"][0]["name"] == "get_weather"
    assert result["tool_calls"][0]["arguments"] == {"city": "SF"}
    assert result["usage"]["input"] == 50
    assert result["usage"]["output"] == 20
    assert result["usage"]["cache_read"] == 3
    assert result["usage"]["cache_write"] == 5


def test_provider_stream_anthropic(client, mock_anthropic_server):
    """provider.stream yields text/reasoning deltas for Anthropic Messages."""
    deltas = []
    gen = client.stream({
        "cmd": "provider.stream",
        "protocol": "anthropic",
        "model": "claude-test",
        "messages": [{"role": "user", "content": "hi"}],
        "settings": {"max_tokens": 1024},
        "api_key": "test-key",
        "base_url": mock_anthropic_server,
    })
    for data in gen:
        deltas.append(data)
    text_deltas = [d for d in deltas if "text_delta" in d]
    reasoning_deltas = [d for d in deltas if "reasoning_delta" in d]
    assert len(text_deltas) == 2
    assert text_deltas[0]["text_delta"] == "Hello"
    assert text_deltas[1]["text_delta"] == " world"
    assert len(reasoning_deltas) == 1
    assert reasoning_deltas[0]["reasoning_delta"] == "reasoning"
