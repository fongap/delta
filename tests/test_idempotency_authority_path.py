"""Structural test: all production Idempotency paths go through maybe_wrap (P0-D).

This test verifies that no production code outside the exempt set
(delegate module, tests, migration tool) directly instantiates
``IdempotencyLog(...)`` without wrapping it through ``maybe_wrap``.

The CI guard ``scripts/check_rust_authority_migration.py`` enforces the
same check at a static-analysis level; this test provides a pytest-level
safety net that runs in the default test suite.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "core"
SERVICES = REPO / "services"

IDEMPOTENCY_LOG_DIRECT = re.compile(r"IdempotencyLog\s*\(")
MAYBE_WRAP = re.compile(r"maybe_wrap\s*\(")
DELEGATE_IMPORT = re.compile(r"from\s+core\.idemlog_delegate\s+import")

EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "core/idemlog.py",
        "core/idemlog_delegate.py",
        "core/reference_harness.py",  # docstring mention only
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


def test_no_direct_idempotency_log_instantiation_without_maybe_wrap():
    """Production code must not directly instantiate IdempotencyLog(...)
    without wrapping it through maybe_wrap."""
    violations: list[str] = []

    for py, rel in _production_py_files():
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not IDEMPOTENCY_LOG_DIRECT.search(text):
            continue
        if MAYBE_WRAP.search(text) and DELEGATE_IMPORT.search(text):
            continue
        for match in IDEMPOTENCY_LOG_DIRECT.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            violations.append(f"{rel}:{line_no}")

    assert not violations, (
        "Production code must not directly instantiate IdempotencyLog(...) "
        "without wrapping through maybe_wrap. Violations:\n  "
        + "\n  ".join(violations)
    )
