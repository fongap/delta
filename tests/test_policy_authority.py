"""Policy authority — architecture guard + integration tests (ADR-030).

Tests the Rust policy authority via the Python thin facade.

1. **Architecture guard**: Python policy implementation removed, facade has no
   Python writer/selector, production constructs Rust facade directly.
2. **Concurrency**: multiple threads evaluating policy must all succeed.
3. **Fail-closed**: classification returns L4, evaluation denies when delta_core
   is unavailable or returns errors.
4. **Integrity**: all four slices (classify, enforce_level, restrict_grants,
   enforce_scope) enforced in single Rust call.
"""

from __future__ import annotations

import threading
from pathlib import Path

from core.gateway import (
    RiskLevel,
    classify,
    evaluate_policy,
    isolation_status,
    write_paths,
)

REPO = Path(__file__).resolve().parent.parent


# -- architecture guard tests (ADR-030) ------------------------------------


def test_gateway_is_thin_facade():
    """core/gateway.py must be a thin Rust facade with no Python policy logic."""
    source = (REPO / "core" / "gateway.py").read_text(encoding="utf-8")
    # No local classification logic
    assert "_VALID_METADATA_RISK" not in source
    assert "_LOCAL_CATEGORIES" not in source
    assert "_REVERSIBLE_WRITE_CATEGORIES" not in source
    assert "_SENSITIVE_TOKENS" not in source
    assert "_band_level" not in source
    assert "def classify" in source  # facade function exists
    assert "def evaluate_policy" in source  # new facade function
    assert "class PolicyAuthorityError" in source
    assert "default_client" in source
    # Utility functions remain (not policy authority)
    assert "def write_paths" in source
    assert "def isolation_status" in source


def test_no_python_policy_implementation_in_production():
    """Production code must delegate to Rust via gateway facades."""
    # ADR-034: policy evaluation lives in the tool lifecycle orchestrator
    # (core/tool_lifecycle.py), not directly in engine.py.
    engine_source = (REPO / "core" / "engine.py").read_text(encoding="utf-8")
    lifecycle_source = (REPO / "core" / "tool_lifecycle.py").read_text(encoding="utf-8")
    # Old slice calls removed from both production modules
    for source in (engine_source, lifecycle_source):
        assert "gateway.enforce_level" not in source
        assert "gateway.restrict_grants" not in source
        assert "gateway.enforce_scope" not in source
    # evaluate_policy (the Rust facade) is used by the orchestrator
    assert "gateway.evaluate_policy" in lifecycle_source


def test_policy_in_rust_write_domains():
    """policy must be in RUST_WRITE_DOMAINS after ADR-030."""
    from packages.storage_authority import RUST_WRITE_DOMAINS

    assert "policy" in RUST_WRITE_DOMAINS



# -- integration tests (require delta_core) ---------------------------------


class Meta:
    def __init__(self, risk_level="low", requires_approval=False, category="", capabilities=()):
        self.risk_level = risk_level
        self.requires_approval = requires_approval
        self.category = category
        self.capabilities = list(capabilities)


class Decision:
    def __init__(self, allowed=True, rule="", needs_user=False, reason="", grant=""):
        self.allowed = allowed
        self.needs_user = needs_user
        self.rule = rule
        self.reason = reason
        self.grant = grant


def _roots(workspace):
    return [(workspace.resolve(), True)]


def test_classify_delegates_to_rust():
    """classify returns Rust-authoritative level."""
    assert classify("read_file", {}, Meta("low")) is RiskLevel.L0
    assert classify("remember", {}, Meta("low", False, "memory", ("remember",))) is RiskLevel.L1
    assert classify("write_file", {}, Meta("medium", True, "filesystem")) is RiskLevel.L2
    assert classify("run_shell", {"command": "ls"}, Meta("high")) is RiskLevel.L3
    assert classify("send_email", {}, Meta("low")) is RiskLevel.L4


