"""Agent registry — resolves an agent id to its runtime Agent.

Delta is the single product agent. There is exactly one registered agent (``delta``);
any missing, empty, or unknown id (including ids from historical sessions) resolves to
Delta through the persona registry's default fallback. Imports of the persona registry
are lazy to avoid an import cycle (personas → agents builders).
"""

from __future__ import annotations

from core.agents.base import Agent

DEFAULT_AGENT_ID = "delta"


def get_agent(name: str) -> Agent:
    from core.personas.registry import get_registry

    return get_registry().agent(name or DEFAULT_AGENT_ID)


def list_agents() -> list[dict]:
    # Session surfaces shown in the new-session picker (enabled + surfaced personas).
    from core.personas.registry import get_registry

    return get_registry().sidebar()
