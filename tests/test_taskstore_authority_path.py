"""Task identity authority path — structural guard test (P1-B).

Verifies that all production ``TaskStore(...)`` instantiations go
through ``maybe_wrap_taskstore(...)``. The Rust authority is already
wired in ADR-016 (save, delete, add_run). This test guarantees no
production code path bypasses the delegate.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "core"
SERVICES = REPO / "services"

TASK_STORE_DIRECT = re.compile(r"TaskStore\s*\(")
MAYBE_WRAP_TASKSTORE = re.compile(r"maybe_wrap_taskstore\s*\(")
DELEGATE_IMPORT = re.compile(r"from\s+core\.automation\.store_delegate\s+import")

EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "core/automation/store.py",
        "core/automation/store_delegate.py",
        "scripts/check_rust_authority_migration.py",
    }
)


def _production_py_files():
    for search_dir in (CORE, SERVICES):
        if not search_dir.exists():
            continue
        for py in search_dir.rglob("*.py"):
            try:
                rel = py.relative_to(REPO).as_posix()
            except ValueError:
                continue
            if rel in EXEMPT_FILES:
                continue
            yield py, rel


def test_no_direct_task_store_instantiation_without_maybe_wrap():
    """Production code must not directly instantiate TaskStore(...)
    without wrapping it through maybe_wrap_taskstore."""
    violations: list[str] = []

    for py, rel in _production_py_files():
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not TASK_STORE_DIRECT.search(text):
            continue
        if MAYBE_WRAP_TASKSTORE.search(text) and DELEGATE_IMPORT.search(text):
            continue
        for match in TASK_STORE_DIRECT.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            violations.append(f"{rel}:{line_no}")

    assert not violations, (
        "Production code must not directly instantiate TaskStore(...) "
        "without wrapping through maybe_wrap_taskstore. Violations:\n  "
        + "\n  ".join(violations)
    )
