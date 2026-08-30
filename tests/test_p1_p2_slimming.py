"""v0.3.0 P1/P2: schema slimming, read-only tool routing, error classification +
bounded retry, endpoint param negotiation, provider health profile."""

from __future__ import annotations

import asyncio
import json

from core import call_errors
from core.call_errors import (
    ErrorClass,
    ProtocolIncompatibleError,
    StreamTruncatedError,
    TTFTTimeoutError,
    backoff_delay,
    classify_error,
    is_retryable,
)
from core.engine import TurnEngine
from core.permissions import Mode, PermissionEngine
from integrations.tools import ToolRegistry
from providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from providers import health as _health
from providers.openai_provider import (
    OpenAIProvider,
    _apply_endpoint_caps,
    _param_fix_retry,
)


# -- schema slimming -----------------------------------------------------------


def test_registry_slims_verbose_schema():
    reg = ToolRegistry()

    def long_doc(path: str) -> dict:
        """Write a file. This description is extremely long and repeats the same
        information many times over because the original docstring was written for
        humans and never meant to ride into the model context on every single call,
        which wastes prompt-processing time on weak and shared gateways where the
        model already knows what write_file does."""
        return {}

    reg.register(long_doc)
    fn = reg.get("long_doc").schema["function"]
    assert len(fn["description"]) <= 300
    assert "title" not in fn["parameters"]
    # Structure the runtime reads is preserved verbatim.
    assert set(fn["parameters"]["properties"]) == {"path"}


def test_registry_keeps_structure_for_tool_index():
    reg = ToolRegistry()

    def pick(mode: str, values: list[str]) -> dict:
        """Pick one."""
        return {}

    reg.register(pick)
    fn = reg.get("pick").schema["function"]
    props = fn["parameters"]["properties"]
    assert props["mode"]["type"] == "string"
    assert "values" in props


def test_slim_is_idempotent():
    from integrations.tools.registry import slim_schema

    def f(x: int) -> dict:
        """A short docstring."""
        return {}

    schema = slim_schema({"type": "function", "function": {"name": "f"}})
    assert schema["function"]["name"] == "f"


# -- read-only tool routing ----------------------------------------------------


def _engine_registry():
    reg = ToolRegistry()

    def run_shell(command: str) -> dict:
        """Run a shell command."""
        return {}

    def write_file(path: str, content: str) -> dict:
        """Write a file."""
        return {}

    def read_file(path: str) -> dict:
        """Read a file."""
        return {}

    def ask_user(question: str) -> dict:
        """Ask the user."""
        return {}

    reg.register_all([run_shell, write_file, read_file, ask_user])
    return reg


def test_readonly_turn_drops_write_and_shell_tools():
    from core.tool_selection import select_tool_names

    names = [
        "run_shell",
        "write_file",
        "read_file",
        "ask_user",
    ]
    # A files-signal task with NO write/exec intent — read-only.
    messages = [{"role": "user", "content": "读取这个文件的内容并告诉我"}]
    selected = select_tool_names(names, messages, read_only=True)
    assert "run_shell" not in selected
    assert "write_file" not in selected
    assert "read_file" in selected  # read tools still ride the files category
    assert "ask_user" in selected  # core stays

    # Same task WITHOUT read_only keeps the write tool.
    selected_full = select_tool_names(names, messages, read_only=False)
    assert "write_file" in selected_full

    # Non-read-only task keeps them too.
    messages2 = [{"role": "user", "content": "把这个报告保存成文件"}]
    selected2 = select_tool_names(names, messages2, read_only=False)
    assert "write_file" in selected2


def test_is_readonly_turn_detects_read_intent():
    from core.tool_selection import is_readonly_turn

    assert is_readonly_turn([{"role": "user", "content": "读取这个文件"}])
    assert not is_readonly_turn([{"role": "user", "content": "把文件保存下来"}])
    assert not is_readonly_turn([{"role": "user", "content": "继续"}])  # ambiguous


