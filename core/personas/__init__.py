"""Personas — Delta as the single registered agent.

A persona was historically a manifest (YAML frontmatter + a markdown body) that composed
vetted catalog capabilities. The product has converged on a single agent (Delta) extended
through Skills / Capabilities, so the registry now registers exactly one built-in persona.
"""

from __future__ import annotations

from core.personas.registry import DEFAULT_PERSONA_ID, PersonaRegistry, PersonaState

__all__ = [
    "DEFAULT_PERSONA_ID",
    "PersonaRegistry",
    "PersonaState",
]
