"""Phase 0 gate — the vetted tool catalog.

Asserts capabilities register, ``expand`` reproduces the Code and Cowork toolsets exactly
(the equivalence net for the build_tools refactor), context prerequisites are honored
(no shell without an executor, no files without a workspace), and the Code/Cowork file-tool
distinction (single-root numbered reader vs multi-root) is preserved."""

from __future__ import annotations

import pytest

from core.agents.base import AgentContext
from core.agents.code import CODE_CAPABILITIES, code_agent
from core.agents.cowork import COWORK_CAPABILITIES, cowork_agent
from core.catalog import CATALOG, capability, expand, risk_summary
from core.risk import RiskClass
from integrations.tools.todo import TodoList

# Expected toolset for each surface — the frozen equivalence contract for the refactor.
# `read_file` and `read_file_lines` are both ours in v0.3.1: same multi-root path
# resolver, same Source/Citation chokepoint, only the default window differs.
CODE_TOOLS = {
    "list_files",
    "write_file",
    "apply_unified_diff",
    "apply_patch",
    "replace_in_file",
    "read_file",  # ours: numbered/windowed (single-root) with cite hook
    "read_file_lines",  # ours: small-window sibling of read_file with cite hook
    "read_document",  # P2 实用: PDF / XLSX / DOCX with typed citations
    "git_status",
    "git_diff",
    "git_log",
    "grep",
    "run_shell",
    "shell_task_output",
    "shell_task_kill",
    "todo_write",
}
COWORK_TOOLS = {
    "list_files",
    "read_file",  # ours: cite-aware multi-root
    "read_file_lines",  # ours: cite-aware multi-root
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
    assert {"code_files", "files", "git", "search", "shell", "todo"} <= set(CATALOG)
    for cap in CATALOG.values():
        assert cap.id and cap.name and callable(cap.build)


def test_expand_code_matches_expected(tmp_path):
    tools = expand(CODE_CAPABILITIES, _full_context(tmp_path))
    assert _names(tools) == CODE_TOOLS


def test_expand_cowork_matches_expected(tmp_path):
    tools = expand(COWORK_CAPABILITIES, _full_context(tmp_path))
    assert _names(tools) == COWORK_TOOLS


def test_agents_use_catalog(tmp_path):
    # The agent factories build through the catalog now — same result as direct expand.
    ctx = _full_context(tmp_path)
    assert _names(code_agent().build_tools(ctx)) == CODE_TOOLS
    assert _names(cowork_agent().build_tools(ctx)) == COWORK_TOOLS


def test_file_capability_distinction(tmp_path):
    # Both Code and Cowork get our `read_file` AND our `read_file_lines` (v0.3.1:
    # `read_file_lines` gained a Source/Citation hook and was promoted from a
    # Cowork-only windowed reader to a shared chokepoint). The remaining
    # distinction is what aisuite provides: Code has single-root + read_document,
    # Cowork has multi-root roots-aware writes.
    code = _names(expand(["code_files"], _full_context(tmp_path)))
    cowork = _names(expand(["files"], _full_context(tmp_path)))
    assert "read_file_lines" in code
    assert "read_file_lines" in cowork
    assert "read_file" in code and "read_file" in cowork
    # Code has read_document; Cowork does not.
    assert "read_document" in code
    assert "read_document" not in cowork


def test_requirements_skip_unavailable(tmp_path):
    # No executor → no shell; no todo → no todo_write; no workspace → no files/git/search.
    no_exec = AgentContext(workspace=tmp_path, executor=None, todo=TodoList())
    assert "run_shell" not in _names(expand(CODE_CAPABILITIES, no_exec))
    assert "todo_write" in _names(expand(CODE_CAPABILITIES, no_exec))

    no_todo = AgentContext(workspace=tmp_path, executor=object(), todo=None)
    assert "todo_write" not in _names(expand(CODE_CAPABILITIES, no_todo))
    assert "run_shell" in _names(expand(CODE_CAPABILITIES, no_todo))

    no_ws = AgentContext(workspace=None, executor=object(), todo=TodoList())
    names = _names(expand(CODE_CAPABILITIES, no_ws))
    assert names == {"run_shell", "shell_task_output", "shell_task_kill", "todo_write"}


def test_risk_summary():
    assert risk_summary(["shell"]) == {RiskClass.EXEC}
    assert risk_summary(["code_files"]) == {RiskClass.READ, RiskClass.WRITE_LOCAL}
    assert risk_summary(["git", "search"]) == {RiskClass.READ}


def test_unknown_capability_raises():
    with pytest.raises(KeyError):
        capability("does_not_exist")
