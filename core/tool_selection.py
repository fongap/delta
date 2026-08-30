"""Per-call tool injection (v0.3.0 P0 "model-call slimming").

Every model call used to carry the FULL `registry.schemas()` — dozens of tool schemas
riding along on turns that never touch a tool. On free/shared gateways (NVIDIA nodes
behind ai-gateway) that inflates prompt processing and TTFT until the call times out,
and it hands the model a huge surface to mis-pick from.

This module decides, per model call, WHICH tool schemas to inject. It is deliberately
deterministic and conservative:

- A small CORE set (human-in-the-loop, planning, skills, progress) is always injected —
  these are harness functions, never task tools, and cutting them silently breaks the
  conversation contract (ask_user/propose_plan/ask-first skills).
- Everything else rides on CATEGORY SIGNALS scanned over the CURRENT turn (the latest
  user message onward: user text, assistant narration, and tool calls/results already
  made this turn). Signals are recall-biased (EN+ZH): a spurious match only costs a few
  extra schemas, while a miss would leave the model unable to do the task at all.
- Categories only GROW within a turn (the scan window widens as the turn progresses) —
  a task that starts as "summarize this" and turns into "save it to a file" keeps its
  file tools from the moment the model asks for them.
- `family="code"` pins the workspace base (files/search/shell/git): a coding agent whose
  shell tools depend on keyword luck is broken by design.

Escape hatches (engine-owned): a turn whose reply contains tool-call markup naming a
withheld tool flips the session to full injection (`named_withheld_tools`), and a
context-budget trim can drop to CORE-only for a turn. `"full"`/`"off"` modes (and any
non-"auto" value) restore the old always-everything behavior exactly.
"""

from __future__ import annotations

import re
from typing import Any

# Harness tools that must ride along on every call: the model can neither ask a
# clarifying question, propose a plan, load a skill, nor drive the Progress panel
# without them, and none of them is task-specific or heavy.
CORE_TOOLS = frozenset(
    {"ask_user", "propose_plan", "load_skill", "save_skill", "todo_write"}
)

# family → categories injected regardless of signals. Code is workspace-bound BY
# PURPOSE; keyword-gating its file/shell/search/git tools would trade reliability
# for tokens on the surface where reliability is the product.
FAMILY_BASE: dict[str, frozenset[str]] = {
    "code": frozenset({"files", "search", "shell", "git"}),
}

# Ordered name → category rules; first match wins. Order matters: `send_file` must be
# messaging (not files), `web_search` must be web (not search), `git_diff` must be git.
_NAME_RULES: tuple[tuple[str, str], ...] = (
    (r"shell|^run_|command", "shell"),
    (r"send_|message|channel|subscription|notif", "messaging"),
    (r"schedul|wake|sleep_|timer|cron|recurr|remind", "automation"),
    (r"^git_|commit|branch|pull_request", "git"),
    (r"memory|remember|recall", "memory"),
    (r"web_|browser|_url", "web"),
    (r"^grep$|search_files|^search$|ripgrep", "search"),
    (r"github|gitlab|issue|jira|notion|linear|gmail|email|calendar|gcal|drive|sheet|repo", "connectors"),
    (r"file|patch|director|folder|read|write|edit|diff|^list_", "files"),
    (r"todo", "todo"),
    (r"skill|explore", "skills"),
)
_NAME_RE = [(re.compile(pattern), cat) for pattern, cat in _NAME_RULES]


def tool_category(name: str) -> str:
    """The injection category for a registry tool name. Unrecognized names (MCP,
    future tools) map to "misc", which is ALWAYS injected — an unknown tool is more
    likely session-critical than safe to hide."""
    if name in CORE_TOOLS:
        return "core"
    for pattern, category in _NAME_RE:
        if pattern.search(name):
            return category
    return "misc"


