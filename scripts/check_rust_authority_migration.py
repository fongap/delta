"""Storage authority migration guard — R1 plumbing + enforcement (ADR-012/014).

Two-tier checks:

1. **Structural (always on)**: scans ``core/`` for direct ``sqlite3.connect``
   calls and verifies that each R1 candidate module forward-declares the
   ``is_rust_authority`` import. Lands in PR11 (ADR-012).

2. **Enforcement (opt-in)**: when ``DELTA_RUST_AUTHORITY=1`` AND this
   script is invoked with the ``--enforce-rust-authority`` flag, scans
   for direct ``IdempotencyLog(...)`` instantiations in non-test code
   and verifies they go through ``maybe_wrap`` or are test fixtures.
   Lands in PR12 stage C (ADR-014).

Run::

    python scripts/check_rust_authority_migration.py
    python scripts/check_rust_authority_migration.py --enforce-rust-authority

Exits 0 on success, 1 on any violation, 2 on missing prerequisites.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from packages.storage_authority import DOMAINS, is_rust_authority  # noqa: E402

CORE = REPO / "core"
SERVICES = REPO / "services"
SQLITE_CONNECT = re.compile(r"sqlite3\.connect\s*\(")
STORAGE_AUTHORITY_IMPORT = re.compile(
    r"from\s+packages\.storage_authority\s+import|"
    r"from\s+\.\.storage_authority\s+import|"
    r"from\s+\.storage_authority\s+import|"
    r"import\s+packages\.storage_authority|"
    r"from\s+packages\s+import\s+storage_authority"
)
IDEMPOTENCY_LOG_DIRECT = re.compile(r"IdempotencyLog\s*\(")
MAYBE_WRAP_OR_DELEGATE = re.compile(
    r"maybe_wrap\s*\(|IdempotencyLogWithDelegate\s*\("
)
TESTS = REPO / "tests"
CORE_REFERENCE = re.compile(r"from\s+core\.idemlog_delegate\s+import")

# Map each R1 domain to the file path(s) that own it. The mapping is
# explicit (not heuristic) so the guard is stable across refactors.
DOMAIN_TO_FILES: dict[str, tuple[str, ...]] = {
    "idempotency": ("core/idemlog.py",),
    "ledger": ("core/ledger.py",),
    "run_state": ("core/ledger.py",),
    "task_identity": ("core/automation/store.py",),
}


def _file_owns_domain(path: Path, domain: str) -> bool:
    """Whether ``path`` is the canonical owner of ``domain``."""
    try:
        rel = path.relative_to(REPO).as_posix()
    except ValueError:
        return False
    return rel in DOMAIN_TO_FILES.get(domain, ())


def _scan_structural() -> list[tuple[Path, str]]:
    """Find R1 candidate modules that lack the storage_authority import."""
    violations: list[tuple[Path, str]] = []
    for py in CORE.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not SQLITE_CONNECT.search(text):
            continue
        if not STORAGE_AUTHORITY_IMPORT.search(text):
            for domain in DOMAINS:
                if _file_owns_domain(py, domain):
                    violations.append((py, domain))
                    break
    return violations


def _scan_enforcement() -> list[tuple[Path, int, str]]:
    """Find direct IdempotencyLog() instantiations in non-test code.

    Only runs when ``DELTA_RUST_AUTHORITY=1`` is set (per ADR-014 stage C).
    Scans ``core/`` and ``services/server/`` but skips tests. A direct
    instantiation is OK only if the same file imports ``maybe_wrap`` or
    ``IdempotencyLogWithDelegate`` and uses it (i.e. we are mid-migration
    on that file).
    """
    violations: list[tuple[Path, int, str]] = []
    search_dirs = [CORE, SERVICES]
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for py in search_dir.rglob("*.py"):
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for match in IDEMPOTENCY_LOG_DIRECT.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                if not is_rust_authority("idempotency"):
                    return []
                if MAYBE_WRAP_OR_DELEGATE.search(text) and CORE_REFERENCE.search(text):
                    continue
                violations.append((py, line_no, match.group()))
    return violations


def main() -> int:
    if "--enforce-rust-authority" in sys.argv and not is_rust_authority("idempotency"):
        print(
            "warning: --enforce-rust-authority requested but DELTA_RUST_AUTHORITY "
            "is not set; enforcement layer is inert.",
            file=sys.stderr,
        )

    if not CORE.is_dir():
        print(f"error: {CORE} is not a directory", file=sys.stderr)
        return 2

    structural = _scan_structural()
    if structural:
        print("storage-authority guard violations (structural):", file=sys.stderr)
        for path, domain in structural:
            rel = path.relative_to(REPO)
            print(
                f"  - {rel}: writes to {domain} domain "
                f"but does not import packages.storage_authority.is_rust_authority",
                file=sys.stderr,
            )
        print(
            f"\n  {len(structural)} file(s) need an import like:",
            file=sys.stderr,
        )
        print(
            "    from packages.storage_authority import is_rust_authority",
            file=sys.stderr,
        )
        return 1

    if "--enforce-rust-authority" in sys.argv:
        enforce = _scan_enforcement()
        if enforce:
            print("storage-authority guard violations (enforcement):", file=sys.stderr)
            for path, line_no, snippet in enforce:
                rel = path.relative_to(REPO)
                print(
                    f"  - {rel}:{line_no}: direct {snippet} instantiation "
                    f"while DELTA_RUST_AUTHORITY=1. "
                    f"Wrap via core.idemlog_delegate.maybe_wrap.",
                    file=sys.stderr,
                )
            return 1

    print("storage-authority guard: all R1 candidate modules import is_rust_authority")
    return 0


if __name__ == "__main__":
    sys.exit(main())
