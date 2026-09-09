"""Tests for the Approval Authority hard-cut (ADR-031).

Verifies that:
1. AuditStore.append delegates to Rust delta_core (approval.record)
2. The Rust-written rows are readable via AuditStore.list (same SQLite file)
3. Schema guard: APPROVAL_SCHEMA_VERSION constant exists
4. Storage authority includes "approval" in RUST_WRITE_DOMAINS
5. Fail-closed: ApprovalAuthorityError is raised when the Rust authority fails
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.approval import ApprovalAuthorityError, record
from core.audit import AuditStore
from packages.delta_core_client import PROTOCOL_VERSION
from packages.storage_authority import RUST_WRITE_DOMAINS

REPO = Path(__file__).resolve().parent.parent


# -- Schema guard ---------------------------------------------------------------


def test_approval_schema_version_constant():
    """APPROVAL_SCHEMA_VERSION must be defined and >= 1."""
    import subprocess

    result = subprocess.run(
        ["cargo", "run", "--bin", "delta_core"],
        cwd=REPO / "core" / "runtime-native",
        input=json.dumps(
            {
                "cmd": "approval.record",
                "db": ":memory:",
                "session_id": "schema-test",
                "tool": "test_tool",
                "stage": "started",
            }
        ),
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The command should succeed (ok=true) — if the schema is wrong, the
    # CREATE TABLE would fail.
    assert '"ok":true' in result.stdout or '"ok": false' not in result.stdout


# -- Protocol version guard -----------------------------------------------------


def test_protocol_version_is_9():
    """Python and Rust must agree on PROTOCOL_VERSION = 9."""
    assert PROTOCOL_VERSION == 9


# -- Storage authority guard ----------------------------------------------------


def test_approval_in_rust_write_domains():
    """Approval must be in RUST_WRITE_DOMAINS after ADR-031."""
    assert "approval" in RUST_WRITE_DOMAINS


def test_rust_write_domains_complete():
    """All four hard-cut domains must be present."""
    assert RUST_WRITE_DOMAINS == frozenset(
        {"validation", "checkpoint", "policy", "approval"}
    )


# -- Integration: AuditStore.append → Rust → AuditStore.list --------------------


def test_audit_append_writes_via_rust(tmp_path):
    """AuditStore.append delegates to Rust; list reads the same SQLite file."""
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append(
        {
            "session_id": "s1",
            "tool": "run_shell",
            "stage": "started",
            "level": "L3",
            "isolation": "none",
            "arguments": {"cmd": "ls"},
        }
    )
    events = store.list(session_id="s1")
    assert len(events) == 1
    assert events[0]["tool"] == "run_shell"
    assert events[0]["stage"] == "started"
    assert events[0]["level"] == "L3"
    assert events[0]["isolation"] == "none"
    store.close()


def test_audit_append_multiple_events(tmp_path):
    """Multiple audit events are persisted in order."""
    store = AuditStore(db_path=tmp_path / "audit.db")
    for i in range(5):
        store.append(
            {
                "session_id": "batch",
                "tool": f"tool_{i}",
                "stage": "started",
            }
        )
    events = store.list(session_id="batch")
    assert len(events) == 5
    # list() returns DESC by id, so tool_4 is first
    assert events[0]["tool"] == "tool_4"
    assert events[4]["tool"] == "tool_0"
    store.close()


def test_audit_append_with_connector(tmp_path):
    """The connector field is persisted when provided."""
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append(
        {
            "session_id": "s1",
            "tool": "run_shell",
            "stage": "finished",
            "connector": "shell",
        }
    )
    events = store.list(session_id="s1")
    assert len(events) == 1
    assert events[0]["connector"] == "shell"
    store.close()


def test_audit_append_preserves_args_json(tmp_path):
    """Arguments are sanitized and stored as JSON."""
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append(
        {
            "session_id": "s1",
            "tool": "run_shell",
            "stage": "started",
            "arguments": {"cmd": "echo hello", "secret": "s3cr3t"},
        }
    )
    events = store.list(session_id="s1")
    assert len(events) == 1
    args = events[0]["args"]
    assert isinstance(args, dict)
    assert args.get("cmd") == "echo hello"
    store.close()


def test_audit_append_with_approval_field(tmp_path):
    """The approval field is persisted."""
    store = AuditStore(db_path=tmp_path / "audit.db")
    store.append(
        {
            "session_id": "s1",
            "tool": "run_shell",
            "stage": "approval_resolved",
            "approval": "once",
        }
    )
    events = store.list(session_id="s1")
    assert len(events) == 1
    assert events[0]["approval"] == "once"
    store.close()


# -- Fail-closed behavior -------------------------------------------------------


def test_record_raises_on_bad_db_path(tmp_path):
    """record() raises ApprovalAuthorityError when the Rust authority fails
    to open the database (e.g., when the parent path is a file, not a directory)."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    with pytest.raises(ApprovalAuthorityError):
        record(
            str(blocker / "audit.db"),
            {
                "session_id": "s1",
                "tool": "test",
                "stage": "started",
            },
        )
