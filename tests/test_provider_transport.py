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
from providers.openai_provider import OpenAIProvider

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


# -- OpenAI Responses tests ---------------------------------------------------


class _MockResponsesHandler(BaseHTTPRequestHandler):
    """Returns canned OpenAI Responses API responses."""

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
            "id": "resp_test",
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Hello from responses!"}]},
                {"type": "reasoning", "summary": [{"text": "thinking hard"}]},
                {
                    "type": "function_call",
                    "id": "fc_456",
                    "name": "lookup",
                    "arguments": {"term": "delta"},
                },
            ],
            "incomplete_details": None,
            "usage": {"input_tokens": 30, "output_tokens": 10},
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
        full = {
            "id": "resp_test",
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Hello streamed world"}]},
                {"type": "reasoning", "summary": [{"text": "stream thinking"}]},
                {"type": "function_call", "id": "fc_1", "name": "search", "arguments": {"q": "test"}},
            ],
            "incomplete_details": None,
            "usage": {"input_tokens": 30, "output_tokens": 10},
        }
        events = [
            ("response.created", {"type": "response.created", "response": full}),
            ("response.in_progress", {"type": "response.in_progress", "response": full}),
            ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "Hello"}),
            ("response.output_text.delta", {"type": "response.output_text.delta", "delta": " streamed"}),
            ("response.output_text.delta", {"type": "response.output_text.delta", "delta": " world"}),
            ("response.reasoning_summary_text.delta", {"type": "response.reasoning_summary_text.delta", "delta": "stream thinking"}),
            ("response.completed", {"type": "response.completed", "response": full}),
        ]
        for evt_name, data in events:
            line = f"event: {evt_name}\ndata: {json.dumps(data)}\n\n"
            self.wfile.write(line.encode())
        self.wfile.flush()

    def log_message(self, *args, **kwargs):
        pass


@pytest.fixture()
def mock_responses_server():
    server = HTTPServer(("127.0.0.1", 0), _MockResponsesHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_provider_complete_openai_responses(client, mock_responses_server):
    """provider.complete parses OpenAI Responses output."""
    result = client.command({
        "cmd": "provider.complete",
        "protocol": "openai_responses",
        "model": "gpt-5.6-test",
        "messages": [{"role": "user", "content": "hi"}],
        "settings": {"instructions": "be brief"},
        "api_key": "test-key",
        "base_url": mock_responses_server,
    })
    assert result["text"] == "Hello from responses!"
    assert result["finish_reason"] == "tool_calls"
    assert result["reasoning"] == "thinking hard"
    assert result["tool_calls"][0]["id"] == "fc_456"
    assert result["tool_calls"][0]["name"] == "lookup"
    assert result["tool_calls"][0]["arguments"] == {"term": "delta"}
    assert result["usage"]["input"] == 30
    assert result["usage"]["output"] == 10


def test_provider_stream_openai_responses(client, mock_responses_server):
    """provider.stream yields text/reasoning deltas for OpenAI Responses."""
    deltas = []
    gen = client.stream({
        "cmd": "provider.stream",
        "protocol": "openai_responses",
        "model": "gpt-5.6-test",
        "messages": [{"role": "user", "content": "hi"}],
        "api_key": "test-key",
        "base_url": mock_responses_server,
    })
    for data in gen:
        deltas.append(data)
    text_deltas = [d for d in deltas if "text_delta" in d]
    reasoning_deltas = [d for d in deltas if "reasoning_delta" in d]
    assert len(text_deltas) == 3
    assert text_deltas[0]["text_delta"] == "Hello"
    assert text_deltas[-1]["text_delta"] == " world"
    assert len(reasoning_deltas) == 1
    assert reasoning_deltas[0]["reasoning_delta"] == "stream thinking"


# -- R5 Phase 1d: OpenAIProvider.complete() delegates to Rust -----------------


def test_openai_provider_complete_delegates_to_rust(client, mock_server):
    """OpenAIProvider.complete() with `core` delegates the wire call to Rust.

    The provider keeps decision/parsing (caps, param-fix retry, health, salvage)
    in Python but sends the actual HTTP request through delta_core's
    `provider.complete` command.
    """
    provider = OpenAIProvider(
        core=client,
        api_key="test-key",
        base_url=mock_server,
        endpoint_caps=None,
        endpoint_key=None,
    )
    turn = provider.complete(
        model="gpt-test",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert turn.text == "Hello from mock!"
    assert turn.finish_reason == "stop"
    assert turn.reasoning == "thinking about it"
    assert turn.usage is not None
    assert turn.usage.input == 8
    assert turn.usage.output == 5
    assert turn.usage.cache_read == 2


def test_openai_provider_stream_delegates_to_rust(client, mock_server):
    """OpenAIProvider.stream() with `core` delegates the wire call to Rust.

    Yields text/reasoning deltas + a final turn, with truncation detection.
    """
    provider = OpenAIProvider(
        core=client,
        api_key="test-key",
        base_url=mock_server,
        endpoint_caps=None,
        endpoint_key=None,
    )
    chunks = list(
        provider.stream(model="gpt-test", messages=[{"role": "user", "content": "hi"}])
    )
    text_chunks = [c for c in chunks if c.text_delta is not None]
    reasoning_chunks = [c for c in chunks if c.reasoning_delta is not None]
    turn_chunk = next(c for c in chunks if c.turn is not None)

    assert len(text_chunks) == 2
    assert text_chunks[0].text_delta == "Hello"
    assert text_chunks[1].text_delta == " world"
    assert len(reasoning_chunks) == 1
    assert reasoning_chunks[0].reasoning_delta == "thinking"
    assert turn_chunk.turn.text == "Hello world"
    assert turn_chunk.turn.finish_reason == "stop"
    assert turn_chunk.turn.reasoning == "thinking"
    assert turn_chunk.turn.usage is not None
    assert turn_chunk.turn.usage.output == 5
