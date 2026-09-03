"""Agent — a top-level surface (Code / Chat / Cowork).

An agent owns its system prompt + base toolset + whether it needs a workspace. Distinct
from a Skill: skills are Anthropic-format, loadable capabilities that ANY agent can pull
in (see integrations.skills).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from integrations.tools.todo import TodoList

if TYPE_CHECKING:
    from core.sources import SourceStore


@dataclass
class AgentContext:
    workspace: Path | None = None
    executor: Any | None = None
    todo: TodoList | None = None
    # Shared, mutable list of RootDir the session may touch (primary scratch + added folders).
    # When None, tools fall back to the single `workspace` root. Held by reference so runtime
    # add/remove of folders is seen by the file tools built from it.
    roots: list | None = None
    # P2 实用 (DELTA_BLUEPRINT §7.2): the run's source ledger. The capability
    # catalog threads this into file/connector readers so every successful
    # read auto-cites the run with a typed locator (lines / page / cells /
    # message_id / custom). None disables the audit hook.
    source_store: "SourceStore | None" = None
    # P2 实用: the active run id (ADR-005 G1 — one identity across TaskStore,
    # ledger, artifact, validation, idemlog). Threaded into readers as the
    # ``run_id`` for citation appends. None disables the audit hook.
    run_id: str | None = None


@dataclass
class Agent:
    name: str
    title: str
    system_prompt: str
    needs_workspace: bool = False
    tool_factory: Callable[[AgentContext], list] | None = None
    # Traits that replace the old per-agent-name branching in build_engine / manager.
    # family: "code" gets explorer subagents; "knowledge" gets scheduling / request_directory /
    # roots context (when it has a workspace). messaging: exposes send_message. connectors:
    # loads the integration toolset. Defaults keep non-persona callers behaving as before.
    family: str = "knowledge"
    messaging: bool = False
    connectors: bool = False

    def build_tools(self, context: AgentContext) -> list:
        return list(self.tool_factory(context)) if self.tool_factory else []
