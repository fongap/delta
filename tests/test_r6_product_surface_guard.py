"""R6.0 architecture guard — prevents regression of the product-surface convergence.

The guard is deliberately static and mechanical (no framework, no provider): it scans
the source for the specific anti-patterns the cleanup removed, so they cannot sneak back
in through a copy-paste or a partial revert. It also asserts the capabilities that must
STAY (files, shell, script execution) are present — cleaning Coding must never delete
Scripting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# -- identity defaults must not fall back to "code" ----------------------------------


def _grep_code_default(rel: str) -> list[str]:
    text = _read(rel)
    # A bare `"code"` or `'code'` used as a default/fallback for an agent identity.
    patterns = [
        r'name\s*=\s*name\s*or\s*["\']code["\']',
        r'agent:\s*str\s*=\s*["\']code["\']',
        r'or\s*["\']code["\']',  # `record.agent or "code"` / `agent or "code"`
        r'default\s*=\s*["\']code["\']',  # argparse / dataclass default
        r'DEFAULT.*=.*["\']code["\']',
    ]
    hits = []
    for p in patterns:
        for m in re.finditer(p, text):
            line = text[: m.start()].count("\n") + 1
            hits.append(f"{rel}:{line}: {m.group().strip()}")
    return hits


@pytest.mark.parametrize(
    "rel",
    [
        "core/agents/registry.py",
        "core/sessions.py",
        "core/conversations.py",
        "core/cli.py",
        "core/runtime.py",
        "services/server/manager_sessions.py",
        "services/server/manager_workspace.py",
        "services/server/manager_mcp_connectors.py",
        "services/server/manager_contract.py",
        "services/server/app.py",
    ],
)
def test_no_code_default_anywhere(rel):
    hits = _grep_code_default(rel)
    assert not hits, "agent default/fallback to 'code' found (must be 'delta'):\n  " + "\n  ".join(
        hits
    )


# -- the code agent builder must not exist ------------------------------------------


def test_code_agent_removed():
    assert not (ROOT / "core/agents/code.py").exists(), "core/agents/code.py must not exist"
    assert not (ROOT / "core/agents/chat.py").exists(), "core/agents/chat.py must not exist"
    assert not (ROOT / "core/agents/myhelper.py").exists(), "core/agents/myhelper.py must not exist"
    assert not (ROOT / "core/personas/manifest.py").exists(), "third-party manifest parser must not exist"
    assert not (ROOT / "core/personas/loading.py").exists(), "third-party persona loading must not exist"
    assert not (ROOT / "integrations/tools/git.py").exists(), "git tool module must not exist"
    assert not (ROOT / "integrations/tools/subagent.py").exists(), "explorer subagent must not exist"
    # build_code_engine shim is gone.
    assert "def build_code_engine" not in _read("core/agent.py")


def test_code_files_and_git_capabilities_removed_from_catalog():
    text = _read("core/catalog.py")
    assert '"code_files"' not in text, "code_files capability must not be in the catalog"
    assert '"git"' not in text or "git" not in re.sub(r'#.*', '', text), "git capability must not be in the catalog"
    assert "from integrations.tools.git import" not in text


def test_family_code_branch_removed():
    # FAMILY_BASE no longer pins a code entry; the explorer registration is gone.
    text = _read("core/tool_selection.py")
    assert '"code"' not in text or text.count('"code"') == 0 or '"code"' not in re.sub(
        r'#.*', '', text
    ), 'no "code" family string in tool_selection'
    assert "FAMILY_BASE" not in text or "{}" in text, "FAMILY_BASE must be empty or removed"
    agent_text = _read("core/agent.py")
    assert 'family == "code"' not in agent_text, "no code-family branch in build_engine"
    assert "explorer_tools" not in agent_text


# -- capabilities that must STAY (cleaning Coding ≠ deleting Scripting) ----------------


def test_delta_retains_scripting_and_files():
    from core.agents.delta_agent import DELTA_CAPABILITIES

    caps = set(DELTA_CAPABILITIES)
    assert "files" in caps, "Delta must retain the files capability"
    assert "search" in caps, "Delta must retain search"
    assert "shell" in caps, "Delta must retain shell (script execution)"
    assert "todo" in caps, "Delta must retain todo"
    # No coding surface leaked in.
    assert "code_files" not in caps
    assert "git" not in caps


def test_delta_toolset_has_editors_and_shell():
    from core.agents.delta_agent import delta_agent
    from core.agents.base import AgentContext
    from integrations.tools.todo import TodoList
    from pathlib import Path
    import tempfile

    ctx = AgentContext(workspace=Path(tempfile.mkdtemp()), executor=object(), todo=TodoList())
    names = {getattr(t, "__name__", "") for t in delta_agent().build_tools(ctx)}
    # Generic file editing (moved out of code_files) — must survive the cleanup.
    for must_have in (
        "read_file",
        "read_file_lines",
        "read_document",
        "write_file",
        "replace_in_file",
        "apply_patch",
        "apply_unified_diff",
        "list_files",
        "grep",
        "run_shell",
        "shell_task_output",
        "shell_task_kill",
        "todo_write",
    ):
        assert must_have in names, f"Delta lost a required tool: {must_have}"
    # No git tools on Delta.
    for must_not in ("git_status", "git_diff", "git_log"):
        assert must_not not in names, f"Delta must not expose {must_not}"


# -- the delta prompt names the three product domains --------------------------------


def test_delta_prompt_names_three_domains():
    from core.agents.delta_agent import DELTA_INSTRUCTIONS

    prompt = DELTA_INSTRUCTIONS.lower()
    # The three first-class domains.
    assert "office work" in prompt
    assert "research" in prompt and "analysis" in prompt
    assert "content" in prompt and "creation" in prompt
    # Scripting is explicitly named as an execution ability, not the product.
    assert "script" in prompt
    assert "not a coding agent" in prompt
