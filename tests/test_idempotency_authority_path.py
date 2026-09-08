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