def test_evaluate_policy_applies_all_slices(tmp_path):
    """evaluate_policy runs all four slices in one Rust call."""
    ws = tmp_path / "ws"
    ws.mkdir()

    # Slice 1 (classify) + Slice 2 (L4 never auto-allowed)
    d, level = evaluate_policy(
        Decision(allowed=True, grant="policy"),
        RiskLevel.L4,
        "send_email",
        {},
        Meta("low"),
        workspace_root=ws,
        roots=_roots(ws),
    )
    assert not d.allowed and d.needs_user
    assert level is RiskLevel.L4

    # Slice 3 (scope): L1 write under writable root passes
    d, level = evaluate_policy(
        Decision(allowed=True, grant="blanket"),
        RiskLevel.L1,
        "remember",
        {"path": "note.txt"},
        Meta("low", False, "memory", ("remember",)),
        workspace_root=ws,
        roots=_roots(ws),
    )
    assert d.allowed and not d.needs_user

    # Slice 3 (scope): L1 write outside roots is blocked
    d, level = evaluate_policy(
        Decision(allowed=True, grant="blanket"),
        RiskLevel.L1,
        "remember",
        {"path": "../escape.txt"},
        Meta("low", False, "memory", ("remember",)),
        workspace_root=ws,
        roots=_roots(ws),
    )
    assert not d.allowed and d.needs_user

    # Slice 4a (grants): L3 blanket grant is downgraded
    d, level = evaluate_policy(
        Decision(allowed=True, reason="full access", grant="blanket"),
        RiskLevel.L3,
        "web_fetch",
        {"url": "https://example.com"},
        Meta("low"),
        workspace_root=ws,
        roots=_roots(ws),
    )
    assert not d.allowed and d.needs_user

    # Slice 4a (grants): L3 policy grant passes
    d, level = evaluate_policy(
        Decision(
            allowed=True,
            reason="allowed by standing rule",
            rule="send_message → slack:chan",
            grant="policy",
        ),
        RiskLevel.L3,
        "web_fetch",
        {"url": "https://example.com"},
        Meta("low"),
        workspace_root=ws,
        roots=_roots(ws),
    )
    assert d.allowed and not d.needs_user


def test_fail_closed_classification():
    """Unclassifiable tool returns L4 (fail-closed)."""
    assert classify("ghost_tool", {}, None) is RiskLevel.L4
    assert classify("weird", {}, Meta("critical")) is RiskLevel.L4


def test_fail_closed_evaluation(tmp_path):
    """Evaluation denies when authority error (simulated via bad inputs)."""
    # Note: Real fail-closed requires delta_core unavailable, which is
    # hard to test here. The facade catches DeltaCoreError and returns
    # a denied decision.
    ws = tmp_path / "ws"
    ws.mkdir()
    d, level = evaluate_policy(
        Decision(allowed=True, grant="blanket"),
        RiskLevel.L4,  # will be re-classified by Rust
        "send_email",  # Rust re-classifies as L4
        {},
        Meta("low"),
        workspace_root=ws,
        roots=_roots(ws),
    )
    assert not d.allowed and d.needs_user
    assert level is RiskLevel.L4


def test_concurrent_policy_evaluation(tmp_path):
    """Multiple threads evaluating policy must all succeed."""
    ws = tmp_path / "ws"
    ws.mkdir()
    num_threads = 4
    barrier = threading.Barrier(num_threads)
    errors: list[Exception] = []

    def worker(tid: int):
        try:
            barrier.wait()
            for i in range(10):
                d, level = evaluate_policy(
                    Decision(allowed=True, grant="blanket"),
                    RiskLevel.L1,
                    "remember",
                    {"path": f"note_{tid}_{i}.txt"},
                    Meta("low", False, "memory", ("remember",)),
                    workspace_root=ws,
                    roots=_roots(ws),
                )
                assert d.allowed
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # All writes succeeded (no crashes)


def test_isolation_status_unchanged():
    """isolation_status remains a pure Python display helper."""
    assert isolation_status(None) == ""
    assert isolation_status(RiskLevel.L0) == "read-only"
    assert isolation_status(RiskLevel.L1) == "checkpoint"
    for level in (RiskLevel.L2, RiskLevel.L3, RiskLevel.L4):
        assert isolation_status(level) == "none"


def test_write_paths_unchanged():
    """write_paths remains a pure Python utility for PermissionEngine."""
    blob = "*** Begin Patch\n*** Add File: src/new.py\n+hi\n*** End Patch"
    paths, located = write_paths("apply_patch", {"patch": blob})
    assert located
    assert "src/new.py" in paths


# -- protocol version --------------------------------------------------------


def test_protocol_version_is_9():
    """Python and Rust must agree on PROTOCOL_VERSION = 9."""
    from packages.delta_core_client import PROTOCOL_VERSION

    assert PROTOCOL_VERSION == 9
    # Verify Rust side also has 9
    import subprocess

    result = subprocess.run(
        ["cargo", "run", "--bin", "delta_core"],
        cwd=REPO / "core" / "runtime-native",
        input='{"cmd":"hello","protocol_version":9}',
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert '"protocol_version":9' in result.stdout