"""Execution Gateway slices 1–3: deterministic L0–L4 classification, audit levels,
L4 never auto-allowed, and confinement of declared targets at the choke point.

Behavior contract (docs/approval-taxonomy-adr.md):
- fail closed — unclassifiable calls are L4, never "probably fine"
- classification is deterministic and does not depend on model output
"""

from pathlib import Path

from coworker.audit import AuditStore
from coworker.gateway import (
    RiskLevel,
    classify,
    declared_targets,
    enforce_level,
    enforce_scope,
    isolation_status,
    restrict_grants,
)


class Meta:
    def __init__(self, risk_level="low", requires_approval=False, category=""):
        self.risk_level = risk_level
        self.requires_approval = requires_approval
        self.category = category


# -- classification -----------------------------------------------------------

def test_low_metadata_without_approval_is_l0():
    assert classify("read_file", {}, Meta("low")) is RiskLevel.L0


def test_medium_without_approval_is_l2():
    assert classify("mcp_tool", {}, Meta("medium")) is RiskLevel.L2


def test_high_is_l3_even_without_approval():
    # Shell-grade execution: consequential local effects → explicit-grant territory.
    assert classify("run_shell", {"command": "ls"}, Meta("high")) is RiskLevel.L3


def test_approval_required_raises_within_the_same_band():
    assert classify("automation_run", {}, Meta("low", True)) is RiskLevel.L2
    assert classify("gmail_send", {}, Meta("medium", True, "connector")) is RiskLevel.L3


def test_medium_approval_local_category_stays_l2():
    # Local checkpointed writes are consequential-but-local: they ask in interactive
    # modes but must not be treated as external effects (slice 4a depends on this).
    assert classify("write_file", {}, Meta("medium", True, "filesystem")) is RiskLevel.L2
    # Unknown/missing category conservatively stays at L3 (fail closed → ask).
    assert classify("mystery_tool", {}, Meta("medium", True)) is RiskLevel.L3


def test_unknown_risk_value_fails_closed_to_l4():
    assert classify("weird", {}, Meta("critical")) is RiskLevel.L4


def test_missing_metadata_fails_closed_to_l4():
    assert classify("ghost_tool", {}, None) is RiskLevel.L4


def test_irreversible_list_beats_metadata():
    # Even if a future refactor marks send_email "low", the list wins.
    assert classify("send_email", {}, Meta("low")) is RiskLevel.L4


def test_classification_is_deterministic_and_model_blind():
    args_a = {"command": "rm -rf /"}
    args_b = {"query": "harmless"}
    m = Meta("high")
    assert classify("run_shell", args_a, m) == classify("run_shell", args_b, m)


# -- slice 2 policy: L4 is never auto-allowed -----------------------------------

class Decision:
    def __init__(self, allowed=True, rule="send_email → x", needs_user=False, reason="standing rule", grant=""):
        self.allowed = allowed
        self.needs_user = needs_user
        self.rule = rule
        self.reason = reason
        self.grant = grant


def test_l4_downgrades_rule_based_allow_to_human():
    d = enforce_level(RiskLevel.L4, Decision(allowed=True))
    assert not d.allowed and d.needs_user
    assert "L4" in d.reason
    assert d.rule == ""  # the auto-allow citation must not survive


def test_l4_forces_ask_even_without_a_rule():
    # A mode/allowlist allow (no standing rule) on an L4 tool still becomes a prompt.
    d = enforce_level(RiskLevel.L4, Decision(allowed=True, rule="", reason="mode allows"))
    assert not d.allowed and d.needs_user


def test_l4_already_asking_passes_through():
    d = enforce_level(RiskLevel.L4, Decision(allowed=False, needs_user=True))
    assert not d.allowed and d.needs_user


def test_below_l4_is_untouched():
    for level in (RiskLevel.L0, RiskLevel.L1, RiskLevel.L2, RiskLevel.L3):
        original = Decision(allowed=True)
        before = (original.allowed, original.needs_user, original.reason)
        after = enforce_level(level, original)
        assert (after.allowed, after.needs_user, after.reason) == before


# -- slice 4a grant gate: L3+ never rides a blanket or card-minted grant ----------

def test_l3_blanket_mode_grant_is_downgraded_to_ask():
    # Auto mode's "full access" must not release external effects.
    d = restrict_grants(
        RiskLevel.L3, Decision(allowed=True, rule="", reason="full access", grant="blanket")
    )
    assert not d.allowed and d.needs_user
    assert "L3" in d.reason and d.rule == ""


def test_l3_session_card_grant_is_downgraded_to_ask():
    d = restrict_grants(
        RiskLevel.L3,
        Decision(allowed=True, reason="tool allowed for session", grant="session"),
    )
    assert not d.allowed and d.needs_user
    assert "session" in d.reason


def test_l3_policy_grant_passes_through():
    d = restrict_grants(
        RiskLevel.L3,
        Decision(
            allowed=True,
            reason="allowed by standing rule: send_message → slack:chan",
            rule="send_message → slack:chan",
            grant="policy",
        ),
    )
    assert d.allowed and not d.needs_user
    assert d.rule == "send_message → slack:chan"


