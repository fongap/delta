"""Per-call tool injection (v0.3.0 P0): category mapping, signal resolution, escape
hatch — all pure, no provider."""

from __future__ import annotations

from core.tool_selection import (
    CORE_TOOLS,
    named_withheld_tools,
    select_tool_names,
    tool_category,
    turn_signal_categories,
)

# Simulated registry for a Cowork-like surface: files/shell/web/memory/messaging/skills.
REGISTRY = [
    "list_files", "read_file", "write_file", "apply_patch", "request_directory",
    "grep", "run_shell", "shell_task_output",
    "git_status", "git_diff", "git_log",
    "web_search", "web_fetch",
    "remember", "memory_read", "memory_forget",
    "send_message", "send_file", "subscribe_channel",
    "create_scheduled_task", "sleep_until",
    "load_skill", "save_skill", "ask_user", "propose_plan", "todo_write",
    "mcp__notes__lookup",  # unrecognized → misc
]


def _selected(messages, family="knowledge", minimal=False):
    return set(select_tool_names(REGISTRY, messages, family=family, minimal=minimal))


def _turn(*user_texts):
    return [{"role": "user", "content": t} for t in user_texts]


# -- category mapping -----------------------------------------------------------


def test_categories_are_specific_not_generic():
    assert tool_category("run_shell") == "shell"
    assert tool_category("send_file") == "messaging"  # NOT files (order matters)
    assert tool_category("web_search") == "web"  # NOT search
    assert tool_category("grep") == "search"
    assert tool_category("git_diff") == "git"
    assert tool_category("remember") == "memory"
    assert tool_category("create_scheduled_task") == "automation"
    assert tool_category("read_file") == "files"
    assert tool_category("mcp__notes__lookup") == "misc"


def test_core_tools_are_core():
    for name in CORE_TOOLS:
        assert tool_category(name) == "core"


# -- selection ------------------------------------------------------------------


def test_plain_chat_turn_gets_core_plus_misc_only():
    """The headline case: “帮我总结这段文字” must not carry the whole toolbox."""
    selected = _selected(_turn("帮我总结这段文字"))
    assert "run_shell" not in selected
    assert "web_search" not in selected
    assert "read_file" not in selected
    assert "send_message" not in selected
    assert "mcp__notes__lookup" in selected  # misc always rides along
    assert {"ask_user", "propose_plan", "load_skill"} <= selected


def test_task_message_signals_match_categories():
    selected = _selected(_turn("查一下 http://example.com 的网页并保存报告"))
    assert {"web_search", "web_fetch", "write_file", "read_file"} <= selected
    assert "run_shell" not in selected

    selected = _selected(_turn("send a message to the telegram channel"))
    assert "send_message" in selected
    assert "read_file" not in selected


def test_english_signals():
    selected = _selected(_turn("remember that I prefer short replies"))
    assert "remember" in selected
    assert "run_shell" not in selected


def test_code_family_pins_workspace_base():
    selected = _selected(_turn("hi"), family="code")
    # A coding agent gets its workspace base on every call regardless of keywords.
    assert {"read_file", "grep", "run_shell", "git_status"} <= selected
    assert "send_message" not in selected  # …but not the whole toolbox


def test_categories_grow_within_a_turn():
    """The task started tool-free, then the model called web_search: the next model
    call of the SAME turn keeps web (and anything the user's later message signals)."""
    messages = [
        {"role": "user", "content": "总结这段"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"function": {"name": "web_search", "arguments": '{"query": "x"}'}}
            ],
        },
        {"role": "tool", "content": "results"},
    ]
    selected = _selected(messages)
    assert "web_fetch" in selected  # web category activated by the call itself


def test_minimal_drops_to_core():
    selected = _selected(_turn("查网页保存报告"), minimal=True)
    assert selected == (CORE_TOOLS & set(REGISTRY)) | {"mcp__notes__lookup"}


def test_empty_registry_selects_nothing():
    assert select_tool_names([], _turn("hi")) == []


# -- escape hatch ---------------------------------------------------------------


def test_named_withheld_tools():
    text = 'I would call web_search("x") to look that up'
    assert named_withheld_tools(
        text, injected=["ask_user"], registry_names=["web_search", "run_shell"]
    ) == ["web_search"]
    # Injected tools are never reported; prose that doesn't name a tool stays silent.
    assert named_withheld_tools("all done", ["ask_user"], ["web_search"]) == []
    assert named_withheld_tools(
        'calling run_shell now', injected=[], registry_names=["run_shell"]
    ) == ["run_shell"]


def test_signals_scan_only_current_turn():
    """An earlier turn's web task doesn't keep web tools on a later tool-free turn."""
    messages = [
        {"role": "user", "content": "search the web for x"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "谢谢"},
    ]
    assert "web" not in turn_signal_categories(messages)
