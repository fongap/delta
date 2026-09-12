"""Vetted tool catalog — the stable ``id → capability`` layer the Delta agent references.

A *capability* bundles a group of tools (the existing ``tools/`` factories) behind a stable
id, plus what session context it needs (``requires``) and the risk classes it can produce
(``risk``). ``expand(ids, context)`` turns a capability id list into concrete callables,
skipping capabilities whose context prerequisites aren't met (e.g. no shell without an
executor).

The catalog is **platform-owned and closed**: breadth comes from vetted capabilities here
and from MCP, never from third-party entries. Coding-specific capabilities (repo-centric
file editing, git) were removed in R6.0 — generic file editing lives in ``files`` and
script execution in ``shell``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import aisuite as ai

from core.agents.base import AgentContext
from core.risk import RiskClass
from integrations.tools.documents import document_tools
from integrations.tools.files import file_tools
from integrations.tools.search import search_tools
from integrations.tools.shell import shell_tools
from integrations.tools.todo import todo_tools

# Context prerequisites a capability may require, mapped to a predicate over AgentContext.
_REQUIREMENTS: dict[str, Callable[[AgentContext], bool]] = {
    "workspace": lambda c: c.workspace is not None,
    "executor": lambda c: c.executor is not None,
    "todo": lambda c: c.todo is not None,
}


@dataclass(frozen=True)
class Capability:
    id: str
    name: str  # human label
    description: str
    build: Callable[[AgentContext], list]
    requires: tuple[str, ...] = ()
    risk: tuple[RiskClass, ...] = (RiskClass.READ,)

    def available(self, context: AgentContext) -> bool:
        return all(_REQUIREMENTS[r](context) for r in self.requires)


# -- capability builders --------------------------------------------------------


def _files(context: AgentContext) -> list:
    """Knowledge-work files: multi-root aware (reads/writes across the session's roots).
    Includes the generic editors — write_file, apply_patch, apply_unified_diff,
    replace_in_file, list_files — plus our cite-aware multi-root ``read_file`` /
    ``read_file_lines`` and ``grep`` (our readers replace aisuite's slower ones).
    """
    ws = str(context.workspace)
    toolkit = (
        ai.toolkits.files(roots=context.roots)
        if context.roots
        else ai.toolkits.files(root=ws, allow_write=True)
    )
    # Drop aisuite's read_file + read_file_lines (replaced by our cite-aware
    # multi-root versions) and search_files (replaced by our grep).
    replaced = {"search_files", "read_file", "read_file_lines"}
    files = [
        t
        for t in toolkit
        if getattr(t, "__name__", "") not in replaced
    ]
    return [
        *files,
        *file_tools(
            ws,
            source_store=context.source_store,
            run_id=context.run_id,
            roots=context.roots,
        ),
        # read_document (moved from the retired code_files capability): PDF / XLSX /
        # DOCX reading with typed citations. Office work needs document reading, so this
        # generic capability lives in `files` now, not on a retired coding surface.
        *document_tools(
            ws,
            source_store=context.source_store,
            run_id=context.run_id,
        ),
    ]


def _search(context: AgentContext) -> list:
    return search_tools(str(context.workspace))  # grep (ripgrep, .gitignore-aware)


def _shell(context: AgentContext) -> list:
    assert context.executor is not None
    return shell_tools(context.executor)  # run_shell + background task tools


def _todo(context: AgentContext) -> list:
    assert context.todo is not None
    return todo_tools(context.todo)  # todo_write (drives the Progress panel)


_CAPS: list[Capability] = [
    Capability(
        id="files",
        name="Files",
        description="Read & edit files across the session's workspace folders.",
        build=_files,
        requires=("workspace",),
        risk=(RiskClass.READ, RiskClass.WRITE_LOCAL),
    ),
    Capability(
        id="search",
        name="Search",
        description="Fast content search (grep).",
        build=_search,
        requires=("workspace",),
        risk=(RiskClass.READ,),
    ),
    Capability(
        id="shell",
        name="Shell",
        description="Run shell commands and scripts in a persistent session.",
        build=_shell,
        requires=("executor",),
        risk=(RiskClass.EXEC,),
    ),
    Capability(
        id="todo",
        name="Task list",
        description="Maintain a visible task/progress list.",
        build=_todo,
        requires=("todo",),
        risk=(RiskClass.READ,),
    ),
]

CATALOG: dict[str, Capability] = {c.id: c for c in _CAPS}


def capability(cap_id: str) -> Capability:
    cap = CATALOG.get(cap_id)
    if cap is None:
        raise KeyError(f"Unknown capability id: {cap_id!r}")
    return cap


def expand(ids: list[str], context: AgentContext) -> list:
    """Expand a ``tools:`` id list into concrete tool callables for this context.
    Capabilities whose context prerequisites aren't met are skipped (no shell without an
    executor, no files without a workspace)."""
    tools: list = []
    for cap_id in ids:
        cap = capability(cap_id)
        if cap.available(context):
            tools.extend(cap.build(context))
    return tools


def risk_summary(ids: list[str]) -> set[RiskClass]:
    """The union of risk classes a tool list can produce."""
    out: set[RiskClass] = set()
    for cap_id in ids:
        out.update(capability(cap_id).risk)
    return out

