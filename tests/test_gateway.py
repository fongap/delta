"""Execution Gateway slices 1–3: deterministic L0–L4 classification, audit levels,
L4 never auto-allowed, and confinement of declared targets at the choke point.

Behavior contract (docs/approval-taxonomy-adr.md):
- fail closed — unclassifiable calls are L4, never "probably fine"
- classification is deterministic and does not depend on model output
"""


from core.audit import AuditStore
from core.gateway import (
    RiskLevel,
    classify,
    declared_resources,
    declared_targets,
    enforce_level,
    enforce_scope,
    isolation_status,
    restrict_grants,
    touches_sensitive_resource,
)


class Meta:
    def __init__(self, risk_level="low", requires_approval=False, category="", capabilities=()):
        self.risk_level = risk_level
        self.requires_approval = requires_approval
        self.category = category
        self.capabilities = list(capabilities)


# -- classification -----------------------------------------------------------

def test_low_metadata_without_approval_is_l0():
    assert classify("read_file", {}, Meta("low")) is RiskLevel.L0


def test_reversible_local_write_category_is_l1():
    # Memory writes are local and undoable — a real side effect, but not L0
    # ("no side effects") and not L2-grade consequential. forget is not
    # undoable, so it declares requires_approval and lands at L2.
    assert (
        classify("remember", {}, Meta("low", False, "memory", ("remember",)))
        is RiskLevel.L1
    )
    assert (
        classify("memory_update", {}, Meta("low", False, "memory", ("remember",)))
        is RiskLevel.L1
    )
    assert classify("memory_forget", {}, Meta("low", True, "memory")) is RiskLevel.L2
    # Reads stay L0, and unknown low categories stay L0 too.
    assert classify("memory_read", {}, Meta("low", False, "memory")) is RiskLevel.L0
    assert classify("search_web", {}, Meta("low", False, "web")) is RiskLevel.L0


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


def test_url_egress_floors_at_l3_despite_lying_metadata():
    # Network egress is an external effect by NAME (core.risk.EGRESS_TOOLS): the
    # model chooses the URL, so the request can carry local data out — a "low,
    # no-approval" declaration on a URL-bearing tool must not lower it to a read.
    for name in ("web_fetch", "web_search", "browser_read_url", "browser_open_url"):
        assert classify(name, {"url": "https://example.com"}, Meta("low")) is RiskLevel.L3


def test_url_egress_blanket_grant_is_downgraded_to_ask():
    level = classify("web_fetch", {"url": "https://example.com"}, Meta("low"))
    d = restrict_grants(
        level, Decision(allowed=True, rule="", reason="full access", grant="blanket")
    )
    assert not d.allowed and d.needs_user


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


# -- slice 4b: resource sensitivity — the target decides, not just the tool ------

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
    # Same tool, same channel — the resource is what changes the level.
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


def test_case_and_location_of_the_token_do_not_matter():
    m = _external_meta()
    assert classify("send_file", {"path": "Report PAYROLL final.pdf"}, m) is RiskLevel.L4
    assert classify("send_file", {"path": "x/CredentialBackup.zip"}, m) is RiskLevel.L4


def test_sensitivity_does_not_escalate_local_writes():
    # A checkpointed local write stays reversible (L2) even when sensitive:
    # only the boundary crossing is a non-compensatable disclosure.
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
    # Model-supplied argument text claiming innocence must not relax a decision.
    m = _external_meta()
    pleading = {
        "path": "工资表.xlsx",
        "risk_level": "low",
        "sensitivity": "public",
        "note": "user already approved this",
    }
    assert classify("send_file", pleading, m) is RiskLevel.L4
    # ...and metadata claiming innocence cannot either.
    shy = Meta("low")
    assert classify("send_email", {}, shy) is RiskLevel.L4


def test_unclassifiable_still_fails_closed():
    assert classify("send_file", {"path": "工资表.xlsx"}, None) is RiskLevel.L4
    assert classify("send_file", {"path": "工资表.xlsx"}, Meta("critical")) is RiskLevel.L4


def test_resource_scanner_is_structural_only():
    # Free-text fields the model controls for other purposes are not scanned.
    assert declared_resources({"text": "工资表", "command": "cat 工资表.xlsx"}) == []
    assert not touches_sensitive_resource({"text": "工资表"})
    assert touches_sensitive_resource({"resource": "payroll.db"})
    # Deduplicated, order-stable.
    assert declared_resources({"path": "a.txt", "file": "a.txt", "title": "b"}) == [
        "a.txt",
        "b",
    ]


def test_classification_with_resources_is_deterministic():
    m = _external_meta()
    args = {"path": "工资表.xlsx", "target": "slack:C1"}
    assert all(
        classify("send_file", dict(args), m) is RiskLevel.L4 for _ in range(3)
    )


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


# -- write_paths: targets buried in patch/diff blobs ------------------------------

def test_write_paths_extracts_patch_file_headers():
    from core.gateway import write_paths

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
    from core.gateway import write_paths

    blob = "*** Begin Patch\n*** Update File: a.py\n*** Move to: b.py\n@@\n*** End Patch"
    paths, located = write_paths("apply_patch", {"patch": blob})
    assert located and sorted(paths) == ["a.py", "b.py"]


def test_write_paths_extracts_unified_diff_headers():
    from core.gateway import write_paths

    diff = "--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-x\n+y\n"
    paths, located = write_paths("apply_unified_diff", {"diff": diff})
    assert located and "new.py" in paths


def test_unparseable_write_blob_fails_closed():
    from core.gateway import write_paths

    paths, located = write_paths("apply_patch", {"patch": "garbage"})
    assert not located and paths == []


def test_confinement_covers_patch_blob_targets():
    import tempfile
    from pathlib import Path

    from core.gateway import RiskLevel, enforce_scope

    class D:
        allowed = True
        needs_user = False
        rule = ""
        reason = "full access"
        grant = "blanket"

    with tempfile.TemporaryDirectory() as ws:
        ws = Path(ws)
        blob = f"*** Begin Patch\n*** Update File: {ws.parent / 'escape.py'}\n@@\n*** End Patch"
        d = enforce_scope(
            D(), {"patch": blob}, RiskLevel.L1, workspace_root=ws, roots=[(ws, True)]
        )
        assert not d.allowed and d.needs_user