def test_plan_mode_is_readonly(tmp_path):
    engine = TurnEngine(
        provider=_StreamingProvider(AssistantTurn(text="ok", finish_reason="stop")),
        registry=_engine_registry(),
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.PLAN),
        model="m",
    )
    tools, names, mode = engine._tools_for_call()
    assert "run_shell" not in names
    assert "write_file" not in names
    assert "ask_user" in names


# -- error classification + retry ----------------------------------------------


def test_classify_stream_truncated():
    exc = StreamTruncatedError("上游流式响应被截断")
    assert classify_error(exc) is ErrorClass.STREAM_TRUNCATED
    assert not is_retryable(exc)  # finish_reason guard stays loud


def test_classify_protocol_and_context():
    assert classify_error(ProtocolIncompatibleError("stream_options is not supported")) is (
        ErrorClass.PROTOCOL_INCOMPATIBLE
    )
    assert classify_error(
        RuntimeError("context_length_exceeded: maximum context length")
    ) is ErrorClass.CONTEXT_TOO_LARGE
    assert not is_retryable(RuntimeError("context window exceeded"))
    assert is_retryable(RuntimeError("429 rate limit"))
    assert is_retryable(TTFTTimeoutError("stalled"))


def test_backoff_increases_and_jitters():
    d1 = backoff_delay(0, base_ms=200)
    d2 = backoff_delay(1, base_ms=200)
    assert d1 < d2  # exponential, jitter ±10% keeps order on 2x
    assert 0.8 * 0.2 <= d1 <= 1.2 * 0.2