# Recall-biased (EN+ZH) per-category signal patterns over the current turn's text.
# False positives are cheap (a few extra schemas); false positives here are safer than
# the failure mode of a miss (model can't use tools it can't see).
_SIGNALS: dict[str, tuple[str, ...]] = {
    "files": (
        r"文件|档案|路径|目录|保存|另存|导出|报告|笔记|备忘",
        r"file|folder|director|path|export|\b(read|write|save|edit|open)\b",
        r"\.(docx?|xlsx?|pptx?|pdf|md|csv|json|txt|log)\b",
    ),
    "shell": (
        r"命令|终端|运行|执行|脚本|安装|卸载|编译|部署",
        r"shell|terminal|command|\brun\b|script|install|\bpip\b|\bnpm\b|python|node\b|\bbuild\b|debug",
    ),
    "search": (
        r"grep|ripgrep|全文|源码|代码里|codebase",
        r"\bfind\b.{0,16}\b(files|code|where)\b|search.{0,12}(code|files)",
    ),
    "git": (
        r"\bgit\b|commit|push|branch|merge|rebase|\bdiff\b|提交|分支|合并|版本",
    ),
    "web": (
        r"网页|网站|上网|搜索|链接|最新|今天|新闻|热搜|截图|浏览器",
        r"\bweb\b|\bsearch\b|look\s?up|browse|http|url|google|bing|duckduckgo|browser|screenshot|latest|news",
    ),
    "memory": (
        r"记住|忘了|之前(说|提|讲)|上次|偏好|回忆",
        r"remember|memory|recall|prefer(ence)?|last time|previously",
    ),
    "messaging": (
        r"发送|发消息|消息|邮件|频道|通知|回复到",
        r"\bsend\b|message|email|mail|telegram|slack|discord|channel|notif",
    ),
    "automation": (
        r"定时|每天|每周|每小时|提醒|循环任务|自动化",
        r"schedul|cron|recurr|remind(er)?|automation|every\s?(day|hour|week|morning|monday)",
    ),
    "connectors": (
        r"仓库|日历|表格|工单|网盘",
        r"github|gitlab|jira|notion|linear|gmail|calendar|\bdrive\b|\bsheet\b|issue|pull\s?request|\bpr\b|repo\b",
    ),
}
_SIGNAL_RE = {
    cat: [re.compile(p, re.IGNORECASE) for p in patterns]
    for cat, patterns in _SIGNALS.items()
}

_WORD = re.compile(r"[A-Za-z0-9_/.-]+")


def signal_categories(text: str) -> set[str]:
    """Categories signaled by one chunk of text (user message, assistant narration…)."""
    if not text:
        return set()
    return {
        cat
        for cat, patterns in _SIGNAL_RE.items()
        if any(p.search(text) for p in patterns)
    }


def _message_text(message: dict[str, Any]) -> str:
    """The comparable text of one message: string content, text parts of a parts-list,
    or the tool names a structured assistant message called."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def turn_signal_categories(messages: list[dict[str, Any]]) -> set[str]:
    """Categories for the CURRENT turn: signals from the latest user message onward,
    including categories of any tool calls already made this turn (a task that started
    with tools keeps them — categories only grow within a turn)."""
    start = 0
    for i, message in enumerate(messages):
        if message.get("role") == "user":
            start = i
    categories: set[str] = set()
    for message in messages[start:]:
        role = message.get("role")
        if role in ("user", "assistant"):
            categories |= signal_categories(_message_text(message))
        if role == "assistant":
            for tc in message.get("tool_calls") or []:
                name = (tc.get("function") or {}).get("name") or ""
                if name:
                    categories.add(tool_category(name))
    return categories


def select_tool_names(
    registry_names: list[str],
    messages: list[dict[str, Any]],
    *,
    family: str = "knowledge",
    minimal: bool = False,
) -> list[str]:
    """The tool names to inject for THIS call. `minimal` (context-budget trim) drops
    to the core set only; otherwise core + family base + signal matches + misc."""
    available = set(registry_names)
    selected = set(CORE_TOOLS & available)
    # "misc" (unrecognized names — MCP, future tools) always rides along: an unknown
    # tool is more likely session-critical than safe to hide.
    misc = {n for n in available if tool_category(n) == "misc"}
    if minimal:
        return sorted(selected | misc)
    categories = FAMILY_BASE.get(family, frozenset()) | turn_signal_categories(messages)
    for name in available - selected - misc:
        if tool_category(name) in categories:
            selected.add(name)
    return sorted(selected | misc)


def named_withheld_tools(
    text: str | None,
    injected: list[str],
    registry_names: list[str],
) -> list[str]:
    """Registry tools the model visibly reached for but that weren't injected: tool
    names appearing in assistant text that carries tool-call markup. The engine uses
    this as the escape hatch — flip the session to full injection and retry, instead of
    failing the turn because the model can't see the tool it needs."""
    if not text or not registry_names:
        return []
    injected_set = set(injected)
    tokens = set(_WORD.findall(text))
    return sorted(
        name for name in registry_names if name not in injected_set and name in tokens
    )
