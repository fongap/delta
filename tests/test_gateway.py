"""Execution Gateway — Rust-authoritative policy evaluation (ADR-030).

Tests the thin Python facade over Rust ``delta_core`` policy authority.
Classification and policy enforcement are delegated to Rust; ``write_paths``
and ``isolation_status`` remain pure Python utilities.

Behavior contract (docs/architecture/adr/ADR-002-approval-taxonomy.md):
- fail closed — unclassifiable calls are L4, never "probably fine"
- classification is deterministic and does not depend on model output
"""

from core.audit import AuditStore
from core.gateway import (
    RiskLevel,
    classify,
    evaluate_policy,
    isolation_status,
    write_paths,
)


class Meta:
    def __init__(self, risk_level="low", requires_approval=False, category="", capabilities=()):
        self.risk_level = risk_level
        self.requires_approval = requires_approval
        self.category = category
        self.capabilities = list(capabilities)


class Decision:
    def __init__(self, allowed=True, rule="send_email → x", needs_user=False, reason="standing rule", grant=""):
        self.allowed = allowed
        self.needs_user = needs_user
        self.rule = rule
        self.reason = reason
        self.grant = grant


def _roots(workspace):
    return [(workspace.resolve(), True)]


# -- classification -----------------------------------------------------------

def test_low_metadata_without_approval_is_l0():
    assert classify("read_file", {}, Meta("low")) is RiskLevel.L0


def test_reversible_local_write_category_is_l1():
    assert (
        classify("remember", {}, Meta("low", False, "memory", ("remember",)))
        is RiskLevel.L1
    )
    assert classify("memory_forget", {}, Meta("low", True, "memory")) is RiskLevel.L2
    assert classify("memory_read", {}, Meta("low", False, "memory")) is RiskLevel.L0


def test_medium_without_approval_is_l2():
    assert classify("mcp_tool", {}, Meta("medium")) is RiskLevel.L2


def test_high_is_l3_even_without_approval():
    assert classify("run_shell", {"command": "ls"}, Meta("high")) is RiskLevel.L3


def test_approval_required_raises_within_the_same_band():
    assert classify("automation_run", {}, Meta("low", True)) is RiskLevel.L2
    assert classify("gmail_send", {}, Meta("medium", True, "connector")) is RiskLevel.L3


def test_medium_approval_local_category_stays_l2():
    assert classify("write_file", {}, Meta("medium", True, "filesystem")) is RiskLevel.L2
    assert classify("mystery_tool", {}, Meta("medium", True)) is RiskLevel.L3


def test_unknown_risk_value_fails_closed_to_l4():
    assert classify("weird", {}, Meta("critical")) is RiskLevel.L4


def test_missing_metadata_fails_closed_to_l4():
    assert classify("ghost_tool", {}, None) is RiskLevel.L4


def test_irreversible_list_beats_metadata():
    assert classify("send_email", {}, Meta("low")) is RiskLevel.L4


def test_url_egress_floors_at_l3_despite_lying_metadata():
    for name in ("web_fetch", "web_search", "browser_read_url", "browser_open_url"):
        assert classify(name, {"url": "https://example.com"}, Meta("low")) is RiskLevel.L3


def test_classification_is_deterministic_and_model_blind():
    args_a = {"command": "rm -rf /"}
    args_b = {"query": "harmless"}
    m = Meta("high")
    assert classify("run_shell", args_a, m) == classify("run_shell", args_b, m)


# -- slice 4b: resource sensitivity -------------------------------------------

def _external_meta():
    return Meta("medium", True, "messaging")


def test_benign_external_send_stays_l3():
    assert (
        classify(
            "send_file",
            {"path": "charts/临时图表.png", "target": "slack:C0123"},
            _external_meta(),
        )
        is RiskLevel.L3
    )


def test_sensitive_external_send_escalates_to_l4():
    assert (
        classify(
            "send_file",
            {"path": "报表/工资表.xlsx", "target": "slack:C0123"},
            _external_meta(),
        )
        is RiskLevel.L4
    )


def test_sensitivity_matches_the_whole_path_and_every_alias():
    m = _external_meta()
    assert classify("send_file", {"path": "hr/salary/aug.png"}, m) is RiskLevel.L4
    assert classify("send_file", {"title": "8月工资表"}, m) is RiskLevel.L4
    assert classify("send_file", {"attachments": "id_rsa_backup"}, m) is RiskLevel.L4
    assert classify("send_file", {"path": "notes/.env"}, m) is RiskLevel.L4


def test_sensitivity_does_not_escalate_local_writes():
    local = Meta("medium", True, "filesystem")
    assert classify("write_file", {"path": "工资表.xlsx"}, local) is RiskLevel.L2


def test_read_only_calls_are_never_escalated():
    low = Meta("low")
    assert classify("read_file", {"path": "工资表.xlsx"}, low) is RiskLevel.L0


def test_irreversible_list_still_outranks_everything():
    assert classify("send_email", {"path": "临时图表.png"}, _external_meta()) is (
        RiskLevel.L4
    )


