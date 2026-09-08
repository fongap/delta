"""Task identity authority path — architecture guard test (ADR-024).
 
Verifies the R1 Task Identity hard-cut: Rust is the sole authority,
no delegate wrapper, no authority selector, no Python SQLite writer.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_legacy_taskstore_delegate_is_deleted():
    """core/automation/store_delegate.py must not exist after ADR-024 hard-cut."""
    assert not (REPO / "core" / "automation" / "store_delegate.py").exists()


def test_taskstore_delegate_test_file_deleted():
    """tests/test_taskstore_delegate.py must not exist after ADR-024 hard-cut."""
    assert not (REPO / "tests" / "test_taskstore_delegate.py").exists()


def test_taskstore_facade_has_no_python_writer_or_switch():
    """core/automation/store.py must be a thin Rust facade with no Python SQLite
    writer, no authority selector, and no DELTA_RUST_AUTHORITY reference."""
    source = (REPO / "core" / "automation" / "store.py").read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert "is_rust_authority" not in source
    assert "DELTA_RUST_AUTHORITY" not in source
    assert "default_client().command" in source


def test_production_constructs_taskstore_facade_directly():
    """manager.py must construct TaskStore directly, not via delegate."""
    source = (REPO / "services" / "server" / "manager.py").read_text(encoding="utf-8")
    assert "from core.automation import" in source and "TaskStore" in source
    assert "store_delegate" not in source
    assert "maybe_wrap_taskstore" not in source
    assert "TaskStoreWithDelegate" not in source


def test_no_production_file_imports_deleted_taskstore_delegate():
    """No production file should import the deleted store_delegate module."""
    violations = []
    for root in (REPO / "core", REPO / "services"):
        for path in root.rglob("*.py"):
            if "core.automation.store_delegate" in path.read_text(encoding="utf-8"):
                violations.append(path.relative_to(REPO).as_posix())
    assert not violations, "deleted delegate imported by: " + ", ".join(violations)


def test_task_identity_not_in_rust_write_domains():
    """task_identity must not be in RUST_WRITE_DOMAINS — it's a hard-cut facade."""
    from packages.storage_authority import RUST_WRITE_DOMAINS
    assert "task_identity" not in RUST_WRITE_DOMAINS