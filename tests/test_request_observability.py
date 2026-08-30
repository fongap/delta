"""Request observability (v0.3.0 P0): one JSONL row per model call, on every outcome."""

from __future__ import annotations

import asyncio
import json

from core.engine import TurnEngine
from core.events import EventType
from core.permissions import PermissionEngine
from core.request_log import make_logger
from integrations.tools import ToolRegistry
from providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk


class StreamingProvider(ProviderClient):
    """Streams a final turn; captures the tools kwarg so tests can assert injection."""

    def __init__(self, turn, *, error=None):
        self._turn = turn
        self._error = error
        self.tools = None

    def complete(self, *, model, messages, tools=None, **settings):
        self.tools = tools
        if self._error:
            raise self._error
        return self._turn

    def stream(self, *, model, messages, tools=None, **settings):
        self.tools = tools
        if self._error:
            raise self._error
        yield StreamChunk(turn=self._turn)

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider, **kwargs):
    engine = TurnEngine(
        provider=provider,
        registry=kwargs.pop("registry", None) or ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="test-model",
        **kwargs,
    )
    rows: list[dict] = []
    engine.request_logger = rows.append
    return engine, rows


def _run(engine, text="hi"):
    async def go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(go())


def test_success_call_is_logged(tmp_path):
    turn = AssistantTurn(text="ok", finish_reason="stop")
    provider = StreamingProvider(turn)
    engine, rows = _engine(tmp_path, provider)
    _run(engine, "帮我总结这段文字")
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "test-model"
    assert row["outcome"] == "ok"
    assert row["provider"] == "StreamingProvider"
    assert row["messages_count"] >= 1  # system-less engine: just the user turn
    assert row["body_bytes"] > 0
    assert row["ttft_ms"] is not None
    assert row["duration_ms"] >= 0
    assert isinstance(row["tool_names"], list)


def test_provider_error_is_logged_with_type(tmp_path):
    provider = StreamingProvider(None, error=RuntimeError("boom"))
    engine, rows = _engine(tmp_path, provider)
    events = _run(engine)
    assert events[-1].type == EventType.ERROR
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["error_type"] == "RuntimeError"


def test_tool_count_matches_injection(tmp_path):
    """auto mode on a tool-free chat turn must NOT log the full toolbox."""
    registry = ToolRegistry()

    def web_search(query: str) -> dict:
        """Search the web.
        Args:
            query (str): The query.
        """
        return {}

    def run_shell(command: str) -> dict:
        """Run a shell command.
        Args:
            command (str): The command.
        """
        return {}

    def ask_user(question: str) -> dict:
        """Ask the user.
        Args:
            question (str): The question.
        """
        return {}

    registry.register_all([web_search, run_shell, ask_user])
    provider = StreamingProvider(AssistantTurn(text="ok", finish_reason="stop"))
    engine, rows = _engine(tmp_path, provider, registry=registry)
    _run(engine, "帮我总结这段文字")
    assert rows[0]["tool_mode"] == "selected"
    assert rows[0]["tools_count"] == 1  # ask_user only — no web, no shell
    assert "run_shell" not in rows[0]["tool_names"]

    # "full" policy restores everything.
    engine2, rows2 = _engine(
        tmp_path,
        StreamingProvider(AssistantTurn(text="ok", finish_reason="stop")),
        registry=registry,
        tool_selection="full",
    )
    _run(engine2)
    assert rows2[0]["tool_mode"] == "full"
    assert rows2[0]["tools_count"] == 3


def test_jsonl_sink_writes_and_survives_bad_rows(tmp_path):
    log = make_logger(tmp_path / "nested" / "request_log.jsonl")
    log({"ts": 1, "outcome": "ok"})
    log({"ts": 2, "outcome": "error"})  # non-serializable values must not raise
    log({"ts": 3, "bad": object()})
    lines = (tmp_path / "nested" / "request_log.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["outcome"] == "ok"
