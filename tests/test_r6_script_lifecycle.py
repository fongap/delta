"""R6.0 scripting regression — the script lifecycle must survive the product cleanup.

Removing the Code product must never remove the ability to generate, inspect, modify,
execute, repair, and re-run a task script. These tests exercise the real Delta tools
(`write_file`, `replace_in_file`, `run_shell`) against a real local executor — no LLM —
to pin the contract: task → script → result → artifact, with stderr available on failure
so the script can be repaired and re-run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.agents.base import AgentContext
from core.agents.delta_agent import delta_agent
from integrations.tools import ToolRegistry
from integrations.tools.shell import LocalExecutor
from integrations.tools.todo import TodoList

_WIN = sys.platform == "win32"


def _toolset(workspace: Path) -> tuple[ToolRegistry, LocalExecutor]:
    executor = LocalExecutor(cwd=workspace, default_timeout=10)
    ctx = AgentContext(workspace=workspace, executor=executor, todo=TodoList())
    reg = ToolRegistry()
    reg.register_all(delta_agent().build_tools(ctx))
    return reg, executor


def _run(reg: ToolRegistry, name: str, args: dict):
    return reg.execute(name, args)


def test_delta_generates_and_runs_a_task_script(tmp_path):
    reg, executor = _toolset(tmp_path)
    try:
        script = "compute.py"
        _run(reg, "write_file", {"path": script, "content": "print(6 * 7)"})

        result = _run(reg, "run_shell", {"command": f"python {script}", "description": "run compute"})
        assert result["exit_code"] == 0
        assert "42" in result["output"]

        # The script is a real file in the workspace (the artifact).
        assert (tmp_path / script).read_text() == "print(6 * 7)"
    finally:
        executor.close()


def test_delta_reads_stderr_and_repairs_a_failing_script(tmp_path):
    reg, executor = _toolset(tmp_path)
    try:
        script = "analysis.py"
        # v1 is broken (NameError on the misspelled name).
        _run(reg, "write_file", {"path": script, "content": "print(misspeled_variable)"})
        fail = _run(reg, "run_shell", {"command": f"python {script}", "description": "run analysis"})
        assert fail["exit_code"] != 0
        # stderr carries the diagnostic (available to the agent for repair).
        assert fail.get("stderr") or "Error" in fail.get("output", "")

        # Repair: replace the misspelling, re-run, succeed.
        _run(
            reg,
            "replace_in_file",
            {"path": script, "old": "misspeled_variable", "new": "6 * 7"},
        )
        ok = _run(reg, "run_shell", {"command": f"python {script}", "description": "rerun analysis"})
        assert ok["exit_code"] == 0
        assert "42" in ok["output"]
        assert (tmp_path / script).read_text() == "print(6 * 7)"
    finally:
        executor.close()


def test_shell_retains_powershell_on_windows():
    # Shell is the scripting execution layer; on Windows it must still drive PowerShell,
    # on POSIX bash/sh. This is the "keep shell/script" half of the R6.0 contract.
    if not _WIN:
        pytest.skip("PowerShell only exists on Windows")
    reg, executor = _toolset(Path("."))
    try:
        result = _run(reg, "run_shell", {"command": "$PSVersionTable.PSVersion.Major", "description": "probe"})
        assert result["exit_code"] == 0
        assert result["output"].strip()
    finally:
        executor.close()
