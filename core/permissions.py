"""Permission engine — decides allow / deny / ask-user for each proposed tool call.

Modes: Plan (read-only) · Interactive (auto reads, ask on writes/commands) · Auto
(allow, still path-scoped). Refined by argument patterns (path-under-root, command
prefixes) and a session allowlist. The engine only *decides*; the turn engine routes
`needs_user` decisions to a surface for approval and records the outcome.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from core.gateway import write_paths
from core.risk import (  # re-exported for back-compat (manager.py imports WRITE_TOOLS)
    RiskClass,
    RiskOverrides,
    WRITE_TOOLS,
    classify,
    is_consequential,
)

# Shell metacharacters that turn one "allowlisted" command into several. Any of these in a
# command disqualifies it from allowlist auto-run — approval is required instead. Covers
# chaining (`;` `&` `&&` `||`), pipes (`|`), redirection (`>` `<`), command substitution
# (`` ` `` `$(`), process substitution / grouping (`(`), and newlines.
_SHELL_OPERATORS = (";", "&", "|", ">", "<", "`", "$(", "(", "\n", "\r")


def _has_shell_operators(command: str) -> bool:
    return any(op in command for op in _SHELL_OPERATORS)


class Mode(str, Enum):
    DISCUSS = "discuss"  # read-only conversation: no edits, no planning workflow
    PLAN = (
        "plan"  # read-only + the planning contract (explore → propose_plan → execute)
    )
    INTERACTIVE = "interactive"  # ask for approval (default)
    AUTO = "auto"  # full access
    CUSTOM = "custom"  # interactive + auto-allow the config's `auto_allow` tools


# Modes whose enforcement is read-only. DISCUSS and PLAN share the same gate; they differ
# only in intent — PLAN additionally drives the agent toward a propose_plan approval.
READ_ONLY_MODES = frozenset({Mode.DISCUSS, Mode.PLAN})

# WRITE_LOCAL target arguments, in the spirit of connectors' TARGET_ARGS: which argument
# names a tool's on-disk target for the writable-root scope check. Superseded by
# gateway.write_paths, which also extracts targets buried in patch/diff blobs — kept
# only as a name alias for back-compat.
_WRITE_PATH_ARGS = ("path", "file_path", "filepath", "file")


def _protected_state_files() -> list[str]:
    """Filenames of the permission system's own state, wherever Delta keeps them.

    The manager stores state either in the global state dir or, when a workspace is
    open, in `<workspace>/.delta/` — the same files govern permissions in both layouts,
    so both are protected."""
    return [
        "config.toml",  # packages.config: modes, auto-allow, command allowlists
        "risk_overrides.json",  # core.overrides: per-tool risk classes
        "workspace_trust.json",  # core.workspace_trust: which repos may grant commands
        "secrets.json",  # SecretStore: every connector/MCP credential
        "unattended.json",  # core.unattended: unattended-run autonomy flags
        "inbox_routing.json",  # core.inbox_routing: which agent answers which inbox
        "memory-settings.json",  # memory on/off + standing rules
        "core.db",  # sessions, memory, audit trail
        "run-events.db",  # run event ledger
        "automation.db",  # TaskStore: automation records + §25 standing rules
    ]


def protected_paths(workspace_root: Path | None = None) -> list[Path]:
    """Files that govern the permission system itself. Nothing the agent does may write
    these — in any mode, through any tool. The escalation this blocks is: approve one
    ordinary-looking command, it quietly appends to the rule file, and every future
    session is more permissive. That happens in the DEFAULT interactive mode, so this
    cannot be a property of a sandbox or of any one mode; it is a floor. Loosening
    requires editing these files out-of-band."""
    from packages.secrets import state_dir

    base = state_dir()
    names = _protected_state_files()
    out = [base / name for name in names]
    if workspace_root is not None:
        out.extend(Path(workspace_root) / ".delta" / name for name in names)
    return out


# Files INSIDE a workspace that execute on a later, innocuous-looking action, or that
# carry agent-reachable policy. An edit here is a deferred command: writing
# `.git/hooks/pre-commit` and then running `git commit` runs it; editing
# `.delta/config.toml` rewrites the grants Delta itself reads. They stay writable, but
# never WITHOUT a human — no auto-approve path (auto mode, custom auto_allow, session
# "always allow") may clear them.
_PROTECTED_IN_PROJECT = (
    ".git/hooks/",
    ".github/workflows/",
    ".gitlab-ci.yml",
    ".vscode/tasks.json",
    ".delta/",  # workspace config (command grants) + skills the agent could self-grant
)


def _is_protected_in_project(candidate: Path) -> bool:
    posix = candidate.as_posix().lower()
    return any(
        (f"/{marker}" in posix or posix.startswith(marker))
        if marker.endswith("/")
        else posix.endswith("/" + marker)
        for marker in _PROTECTED_IN_PROJECT
    )


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    needs_user: bool = False  # True → surface should prompt the user for approval
    # Set when a task-scoped standing rule allowed the call ("tool → target") so the
    # engine can audit the exact rule and the tool card can say so (§25).
    rule: str = ""
    # Structured provenance of an allow, consumed by the Execution Gateway's grant
    # gate (slice 4a): "blanket" = mode-level full access, "session" = a grant minted
    # by an earlier approval card (ALWAYS_TOOL/ALWAYS_COMMAND), "policy" = explicitly
    # user-authored policy (trusted-workspace command allowlist, task standing rules,
    # configured auto-allow tools). "" = not allowed via any grant path.
    grant: str = ""


def standing_rule_candidate(
    tool_name: str,
    arguments: dict[str, Any],
    metadata: Any = None,
    overrides: RiskOverrides | None = None,
) -> str | None:
    """The target value iff this call is eligible for a task-scoped standing rule
    (UX-DECISIONS §25): external-risk only (never exec/write-local — shell asks forever),
    the tool must declare a target argument, and the call must actually name a target.
    Returns None otherwise — ineligible calls keep parking approvals as today."""
    from integrations.connectors.tool_defs import target_arg_for

    if classify(tool_name, metadata, overrides) is not RiskClass.EXTERNAL:
        return None
    arg = target_arg_for(tool_name)
    if arg is None:
        return None
    value = str((arguments or {}).get(arg) or "").strip()
    return value or None


@dataclass
class PermissionEngine:
    workspace_root: Path
    mode: Mode = Mode.INTERACTIVE
    allowed_commands: list[str] = field(default_factory=list)
    auto_allow_tools: set[str] = field(default_factory=set)
    session_allow_tools: set[str] = field(default_factory=set)
    session_allow_commands: set[str] = field(default_factory=set)
    # Task-scoped standing rules (§25): {tool: {allowed targets}}, seeded from the owning
    # ScheduledTask's target-shaped entries. Kept by reference and re-read every check, so a
    # rule minted mid-run ("Allow every time") applies to the run's next call too.
    task_rules: dict[str, set[str]] = field(default_factory=dict)
    # User-local risk override resolver (Phase 2). None → use the base classification.
    risk_overrides: RiskOverrides | None = None
    # Shared, possibly-mutable list of roots (RootDir-like / dicts). When omitted, the single
    # `workspace_root` is the sole writable root (back-compat). Kept by reference and re-read on
    # every check, so runtime add/remove of folders takes effect without rebuilding the engine.
    roots: list | None = None

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        self.auto_allow_tools = set(self.auto_allow_tools)
        if self.roots is None:
            self.roots = [{"path": self.workspace_root, "writable": True}]

    def resolved_roots(self) -> list[tuple[Path, bool]]:
        """Public view of the live root table as (resolved path, writable) pairs —
        consumed by the Execution Gateway's confinement re-check."""
        return self._resolved_roots()

    def _resolved_roots(self) -> list[tuple[Path, bool]]:
        out: list[tuple[Path, bool]] = []
        for r in self.roots or []:
            if isinstance(r, dict):
                p, w = r["path"], bool(r.get("writable", False))
            elif isinstance(r, (str, Path)):
                p, w = r, True
            else:  # duck-typed RootDir-like
                p, w = getattr(r, "path"), bool(getattr(r, "writable", False))
            out.append((Path(p).expanduser().resolve(), w))
        return out

    def evaluate(
        self, tool_name: str, arguments: dict[str, Any], metadata: Any = None
    ) -> Decision:
        arguments = arguments or {}
        is_connector = getattr(metadata, "category", "") == "connector"
        risk = classify(tool_name, metadata, self.risk_overrides)
        is_write = risk is RiskClass.WRITE_LOCAL
        is_shell = risk is RiskClass.EXEC
        consequential = is_consequential(risk)

        # SELF-PROTECTION FLOOR — runs before mode, allowlists and every auto-approve
        # path, because the escalation it blocks happens in the DEFAULT mode. No verdict
        # below can reach these files, and no human click in the flow can grant it either:
        # loosening requires editing the files out-of-band.
        if is_write or is_shell:
            hit = self._touches_protected(tool_name, arguments, is_shell)
            if hit is not None:
                return Decision(
                    False,
                    f"refusing to modify Delta's own settings: {hit}",
                    needs_user=False,
                )

        # Discuss / plan modes: read-only.
        if self.mode in READ_ONLY_MODES and consequential:
            return Decision(
                False, f"{self.mode.value} mode is read-only", needs_user=False
            )

        # Path scoping for writes (all modes): every path the write touches — top-level
        # path arguments AND targets buried in a patch/diff blob — must land in a writable
        # root. A write whose path can't be located is not scope-able, so it fails closed
        # to an explicit human decision rather than slipping through auto/custom unscoped.
        needs_human_for_protected = False
        if is_write:
            paths, located = write_paths(tool_name, arguments)
            if not located:
                return Decision(
                    False, "cannot determine the write path to scope", needs_user=True
                )
            for path in paths:
                if not self._under_writable_root(path):
                    return Decision(
                        False, f"path is not in a writable directory: {path}"
                    )
                # In-project files that run on a later action (git hooks, CI config,
                # workspace config) may be edited, but never by an auto-approve path —
                # a human must see it.
                if _is_protected_in_project(self._candidate(path)):
                    needs_human_for_protected = True

        # Non-consequential tools always run.
        if not consequential:
            return Decision(True, "low risk")

        # A protected in-project target (git hooks, CI config, .delta/) skips every
        # auto-approve path below — including auto mode and the session/config
        # allowlists — and asks.
        if needs_human_for_protected:
            return Decision(
                False,
                "this file runs automatically later — approval required",
                needs_user=True,
            )

        # Full access. Blanket by design — the gateway's grant gate (slice 4a) still
        # refuses to release L3+ external effects on this basis alone.
        if self.mode is Mode.AUTO:
            return Decision(True, "full access", grant="blanket")

        # interactive / custom: allowlists.
        if is_shell:
            command = str(arguments.get("command", ""))
            if self._command_allowed(command):
                # User-authored policy: only via workspace trust / config, never minted
                # by an approval card.
                return Decision(True, "command on allowlist", grant="policy")
            if command and command in self.session_allow_commands:
                return Decision(True, "command allowed for session", grant="session")
        if tool_name in self.session_allow_tools and not is_connector:
            return Decision(True, "tool allowed for session", grant="session")

        # Task-scoped standing rules (§25): tool + exact target, owned by the automation.
        # Deliberately NOT subject to the connector exclusion above — the exact-target
        # binding is what makes auto-allowing a connector tool safe. Never for exec risk
        # (candidate extraction is external-risk-only), and additive on top of the mode:
        # read-only modes already returned before this point.
        if tool_name in self.task_rules:
            target = standing_rule_candidate(
                tool_name, arguments, metadata, self.risk_overrides
            )
            if target and target in self.task_rules[tool_name]:
                rule = f"{tool_name} → {target}"
                return Decision(
                    True, f"allowed by standing rule: {rule}", rule=rule, grant="policy"
                )

        # Custom mode auto-approves the configured tools (explicit user policy).
        if self.mode is Mode.CUSTOM and tool_name in self.auto_allow_tools:
            return Decision(True, "auto-allowed by config", grant="policy")

        # Otherwise: ask the user.
        return Decision(False, "requires approval", needs_user=True)

    # -- session memory ---------------------------------------------------------
    def allow_tool_for_session(self, tool_name: str) -> None:
        self.session_allow_tools.add(tool_name)

    def allow_command_for_session(self, command: str) -> None:
        if command:
            self.session_allow_commands.add(command)

    # -- helpers ----------------------------------------------------------------
    def _touches_protected(
        self, tool_name: str, arguments: dict[str, Any], is_shell: bool
    ) -> Optional[str]:
        """The protected settings path this call would modify, or None.

        For writes we resolve the real target (including paths buried in a patch/diff
        blob). For shell we can only inspect the command text — parser depth, so it
        stops accidents and casual attempts, not a determined adversary (that needs the
        OS sandbox). Cheap and worth having regardless.

        Shell matching is on the FULL path only, never a bare filename: matching
        `secrets.json` anywhere in a command would refuse unrelated work that merely
        mentions the name. A command naming the real settings path is refused whether it
        reads or writes — we cannot tell which from text, and the conservative direction
        is the right one for these files.
        """
        targets = [str(p) for p in protected_paths(self.workspace_root)]
        if is_shell:
            command = str(arguments.get("command", ""))
            if not command:
                return None
            lowered = command.replace("\\", "/").lower()
            for target in targets:
                if target.replace("\\", "/").lower() in lowered:
                    return target
            return None
        paths, located = write_paths(tool_name, arguments)
        if not located:
            return None  # unlocatable writes are already failed closed by the caller
        resolved = {str(self._candidate(p)) for p in paths}
        for target in targets:
            if str(Path(target).resolve()) in resolved:
                return target
        return None

    def _candidate(self, path: str) -> Path:
        # Relative paths resolve against the primary (workspace_root); absolute/`~` taken as-is.
        p = Path(path).expanduser()
        return p.resolve() if p.is_absolute() else (self.workspace_root / p).resolve()

    def _under_root(self, path: str) -> bool:
        candidate = self._candidate(path)
        for rp, _ in self._resolved_roots():
            try:
                candidate.relative_to(rp)
                return True
            except ValueError:
                continue
        return False

    def _under_writable_root(self, path: str) -> bool:
        candidate = self._candidate(path)
        for rp, writable in self._resolved_roots():
            if not writable:
                continue
            try:
                candidate.relative_to(rp)
                return True
            except ValueError:
                continue
        return False

    def _command_allowed(self, command: str) -> bool:
        # An allowlist entry auto-runs a command WITHOUT approval, so prefix matching is
        # unsafe: `git status` would auto-approve `git status && rm -rf ~`. Reject anything
        # carrying shell operators (chaining/redirection/substitution) up front, then match
        # the parsed argv against each entry — the entry's own tokens must be an exact
        # prefix of the command's tokens (so `git status` matches `git status -s` but never
        # `git statusfoo` or a bare `git`).
        if _has_shell_operators(command):
            return False
        try:
            argv = shlex.split(command)
        except ValueError:
            return False  # unbalanced quotes etc. — treat as not-allowlisted
        if not argv:
            return False
        for allowed in self.allowed_commands:
            try:
                prefix = shlex.split(allowed)
            except ValueError:
                continue
            if prefix and argv[: len(prefix)] == prefix:
                return True
        return False
