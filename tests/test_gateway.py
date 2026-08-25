"""Execution Gateway slice 1: deterministic L0–L4 classification + audit levels.

Behavior contract (docs/approval-taxonomy-adr.md):
- fail closed — unclassifiable calls are L4, never "probably fine"
- classification is deterministic and does not depend on model output
- slice 1 changes NO allow/deny behavior; it only labels
"""

from coworker.audit import AuditStore
from coworker.gateway import RiskLevel, classify, enforce_level


class Meta:
    def __init__(self, risk_level="low", requires_approval=False):
        self.risk_level = risk_level
        self.requires_approval = requires_approval


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
    assert classify("gmail_send", {}, Meta("medium", True)) is RiskLevel.L3


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
    def __init__(self, allowed=True, rule="send_email → x", needs_user=False, reason="standing rule"):
        self.allowed = allowed
        self.needs_user = needs_user
        self.rule = rule
        self.reason = reason


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
