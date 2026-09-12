"""Phase 0 gate — the vetted tool catalog.

Asserts the four capabilities (files/search/shell/todo) register, ``expand`` reproduces
Delta's toolset exactly, context prerequisites are honored (no shell without an executor,
no files without a workspace), and the generic file capability carries the editors +
document reader (moved out of the retired code_files capability in R6.0)."""

from __future__ import annotations

import pytest

from core.agents.base import AgentContext
from core.agents.delta_agent import DELTA_CAPABILITIES, delta_agent
from core.catalog import CATALOG, capability, expand, risk_summary
from core.risk import RiskClass
from integrations.tools.todo import TodoList

# Delta's toolset — the frozen equivalence contract. read_document moved here from the
# retired code_files capability; git_* tools are gone (no coding surface).
DELTA_TOOLS = {
    "list_files",
    "read_file",  # ours: cite-aware multi-root
    "read_file_lines",  # ours: cite-aware multi-root
    "read_document",  # moved from code_files: PDF / XLSX / DOCX with typed citations
    "write_file",
    "apply_unified_diff",
    "apply_patch",
    "replace_in_file",
    "grep",
    "run_shell",
    "shell_task_output",
    "shell_task_kill",
    "todo_write",
}


def _names(tools) -> set:
    return {getattr(t, "__name__", "") for t in tools}


def _full_context(tmp_path) -> AgentContext:
    return AgentContext(workspace=tmp_path, executor=object(), todo=TodoList())


def test_catalog_registers_expected_ids():
    assert {"files", "search", "shell", "todo"} <= set(CATALOG)
    # R6.0: code_files and git were removed from the product surface.
    assert "code_files" not in CATALOG
    assert "git" not in CATALOG
    for cap in CATALOG.values():
        assert cap.id and cap.name and callable(cap.build)


def test_expand_delta_matches_expected(tmp_path):
    tools = expand(DELTA_CAPABILITIES, _full_context(tmp_path))
    assert _names(tools) == DELTA_TOOLS


def test_agent_uses_catalog(tmp_path):
    ctx = _full_context(tmp_path)
    assert _names(delta_agent().build_tools(ctx)) == DELTA_TOOLS


def test_files_capability_carries_editors_and_document_reader(tmp_path):
    # The generic file capability holds the editors (write_file, replace_in_file,
    # apply_patch, apply_unified_diff, list_files) and the document reader that used to
    # live on the retired code_files surface — so removing code_files lost no capability.
    names = _names(expand(["files"], _full_context(tmp_path)))
    for n in (
        "read_file",
        "read_file_lines",
        "read_document",
        "write_file",
        "replace_in_file",
        "apply_patch",
        "apply_unified_diff",
        "list_files",
    ):
        assert n in names, n
    # No git tools leak into the generic file surface.
    assert not {"git_status", "git_diff", "git_log"} & names


def test_requirements_skip_unavailable(tmp_path):
    no_exec = AgentContext(workspace=tmp_path, executor=None, todo=TodoList())
    assert "run_shell" not in _names(expand(DELTA_CAPABILITIES, no_exec))
    assert "todo_write" in _names(expand(DELTA_CAPABILITIES, no_exec))

    no_todo = AgentContext(workspace=tmp_path, executor=object(), todo=None)
    assert "todo_write" not in _names(expand(DELTA_CAPABILITIES, no_todo))
    assert "run_shell" in _names(expand(DELTA_CAPABILITIES, no_todo))

    no_ws = AgentContext(workspace=None, executor=object(), todo=TodoList())
    names = _names(expand(DELTA_CAPABILITIES, no_ws))
    assert names == {"run_shell", "shell_task_output", "shell_task_kill", "todo_write"}


def test_risk_summary():
    assert risk_summary(["shell"]) == {RiskClass.EXEC}
    assert risk_summary(["files"]) == {RiskClass.READ, RiskClass.WRITE_LOCAL}
    assert risk_summary(["search"]) == {RiskClass.READ}


def test_unknown_capability_raises():
    with pytest.raises(KeyError):
        capability("does_not_exist")
