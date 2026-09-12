"""Phase 1 gate — Delta is the single registered persona.

The persona registry resolves Delta to the same toolset its builder produces, and any
unknown / legacy id (e.g. a historical ``code`` session) falls back to Delta so live
sessions keep working even though those personas are no longer registered products."""

from __future__ import annotations

from core.agents.base import AgentContext
from core.agents.delta_agent import delta_agent
from core.personas.registry import DEFAULT_PERSONA_ID, PersonaRegistry
from integrations.tools.todo import TodoList


def _ctx(tmp_path) -> AgentContext:
    return AgentContext(workspace=tmp_path, executor=object(), todo=TodoList())


def _names(agent, ctx) -> set:
    return {getattr(t, "__name__", "") for t in agent.build_tools(ctx)}


def test_delta_is_registered_default(tmp_path):
    reg = PersonaRegistry()
    assert DEFAULT_PERSONA_ID == "delta"
    assert reg.default_id() == "delta"
    ctx = _ctx(tmp_path)
    assert _names(reg.agent("delta"), ctx) == _names(delta_agent(), ctx)
    a = reg.agent("delta")
    assert a.messaging and a.connectors


def test_unknown_id_falls_back_to_delta(tmp_path):
    reg = PersonaRegistry()
    ctx = _ctx(tmp_path)
    # Historical "code" is no longer registered — it must resolve to Delta, not 404.
    assert _names(reg.agent("code"), ctx) == _names(delta_agent(), ctx)
    assert _names(reg.agent("chat"), ctx) == _names(delta_agent(), ctx)
    assert _names(reg.agent("ops"), ctx) == _names(delta_agent(), ctx)
    assert _names(reg.agent("myhelper"), ctx) == _names(delta_agent(), ctx)
    assert _names(reg.agent("never-existed"), ctx) == _names(delta_agent(), ctx)
    assert _names(reg.agent(None), ctx) == _names(delta_agent(), ctx)


def test_sidebar_only_delta(tmp_path):
    reg = PersonaRegistry()
    sidebar = reg.sidebar()
    assert len(sidebar) == 1
    assert sidebar[0]["name"] == "delta"


def test_delta_keeps_generic_file_tools(tmp_path):
    reg = PersonaRegistry()
    names = _names(reg.agent("delta"), _ctx(tmp_path))
    for n in ("read_file", "read_file_lines", "read_document", "write_file", "run_shell"):
        assert n in names, n
    # No coding/git surface.
    assert not {"git_status", "git_diff", "git_log"} & names
