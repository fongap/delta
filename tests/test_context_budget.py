"""Context budget (v0.3.0 P0): tool schemas count toward the compaction trigger, and
trimming the injected toolset is tried BEFORE the summarizer."""

from __future__ import annotations

import asyncio

from core.compaction import estimate_tools_tokens
from core.engine import TurnEngine
from core.events import EventType
from core.permissions import PermissionEngine
from integrations.tools import ToolRegistry
from providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk


class StreamingProvider(ProviderClient):
    def __init__(self, turn):
        self._turn = turn
        self.tools = None

    def complete(self, *, model, messages, tools=None, **settings):
        self.tools = tools
        return self._turn

    def stream(self, *, model, messages, tools=None, **settings):
        self.tools = tools
        yield StreamChunk(turn=self._turn)

    def capabilities(self, model):
        return ModelCapabilities()


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    def ask_user(question: str) -> dict:
        """Ask the user a question.
        Args:
            question (str): The question.
        """
        return {}

    def web_search(query: str) -> dict:
        """Search the web for facts.
        Args:
            query (str): The query.
        """
        return {}

    def run_shell(command: str) -> dict:
        """Run a shell command in the persistent session.
        Args:
            command (str): The command line to run.
        """
        return {}

    registry.register_all([ask_user, web_search, run_shell])
    return registry


def _engine(tmp_path, provider, window: int) -> TurnEngine:
    engine = TurnEngine(
        provider=provider,
        registry=_registry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="test-model",
    )
    engine.compaction_settings = lambda: {
        "enabled": True,
        "context_window": window,
        "threshold_pct": 0.8,
        "cap_tokens": 1_000_000,
    }
    return engine


def _run(engine, text):
    async def go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(go())


def test_tool_schemas_count_toward_trigger(tmp_path):
    engine = _engine(tmp_path, StreamingProvider(AssistantTurn(text="ok")), window=200)
    assert engine._compaction_due(0) is False  # empty history alone: no trigger
    # A payload of tool schemas alone can trip the trigger on a small window.
    assert engine._compaction_due(estimate_tools_tokens(engine.registry.schemas()))


def test_trim_tools_before_compaction(tmp_path):
    """History + tool schemas breach the trigger; core-only schemas would fit → the
    toolset is trimmed (COMPACTED notice) and the model call carries only the core tool.
    """
    provider = StreamingProvider(AssistantTurn(text="ok", finish_reason="stop"))
    engine = _engine(tmp_path, provider, window=1900)  # trigger = 1520
    # ~1382 estimate tokens AND signals web+shell+files so the selection starts wide.
    text = "search the web, run a command, and save a report. " * 110
    events = _run(engine, text)
    trimmed = [e for e in events if e.type == EventType.COMPACTED]
    assert trimmed and "trimmed the injected toolset" in trimmed[0].data["text"]
    # The model call went out with the core tool only.
    names = [t["function"]["name"] for t in provider.tools or []]
    assert names == ["ask_user"]
    assert engine._tools_minimal is True


def test_full_compaction_still_runs_when_tools_cannot_save_it(tmp_path):
    provider = StreamingProvider(AssistantTurn(text="ok", finish_reason="stop"))
    engine = _engine(tmp_path, provider, window=1000)  # trigger = 800: core-only (916) overflows
    engine.question_asker = None
    engine.is_attended = lambda: False
    text = "search the web, run a command, and save a report. " * 110
    events = _run(engine, text)
    trimmed = [
        e
        for e in events
        if e.type == EventType.COMPACTED and "trimmed" in str(e.data.get("text", ""))
    ]
    assert trimmed == []  # too big even for core-only — no tool trim fired
    assert any(e.type == EventType.COMPACTING for e in events)
