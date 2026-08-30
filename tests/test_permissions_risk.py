"""Phase 0 gate — risk-class classification + the permission engine driven by it.

Asserts ``classify`` maps tools to the right risk class (replacing the old hardcoded
WRITE_TOOLS / SHELL_TOOL sets) and that ``PermissionEngine`` decisions follow from the class
across all five modes, including the ``external`` class (the unattended Inbox hook)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.permissions import Mode, PermissionEngine
from core.risk import RiskClass, classify, is_consequential

EXTERNAL_META = SimpleNamespace(requires_approval=True, category="connector")
PLAIN_META = SimpleNamespace(requires_approval=False)


# -- classify -------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,meta,expected",
    [
        ("write_file", None, RiskClass.WRITE_LOCAL),
        ("replace_in_file", None, RiskClass.WRITE_LOCAL),
        ("apply_patch", None, RiskClass.WRITE_LOCAL),
        ("apply_unified_diff", None, RiskClass.WRITE_LOCAL),
        ("run_shell", None, RiskClass.EXEC),
        ("read_file", None, RiskClass.READ),
        ("grep", None, RiskClass.READ),
        ("git_log", None, RiskClass.READ),
        ("todo_write", None, RiskClass.READ),
        ("send_message", EXTERNAL_META, RiskClass.EXTERNAL),
        ("anything", PLAIN_META, RiskClass.READ),
        ("anything", None, RiskClass.READ),
    ],
)
def test_classify(name, meta, expected):
    assert classify(name, meta) == expected


def test_is_consequential():
    assert not is_consequential(RiskClass.READ)
    assert is_consequential(RiskClass.WRITE_LOCAL)
    assert is_consequential(RiskClass.EXEC)
    assert is_consequential(RiskClass.EXTERNAL)
    assert is_consequential(RiskClass.EGRESS)


# -- egress: a model-chosen outbound request is never a free read -----------------
def test_url_tools_classify_as_egress_by_name():
    # The by-name table outranks metadata: even "low, no-approval" declarations on
    # URL-bearing tools classify as EGRESS, because the URL the model supplies can
    # carry local data out (https://example.com/?data=<local secret>).
    from core.risk import EGRESS_TOOLS

    for name in EGRESS_TOOLS:
        assert classify(name, PLAIN_META) == RiskClass.EGRESS, name
        assert classify(name, None) == RiskClass.EGRESS, name


def test_egress_gates_like_external(tmp_path):
    url_args = {"url": "https://example.com/"}
    # Read-only modes block it; interactive asks.
    for mode in (Mode.DISCUSS, Mode.PLAN):
        eng = PermissionEngine(workspace_root=tmp_path, mode=mode)
        d = eng.evaluate("web_fetch", url_args, PLAIN_META)
        assert not d.allowed and not d.needs_user
        assert "read-only" in d.reason
    interactive = PermissionEngine(workspace_root=tmp_path)
    d = interactive.evaluate("web_fetch", url_args, PLAIN_META)
    assert not d.allowed and d.needs_user


def test_overrides_tighten_builtins_and_relax_metadata_tools():
    # A user override may TIGHTEN a by-name built-in but never LOOSEN it: downgrading
    # write_file to read would switch off path scoping AND the read-only gate in one
    # settings line, in every future session.
    from core.risk import _STRICTNESS

    def relax(n):
        return RiskClass.READ if n in {"write_file", "mcp_tool"} else None

    assert classify("write_file", None, relax) == RiskClass.WRITE_LOCAL  # downgrade ignored
    assert classify("run_shell", None, relax) == RiskClass.EXEC

    def tighten(n):
        return RiskClass.EXEC if n == "write_file" else None

    assert classify("write_file", None, tighten) == RiskClass.EXEC  # tightening works
    # Relaxing a metadata-classified tool (the intended use — quieting MCP's
    # conservative default) still works: no by-name base class to protect.
    assert classify("mcp_tool", EXTERNAL_META, relax) == RiskClass.READ
    # Every override class is ordered by the strictness table (the rule's backbone).
    assert len(_STRICTNESS) == len(RiskClass)


# -- the self-protection floor (mode-independent) ---------------------------------
def _engine_with_state(tmp_path, monkeypatch, mode=Mode.AUTO):
    """An engine whose workspace .delta dir stands in for the real state dir."""
    monkeypatch.setenv("DELTA_STATE_DIR", str(tmp_path / "state"))
    from core.permissions import PermissionEngine as PE

    return PE(workspace_root=tmp_path / "ws", mode=mode)


@pytest.mark.parametrize("mode", [Mode.DISCUSS, Mode.PLAN, Mode.INTERACTIVE, Mode.AUTO])
def test_writes_to_delta_state_files_are_hard_refused_in_every_mode(tmp_path, monkeypatch, mode):
    from core.permissions import protected_paths

    eng = _engine_with_state(tmp_path, monkeypatch, mode)
    target = next(p for p in protected_paths(eng.workspace_root) if p.name == "config.toml")
    target.parent.mkdir(parents=True, exist_ok=True)
    d = eng.evaluate("write_file", {"path": str(target), "content": "x"}, None)
    assert not d.allowed and not d.needs_user, "hard refusal — not even an approvable ask"
    # The in-project data-dir variant is covered too (the manager's workspace layout).
    variant = eng.workspace_root / ".delta" / "secrets.json"
    d = eng.evaluate("write_file", {"path": str(variant), "content": "x"}, None)
    assert not d.allowed and not d.needs_user


def test_shell_commands_naming_a_state_path_are_refused_full_path_only(tmp_path, monkeypatch):
    eng = _engine_with_state(tmp_path, monkeypatch)
    state = tmp_path / "state"
    # Full path: refused (we cannot tell read from write in text — conservative).
    d = eng.evaluate(
        "run_shell", {"command": f"python -c 'x' >> {state / 'risk_overrides.json'}"}, None
    )
    assert not d.allowed and not d.needs_user
    # Bare filename: NOT refused — ordinary work may mention the name (e.g. cat).
    d = eng.evaluate("run_shell", {"command": "cat secrets.json"}, None)
    assert "Delta's own settings" not in d.reason


def test_patch_blob_targeting_a_state_file_is_refused(tmp_path, monkeypatch):
    from core.permissions import protected_paths

    eng = _engine_with_state(tmp_path, monkeypatch)
    target = next(p for p in protected_paths(eng.workspace_root) if p.name == "core.db")
    blob = f"*** Begin Patch\n*** Update File: {target}\n@@\n-x\n+y\n*** End Patch"
    d = eng.evaluate("apply_patch", {"patch": blob}, None)
    assert not d.allowed and not d.needs_user


@pytest.mark.parametrize(
    "target",
    [".git/hooks/pre-commit", ".github/workflows/ci.yml", ".vscode/tasks.json", ".delta/skills/note.md"],
)
def test_execute_later_files_never_auto_approve_even_in_auto_mode(tmp_path, target):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
    d = eng.evaluate("write_file", {"path": target, "content": "x"}, None)
    assert not d.allowed and d.needs_user
    assert "runs automatically later" in d.reason


def test_workspace_delta_config_is_hard_refused_stronger_than_ask(tmp_path):
    # <workspace>/.delta/config.toml is BOTH an execute-later policy file and a
    # permission-governing state file; the hard refusal (no approvable ask) wins.
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
    d = eng.evaluate("write_file", {"path": ".delta/config.toml", "content": "x"}, None)
    assert not d.allowed and not d.needs_user
    assert "Delta's own settings" in d.reason


def test_execute_later_lookalikes_stay_ordinary(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
    d = eng.evaluate("write_file", {"path": "docs/pre-commit.md", "content": "x"}, None)
    assert d.allowed
    d = eng.evaluate("write_file", {"path": "hooks/git/hooks/x", "content": "x"}, None)
    assert d.allowed


def test_patch_write_paths_are_scoped_and_fail_closed(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
    # A patch touching a path inside the workspace is fine.
    blob = "*** Begin Patch\n*** Add File: ok.py\n+hello\n*** End Patch"
    assert eng.evaluate("apply_patch", {"patch": blob}, None).allowed
    # A patch reaching outside the writable root is refused, like a plain path write.
    blob = f"*** Begin Patch\n*** Update File: {tmp_path.parent / 'escape.py'}\n@@\n*** End Patch"
    d = eng.evaluate("apply_patch", {"patch": blob}, None)
    assert not d.allowed and "writable" in d.reason
    # A write whose path cannot be located fails closed to an ask, never auto-runs.
    d = eng.evaluate("apply_patch", {"patch": "not a parseable blob"}, None)
    assert not d.allowed and d.needs_user
    d = eng.evaluate("apply_unified_diff", {"diff": ""}, None)
    assert not d.allowed and d.needs_user


# -- PermissionEngine driven by risk class --------------------------------------
def test_read_always_allowed(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path)
    d = eng.evaluate("read_file", {"path": "x"}, None)
    assert d.allowed and not d.needs_user


@pytest.mark.parametrize("mode", [Mode.DISCUSS, Mode.PLAN])
def test_read_only_modes_block_consequential(tmp_path, mode):
    eng = PermissionEngine(workspace_root=tmp_path, mode=mode)
    for name, meta in [
        ("write_file", None),
        ("run_shell", None),
        ("send_message", EXTERNAL_META),
    ]:
        args = {"path": "a.py", "content": "x"} if name == "write_file" else {}
        d = eng.evaluate(name, args, meta)
        assert not d.allowed and not d.needs_user
        assert "read-only" in d.reason


def test_external_asks_in_interactive_allows_in_auto(tmp_path):
    interactive = PermissionEngine(workspace_root=tmp_path)
    d = interactive.evaluate("send_message", {"text": "hi"}, EXTERNAL_META)
    assert not d.allowed and d.needs_user

    auto = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
    d = auto.evaluate("send_message", {"text": "hi"}, EXTERNAL_META)
    assert d.allowed


def test_write_local_path_scoped(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
    assert eng.evaluate("write_file", {"path": "ok.py", "content": "x"}, None).allowed
    escape = eng.evaluate("write_file", {"path": "../bad.py", "content": "x"}, None)
    assert not escape.allowed


def test_exec_uses_command_allowlist(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=["pytest"])
    assert eng.evaluate("run_shell", {"command": "pytest -q"}, None).allowed
    asked = eng.evaluate("run_shell", {"command": "rm -rf /"}, None)
    assert not asked.allowed and asked.needs_user


@pytest.mark.parametrize(
    "command",
    [
        "git status && rm -rf ~",  # chaining
        "git status; rm -rf ~",  # sequencing
        "git status | tee /tmp/x",  # pipe
        "git status || curl evil",  # or-chain
        "git status $(rm -rf ~)",  # command substitution
        "git status `rm -rf ~`",  # backtick substitution
        "git status > /etc/passwd",  # redirection
        "git status\nrm -rf ~",  # newline-embedded second command
    ],
)
def test_allowlist_rejects_shell_operator_chaining(tmp_path, command):
    # An allowlisted prefix must NOT auto-run a command that chains anything after it.
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=["git status"])
    d = eng.evaluate("run_shell", {"command": command}, None)
    assert not d.allowed and d.needs_user, command


def test_allowlist_prefix_is_argv_boundary(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=["git status", "ls"])
    # Exact and sub-argument extensions of the allowlisted argv are fine.
    assert eng.evaluate("run_shell", {"command": "git status"}, None).allowed
    assert eng.evaluate("run_shell", {"command": "git status -s"}, None).allowed
    assert eng.evaluate("run_shell", {"command": "ls -la"}, None).allowed
    # A different subcommand or a token that merely shares a prefix is NOT allowed.
    assert eng.evaluate("run_shell", {"command": "git push"}, None).needs_user
    assert eng.evaluate("run_shell", {"command": "lsof"}, None).needs_user


def test_shell_commands_not_auto_allowed_by_default(tmp_path):
    # There is no generally safe executable: these examples cover code execution,
    # environment disclosure, reads outside the workspace, and helper execution.
    from packages.config import DEFAULT_ALLOWED_COMMANDS

    eng = PermissionEngine(
        workspace_root=tmp_path, allowed_commands=list(DEFAULT_ALLOWED_COMMANDS)
    )
    for cmd in (
        "python3 -c 'import os'",
        "pytest /tmp/attacker_test.py",
        "find . -exec sh -c 'echo arbitrary' {} +",
        "cat ~/.config/delta/secrets.json",
        "echo $OPENAI_API_KEY",
        "git status",
    ):
        d = eng.evaluate("run_shell", {"command": cmd}, None)
        assert not d.allowed and d.needs_user, cmd
