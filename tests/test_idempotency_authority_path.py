"""Architecture guard for the R1 Idempotency hard cut (ADR-022)."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_legacy_idempotency_delegate_is_deleted():
    assert not (REPO / "core" / "idemlog_delegate.py").exists()


def test_idempotency_facade_has_no_python_writer_or_switch():
    source = (REPO / "core" / "idemlog.py").read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert "is_rust_authority" not in source
    assert "DELTA_RUST_AUTHORITY" not in source
    assert "default_client().command" in source


def test_production_constructs_rust_facade_directly():
    source = (REPO / "services" / "server" / "manager.py").read_text(encoding="utf-8")
    assert "from core.idemlog import IdempotencyLog" in source
    assert 'self.idem_log = IdempotencyLog(base / "side-effects.db")' in source
    assert "idemlog_delegate" not in source


def test_no_production_file_imports_deleted_delegate():
    violations = []
    for root in (REPO / "core", REPO / "services"):
        for path in root.rglob("*.py"):
            if "core.idemlog_delegate" in path.read_text(encoding="utf-8"):
                violations.append(path.relative_to(REPO).as_posix())
    assert not violations, "deleted delegate imported by: " + ", ".join(violations)


def test_idempotency_has_no_shadow_sqlite_writer():
    """No production file should create a direct sqlite3 connection
    for the idempotency domain (side_effects.db). Test fixtures that
    create legacy databases for migration testing are allowed."""
    violations = []
    idem_db_patterns = ("side_effects.db", "side-effects.db")
    for root in (REPO / "core", REPO / "services"):
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "sqlite3" in source and any(p in source for p in idem_db_patterns):
                violations.append(path.relative_to(REPO).as_posix())
    assert not violations, (
        "direct sqlite3 for idempotency found in production: " + ", ".join(violations)
    )


def test_idemlog_delegate_test_file_deleted():
    assert not (REPO / "tests" / "test_idemlog_delegate.py").exists()


def test_tool_lifecycle_plan_is_rust_disposition():
    """ADR-035: the execution disposition (execute/replay/uncertain) and its
    paired record_planned + mark_executing transitions live behind a single
    Rust ``toollifecycle.plan`` call, not Python-side orchestration."""
    source = (REPO / "core" / "tool_lifecycle.py").read_text(encoding="utf-8")
    assert ".plan(" in source
    # The pre-execution state machine is no longer driven step-by-step in Python;
    # only description text may mention the transition names, never a call site.
    assert ".record_planned(" not in source
    assert ".mark_executing(" not in source
    assert ".lookup(" not in source