def test_model_cannot_self_classify_downward():
    m = _external_meta()
    pleading = {
        "path": "工资表.xlsx",
        "risk_level": "low",
        "sensitivity": "public",
        "note": "user already approved this",
    }
    assert classify("send_file", pleading, m) is RiskLevel.L4
    shy = Meta("low")
    assert classify("send_email", {}, shy) is RiskLevel.L4


def test_unclassifiable_still_fails_closed():
    assert classify("send_file", {"path": "工资表.xlsx"}, None) is RiskLevel.L4
    assert classify("send_file", {"path": "工资表.xlsx"}, Meta("critical")) is RiskLevel.L4


# -- evaluate_policy: all four slices in one Rust call ------------------------

def test_l4_downgrades_rule_based_allow_to_human(tmp_path):
    d, level = evaluate_policy(
        Decision(allowed=True, grant="policy"),
        RiskLevel.L4,
        "send_email",
        {},
        Meta("low"),
        workspace_root=tmp_path,
        roots=_roots(tmp_path),
    )
    assert not d.allowed and d.needs_user
    assert "L4" in d.reason
    assert d.rule == ""


def test_l3_blanket_grant_is_downgraded_to_ask(tmp_path):
    d, level = evaluate_policy(
        Decision(allowed=True, reason="full access", grant="blanket"),
        RiskLevel.L3,
        "web_fetch",
        {"url": "https://example.com"},
        Meta("low"),
        workspace_root=tmp_path,
        roots=_roots(tmp_path),
    )
    assert not d.allowed and d.needs_user


def test_l3_policy_grant_passes(tmp_path):
    d, level = evaluate_policy(
        Decision(
            allowed=True,
            reason="allowed by standing rule: send_message → slack:chan",
            rule="send_message → slack:chan",
            grant="policy",
        ),
        RiskLevel.L3,
        "web_fetch",
        {"url": "https://example.com"},
        Meta("low"),
        workspace_root=tmp_path,
        roots=_roots(tmp_path),
    )
    assert d.allowed and not d.needs_user


def test_l2_write_under_writable_root_passes(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    d, level = evaluate_policy(
        Decision(allowed=True, grant="blanket"),
        RiskLevel.L2,
        "write_file",
        {"path": "out.txt"},  # relative path
        Meta("medium", False, "filesystem"),
        workspace_root=ws,
        roots=[(ws.resolve(), True)],
    )
    assert d.allowed and not d.needs_user


def test_l1_write_outside_roots_is_blocked(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    d, level = evaluate_policy(
        Decision(allowed=True, grant="blanket"),
        RiskLevel.L1,
        "remember",
        {"path": "../escape.txt"},  # relative path going outside
        Meta("low", False, "memory", ("remember",)),
        workspace_root=ws,
        roots=[(ws.resolve(), True)],
    )
    assert not d.allowed and d.needs_user


def test_l0_read_passes_even_outside_roots(tmp_path):
    d, level = evaluate_policy(
        Decision(allowed=True, grant="blanket"),
        RiskLevel.L0,
        "read_file",
        {"path": str(tmp_path / "elsewhere.txt")},
        Meta("low"),
        workspace_root=tmp_path,
        roots=_roots(tmp_path),
    )
    assert d.allowed and not d.needs_user


# -- isolation declaration -------------------------------------------------------

def test_isolation_status_tells_the_truth():
    assert isolation_status(None) == ""
    assert isolation_status(RiskLevel.L0) == "read-only"
    assert isolation_status(RiskLevel.L1) == "checkpoint"
    for level in (RiskLevel.L2, RiskLevel.L3, RiskLevel.L4):
        assert isolation_status(level) == "none"


# -- audit persistence ---------------------------------------------------------

def test_audit_store_persists_level(tmp_path):
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append({"session_id": "s1", "tool": "run_shell", "stage": "started", "level": "L3"})
    events = store.list(session_id="s1")
    assert len(events) == 1
    assert events[0]["level"] == "L3"


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


# -- write_paths: targets buried in patch/diff blobs ------------------------------

def test_write_paths_extracts_patch_file_headers():
    blob = (
        "*** Begin Patch\n"
        "*** Add File: src/new.py\n+hi\n"
        "*** Update File: src/old.py\n@@\n"
        "*** Delete File: src/gone.py\n"
        "*** End Patch"
    )
    paths, located = write_paths("apply_patch", {"patch": blob})
    assert located
    assert sorted(paths) == ["src/gone.py", "src/new.py", "src/old.py"]


def test_write_paths_extracts_patch_rename_target():
    blob = "*** Begin Patch\n*** Update File: a.py\n*** Move to: b.py\n@@\n*** End Patch"
    paths, located = write_paths("apply_patch", {"patch": blob})
    assert located and sorted(paths) == ["a.py", "b.py"]


def test_write_paths_extracts_unified_diff_headers():
    diff = "--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-x\n+y\n"
    paths, located = write_paths("apply_unified_diff", {"diff": diff})
    assert located and "new.py" in paths


def test_unparseable_write_blob_fails_closed():
    paths, located = write_paths("apply_patch", {"patch": "garbage"})
    assert not located and paths == []
