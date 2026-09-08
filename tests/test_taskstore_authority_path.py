"""Architecture guard tests for R1 Task Identity Hard-Cut (ADR-024).

Verifies that the hard-cut invariants hold at the file/import level:
- store_delegate.py is deleted
- test_taskstore_delegate.py is deleted
- store.py has no Python writer, switch, or is_rust_authority
- manager.py has no maybe_wrap_taskstore
- No production file imports deleted delegate
- task_identity is not in RUST_WRITE_DOMAINS
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Patterns that must NOT appear in production files
NO_SQLITE3 = re.compile(r"import\s+sqlite3|from\s+sqlite3\s+import")
NO_IS_RUST_AUTHORITY = re.compile(r"is_rust_authority")
NO_DELTA_RUST_AUTHORITY = re.compile(r"DELTA_RUST_AUTHORITY")
NO_MAYBE_WRAP = re.compile(r"maybe_wrap_taskstore")
NO_TASK_STORE_WITH_DELEGATE = re.compile(r"TaskStoreWithDelegate")
NO_STORE_DELEGATE_IMPORT = re.compile(
    r"from\s+core\.automation\.store_delegate\s+import"
)


def test_legacy_taskstore_delegate_is_deleted():
    """store_delegate.py must not exist."""
    assert not (REPO / "core" / "automation" / "store_delegate.py").exists()


def test_taskstore_delegate_test_file_deleted():
    """test_taskstore_delegate.py must not exist."""
    assert not (REPO / "tests" / "test_taskstore_delegate.py").exists()


def test_taskstore_facade_has_no_python_writer_or_switch():
    """store.py must not contain sqlite3, is_rust_authority, or DELTA_RUST_AUTHORITY."""
    store_py = REPO / "core" / "automation" / "store.py"
    text = store_py.read_text(encoding="utf-8")
    assert not NO_SQLITE3.search(text), "store.py imports sqlite3"
    assert not NO_IS_RUST_AUTHORITY.search(text), "store.py references is_rust_authority"
    assert not NO_DELTA_RUST_AUTHORITY.search(text), "store.py references DELTA_RUST_AUTHORITY"


def test_production_constructs_taskstore_facade_directly():
    """manager.py must construct TaskStore directly, not via maybe_wrap_taskstore."""
    manager_py = REPO / "services" / "server" / "manager.py"
    text = manager_py.read_text(encoding="utf-8")
    assert not NO_MAYBE_WRAP.search(text), "manager.py uses maybe_wrap_taskstore"
    assert not NO_STORE_DELEGATE_IMPORT.search(text), (
        "manager.py imports from store_delegate"
    )


def test_no_production_file_imports_deleted_taskstore_delegate():
    """No production file should import the deleted store_delegate module."""
    violations: list[str] = []
    for search_dir in [REPO / "core", REPO / "services"]:
        if not search_dir.exists():
            continue
        for py in search_dir.rglob("*.py"):
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if NO_STORE_DELEGATE_IMPORT.search(text):
                violations.append(str(py.relative_to(REPO)))
    assert not violations, f"Files import deleted store_delegate: {violations}"


def test_task_identity_not_in_rust_write_domains():
    """task_identity must not be in RUST_WRITE_DOMAINS (hard-cut, ADR-024)."""
    from packages.storage_authority import RUST_WRITE_DOMAINS

    assert "task_identity" not in RUST_WRITE_DOMAINS
