"""Persona registry — the registered agent + its lifecycle state.

Delta is the single product agent; the registry exists to resolve an agent id to its
runtime ``Agent`` and to answer the new-session picker's surface list. Any unknown,
missing, or legacy id (e.g. a historical ``code``/``chat`` session) falls back to the
default persona — Delta — so live sessions keep working even though those ids are no
longer registered as products.

The internal id is the routing key persisted in ``SessionRecord.agent`` and
``TaskRun.agent``; the user-facing label remains ``Delta``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.agents.base import Agent
from core.agents.delta_agent import DELTA_CAPABILITIES, delta_agent

DEFAULT_PERSONA_ID = "delta"


@dataclass
class PersonaState:
    enabled: bool = True
    surfaced: bool = True


@dataclass
class PersonaEntry:
    id: str
    name: str
    icon: str = ""
    tagline: str = ""
    needs_workspace: bool = True
    builtin: bool = True
    family: str = "knowledge"
    # The persona's workspace requirement (project|deliverable|none) — surfaced to the GUI.
    workspace: str = "deliverable"
    tools: list[str] = field(default_factory=list)
    default_surfaced: bool = (
        True  # whether it shows in the picker before any user choice
    )
    _builder: Callable[[], Agent] | None = None
    # Legacy manifest hook: only built-in Delta is registered now, so this is always None.
    # Kept so the connections UI's `entry.manifest` reads stay None-safe.
    manifest: Any = None

    def agent(self) -> Agent:
        assert self._builder is not None
        return self._builder()


class PersonaRegistry:
    def __init__(self, *, state_path: str | Path | None = None) -> None:
        self.state_path = Path(state_path) if state_path else None
        self._entries: dict[str, PersonaEntry] = {}
        self._enabled: dict[str, bool] = {}
        self._surfaced: dict[str, bool] = {}
        self._default = DEFAULT_PERSONA_ID
        self._register_builder(
            "delta",
            "Delta",
            "delta",
            "Produce a deliverable — office work, research, content, scripts",
            delta_agent,
            True,
            "knowledge",
            DELTA_CAPABILITIES,
            workspace="deliverable",
        )
        self._load_state()

    # -- loading ----------------------------------------------------------------
    def _register_builder(
        self,
        id,
        name,
        icon,
        tagline,
        builder,
        needs_workspace,
        family,
        tools,
        workspace="deliverable",
        default_surfaced=True,
    ) -> None:
        self._entries[id] = PersonaEntry(
            id=id,
            name=name,
            icon=icon,
            tagline=tagline,
            needs_workspace=needs_workspace,
            builtin=True,
            family=family,
            workspace=workspace,
            tools=list(tools),
            default_surfaced=default_surfaced,
            _builder=builder,
        )

    def _load_state(self) -> None:
        if self.state_path and self.state_path.is_file():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._enabled = dict(data.get("enabled", {}))
            self._surfaced = dict(data.get("surfaced", {}))
            self._default = data.get("default", DEFAULT_PERSONA_ID)
            # A persisted default from a removed persona (e.g. an old "code") must not
            # stick — fall back to Delta.
            if self._default not in self._entries:
                self._default = DEFAULT_PERSONA_ID

    def save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "enabled": self._enabled,
                    "surfaced": self._surfaced,
                    "default": self._default,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- queries ----------------------------------------------------------------
    def ids(self) -> list[str]:
        return list(self._entries)

    def get(self, persona_id: str) -> PersonaEntry | None:
        return self._entries.get(persona_id)

    def is_enabled(self, persona_id: str) -> bool:
        if persona_id in self._enabled:
            return bool(self._enabled[persona_id])
        return persona_id == self._default or persona_id == DEFAULT_PERSONA_ID

    def is_surfaced(self, persona_id: str) -> bool:
        if persona_id in self._surfaced:
            return self._surfaced[persona_id]
        entry = self._entries.get(persona_id)
        return entry.default_surfaced if entry else True

    def default_id(self) -> str:
        # The configured default if it's enabled, else Delta.
        if self._default in self._entries and self.is_enabled(self._default):
            return self._default
        return DEFAULT_PERSONA_ID

    def agent(self, persona_id: str | None) -> Agent:
        """Resolve a persona id to its Agent. Unknown ids fall back to the default persona
        (Delta), so historical sessions keep working even after their persona is retired."""
        entry = self._entries.get(persona_id or "")
        if entry is None:
            entry = self._entries.get(self.default_id())
        if entry is None:
            raise KeyError(f"no persona to resolve for {persona_id!r}")
        return entry.agent()

    def sidebar(self) -> list[dict]:
        """Session surfaces for the new-session picker: enabled AND surfaced, in order."""
        out = []
        for e in self._entries.values():
            if self.is_enabled(e.id) and self.is_surfaced(e.id):
                out.append(
                    {
                        "name": e.id,
                        "title": e.name,
                        "needs_workspace": e.needs_workspace,
                        "icon": e.icon,
                        "tagline": e.tagline,
                        "default": e.id == self.default_id(),
                    }
                )
        return out

    def list_all(self) -> list[dict]:
        """Every registered persona + its lifecycle state — for the Personas settings panel."""
        return [
            {
                "id": e.id,
                "name": e.name,
                "icon": e.icon,
                "tagline": e.tagline,
                "needs_workspace": e.needs_workspace,
                "builtin": e.builtin,
                "family": e.family,
                "workspace": e.workspace,
                "tools": e.tools,
                "enabled": self.is_enabled(e.id),
                "surfaced": self.is_surfaced(e.id),
                "default": e.id == self.default_id(),
            }
            for e in self._entries.values()
        ]

    # -- mutations --------------------------------------------------------------
    def set_enabled(self, persona_id: str, enabled: bool) -> None:
        if persona_id not in self._entries:
            raise KeyError(persona_id)
        self._enabled[persona_id] = bool(enabled)
        if enabled:
            self._surfaced[persona_id] = True
        self.save()

    def set_surfaced(self, persona_id: str, surfaced: bool) -> None:
        if persona_id not in self._entries:
            raise KeyError(persona_id)
        self._surfaced[persona_id] = bool(surfaced)
        self.save()

    def set_default(self, persona_id: str) -> None:
        if persona_id not in self._entries:
            raise KeyError(persona_id)
        self._default = persona_id
        self._enabled[persona_id] = True  # a default must be enabled
        self.save()

    def uninstall(self, persona_id: str) -> None:
        """Remove a persona: registry entry + lifecycle state. Only Delta is registered and it
        is built-in, so this always raises — kept as the stable API surface the manager calls."""
        entry = self._entries.get(persona_id)
        if entry is None:
            raise KeyError(persona_id)
        raise ValueError(f"{persona_id} is built-in and cannot be deleted")


# -- module singleton (used by agents.get_agent / list_agents) ------------------
_singleton: PersonaRegistry | None = None


def get_registry() -> PersonaRegistry:
    global _singleton
    if _singleton is None:
        from packages.secrets import state_dir

        _singleton = PersonaRegistry(state_path=state_dir() / "personas.json")
    return _singleton


def set_registry(registry: PersonaRegistry) -> None:
    """Install a registry as the process singleton (the manager does this with its data dir)."""
    global _singleton
    _singleton = registry