def test_below_l3_grants_are_untouched():
    for level in (RiskLevel.L0, RiskLevel.L1, RiskLevel.L2):
        for grant in ("blanket", "session", "policy"):
            original = Decision(allowed=True, reason="r", grant=grant)
            before = (original.allowed, original.needs_user, original.reason)
            after = restrict_grants(level, original)
            assert (after.allowed, after.needs_user, after.reason) == before


def test_l4_policy_still_forced_through_slice2_first():
    # Slice 2 outranks everything: even a policy grant cannot auto-allow L4.
    d = enforce_level(RiskLevel.L4, Decision(allowed=True, grant="policy"))
    assert not d.allowed and d.needs_user


def test_denials_pass_the_grant_gate_untouched():
    denied = Decision(allowed=False, needs_user=False, reason="denied by user")
    assert restrict_grants(RiskLevel.L3, denied) is denied


# -- audit persistence ---------------------------------------------------------

def _rows(store):
    store._conn.execute("INSERT INTO audit_events (tool, level) VALUES ('t', 'L3')")
    return store.list()


def test_audit_store_persists_level(tmp_path):
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append({"session_id": "s1", "tool": "run_shell", "stage": "started", "level": "L3"})
    events = store.list(session_id="s1")
    assert len(events) == 1
    assert events[0]["level"] == "L3"


# -- slice 3 resource guard: declared targets stay inside trusted roots ----------

class _Scope:
    """Minimal harness: workspace with a writable root and a read-only root."""

    def __init__(self, tmp_path):
        self.workspace = tmp_path / "ws"
        self.workspace.mkdir()
        self.readonly = tmp_path / "ro"
        self.readonly.mkdir()
        self.roots = [
            (self.workspace.resolve(), True),
            (self.readonly.resolve(), False),
        ]

    def check(self, decision, arguments, level=RiskLevel.L2):
        return enforce_scope(
            decision,
            arguments,
            level,
            workspace_root=self.workspace,
            roots=self.roots,
        )


def test_declared_targets_extracts_every_path_arg_alias():
    assert declared_targets({"path": "a.txt", "file_path": "b.txt"}) == ["a.txt", "b.txt"]
    assert declared_targets({"path": "   "}) == []
    assert declared_targets({"command": "rm -rf /"}) == []
    assert declared_targets(None) == []


def test_scope_lets_in_bounds_writes_through(tmp_path):
    s = _Scope(tmp_path)
    d = s.check(Decision(allowed=True), {"path": str(s.workspace / "out.txt")})
    assert d.allowed and not d.needs_user


def test_scope_ignores_read_only_calls_even_out_of_bounds(tmp_path):
    # L0 reads are untouched: consulting files outside the workspace is legitimate.
    s = _Scope(tmp_path)
    d = s.check(
        Decision(allowed=True), {"path": "C:/elsewhere/x.txt"}, level=RiskLevel.L0
    )
    assert d.allowed and not d.needs_user


def test_scope_downgrades_write_outside_all_roots_to_ask(tmp_path):
    s = _Scope(tmp_path)
    d = s.check(Decision(allowed=True), {"path": str(tmp_path / "elsewhere" / "x.txt")})
    assert not d.allowed and d.needs_user
    assert "outside the trusted directories" in d.reason
    assert d.rule == ""


def test_scope_downgrades_relative_traversal_escape(tmp_path):
    s = _Scope(tmp_path)
    d = s.check(Decision(allowed=True), {"path": "../escape.txt"})
    assert not d.allowed and d.needs_user


def test_scope_downgrades_target_in_read_only_root(tmp_path):
    s = _Scope(tmp_path)
    d = s.check(Decision(allowed=True), {"path": str(s.readonly / "f.txt")})
    assert not d.allowed and d.needs_user
    assert "read-only" in d.reason


def test_scope_keeps_denials_and_ask_decisions_untouched(tmp_path):
    s = _Scope(tmp_path)
    denied = Decision(allowed=False, needs_user=False, reason="denied by user")
    after = s.check(denied, {"path": "C:/nowhere/x.txt"})
    assert after is denied
    asking = Decision(allowed=False, needs_user=True, reason="requires approval")
    assert s.check(asking, {"path": "C:/nowhere/x.txt"}) is asking


def test_scope_passes_calls_without_declared_targets(tmp_path):
    s = _Scope(tmp_path)
    d = s.check(Decision(allowed=True), {"command": "git status"})
    assert d.allowed and not d.needs_user


# -- isolation declaration -------------------------------------------------------

def test_isolation_status_tells_the_truth():
    assert isolation_status(None) == ""
    assert isolation_status(RiskLevel.L0) == "read-only"
    assert isolation_status(RiskLevel.L1) == "checkpoint"
    for level in (RiskLevel.L2, RiskLevel.L3, RiskLevel.L4):
        assert isolation_status(level) == "none"


def test_audit_store_persists_isolation(tmp_path):
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append(
        {
            "session_id": "s1",
            "tool": "write_file",
            "stage": "started",
            "level": "L1",
            "isolation": "checkpoint",
        }
    )
    events = store.list(session_id="s1")
    assert len(events) == 1
    assert events[0]["isolation"] == "checkpoint"
