"""Agent registry — resolves a persona id to its runtime Agent.

Delegates to the persona registry (``core.personas``) so built-in surfaces and
markdown/third-party personas resolve through one path. MyHelper is a legacy personal-helper
persona resolved directly (kept for sessions that still reference it).
Imports of the persona registry are lazy to avoid an import cycle (personas → agents builders).
"""

from __future__ import annotations

from core.agents.base import Agent
from core.agents.myhelper import myhelper_agent


def get_agent(name: str) -> Agent:
    name = name or "code"
    if name == "myhelper":
        return myhelper_agent()
    from core.personas.registry import get_registry

    return get_registry().agent(name)


def list_agents() -> list[dict]:
    # Session surfaces shown in the new-session picker (enabled + surfaced personas).
    from core.personas.registry import get_registry

    return get_registry().sidebar()