class _StreamingProvider(ProviderClient):
    def __init__(self, turn, *, error=None, fail_times=0):
        self._turn = turn
        self._error = error
        self._fail = fail_times
        self.calls = 0

    def stream(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if self._fail and self.calls <= self._fail:
            raise RuntimeError("429 too many requests")
        if self._error:
            raise self._error
        yield StreamChunk(turn=self._turn)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turn

    def capabilities(self, model):
        return ModelCapabilities()


def _run(engine, text="hi"):
    async def go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(go())


def test_transient_failure_is_retried(tmp_path):
    provider = _StreamingProvider(
        AssistantTurn(text="ok", finish_reason="stop"), fail_times=2
    )
    engine = TurnEngine(
        provider=provider,
        registry=_engine_registry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="m",
        max_retries=3,
    )
    rows = []
    engine.request_logger = rows.append
    events = _run(engine)
    assert provider.calls == 3  # 2 failures + 1 success
    assert events[-1].type.name == "TURN_END"
    # 2 error rows (retried) then 1 ok.
    assert [r["outcome"] for r in rows] == ["error", "error", "ok"]
    assert rows[0]["error_class"] == "other"


def test_stream_truncation_is_not_retried(tmp_path):
    provider = _StreamingProvider(
        None, error=StreamTruncatedError("上游流式响应被截断")
    )
    engine = TurnEngine(
        provider=provider,
        registry=_engine_registry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="m",
        max_retries=3,
    )
    events = _run(engine)
    assert provider.calls == 1  # never retried
    assert events[-1].type.name == "ERROR"


class _TTFTStallProvider(_StreamingProvider):
    """Never delivers a first token within a tiny window — the TTFT guard fires."""

    def stream(self, *, model, messages, tools=None, **settings):
        import time

        time.sleep(0.5)  # longer than the engine's TTFT ceiling
        yield StreamChunk(turn=AssistantTurn(text="late", finish_reason="stop"))


def test_ttft_timeout_is_retried(tmp_path):
    provider = _TTFTStallProvider(AssistantTurn(text="ok", finish_reason="stop"))
    engine = TurnEngine(
        provider=provider,
        registry=_engine_registry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="m",
        max_retries=1,
        ttft_timeout=0.05,
    )
    rows = []
    engine.request_logger = rows.append
    events = _run(engine)
    # Every attempt before the (missing) first token logs as ttft_timeout; the final
    # surfaced error is also classified ttft_timeout.
    assert rows[0]["error_class"] == "ttft_timeout"
    assert rows[-1]["error_class"] == "ttft_timeout"


# -- endpoint param negotiation ------------------------------------------------


def test_apply_endpoint_caps_negotiates_params():
    from providers.endpoint import EndpointCaps

    kwargs = {"tools": [{"type": "function", "function": {"name": "f"}}]}
    caps = EndpointCaps(stream_options=True, parallel_tool_calls=False, reasoning_content=False)
    _apply_endpoint_caps(kwargs, caps, has_tools=True)
    assert "stream_options" in kwargs
    assert kwargs["parallel_tool_calls"] is False
    assert "reasoning_effort" not in kwargs

    kwargs2 = {"tools": []}
    _apply_endpoint_caps(kwargs2, EndpointCaps(), has_tools=False)
    assert "parallel_tool_calls" not in kwargs2  # no tools, no negotiation


def test_param_fix_retry_drops_parallel_and_learns():
    import types

    class Fake:
        def __init__(self):
            self.calls = []

        def create(self, **kw):
            self.calls.append(kw)
            if "parallel_tool_calls" in kw:
                raise RuntimeError("'parallel_tool_calls' is not supported by this server")
            return [
                types.SimpleNamespace(
                    usage=None,
                    choices=[types.SimpleNamespace(delta=None, finish_reason="stop")],
                )
            ]

        @property
        def chat(self):
            return types.SimpleNamespace(completions=self)

    import tempfile
    from pathlib import Path
    from providers.endpoint import from_profile, learned_caps

    tmp = Path(tempfile.mkdtemp()) / "state"
    import os

    os.environ["DELTA_STATE_DIR"] = str(tmp)
    key = "http://gw:8000/v1"
    fake = Fake()
    # Profile declares the endpoint does NOT support parallel calls → proactive
    # negotiation sends parallel_tool_calls=False; the server still rejects the field
    # entirely, so it's dropped and the rejection is learned.
    p = OpenAIProvider(
        client=fake,
        api_key="k",
        base_url=key,
        endpoint_key=key,
        endpoint_caps=from_profile({"parallel_tool_calls": False}),
    )
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    list(p.stream(model="m", messages=[{"role": "user", "content": "x"}], tools=tools))
    assert "parallel_tool_calls" in fake.calls[0]
    assert fake.calls[0]["parallel_tool_calls"] is False
    assert "parallel_tool_calls" not in fake.calls[1]
    assert learned_caps(key)["parallel_tool_calls"] is False


# -- provider health profile ---------------------------------------------------


def test_health_records_and_aggregates(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    _health.record_call("gw1", "model-x", ok=True, ttft_ms=10, duration_ms=50)
    _health.record_call("gw1", "model-x", ok=False, ttft_ms=5, duration_ms=1000)
    _health.record_call("gw1", "model-x", ok=True, ttft_ms=20, duration_ms=60)
    p = _health.profile("gw1", "model-x")
    assert p.samples == 3
    assert p.errors == 1
    assert p.success_rate == 2 / 3
    assert p.avg_ttft_ms == (10 + 5 + 20) / 3
    assert p.last_error_class is None  # last call was ok
    assert not p.degraded


def test_health_degraded_and_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    for _ in range(8):
        _health.record_call("gw1", "m", ok=False, error_class="ttft_timeout")
    assert _health.profile("gw1", "m").degraded
    # Healthy/unseen candidates rank ahead of the degraded one.
    ordered = _health.route_healthy([("gw1", "m"), ("gw2", "m")])
    assert ordered[0] == ("gw2", "m")


def test_health_file_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    _health.record_call("gw1", "m", ok=True)
    store = json.loads((tmp_path / "state" / "provider_health.json").read_text())
    assert store["gw1"]["m"]["samples"] == 1
