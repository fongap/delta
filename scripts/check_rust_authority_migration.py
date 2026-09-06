"""Storage authority migration guard — Pre-R1 plumbing (ADR-012).

Scans Python sources for direct ``sqlite3.connect`` calls in
``core/`` and verifies that each call site either:

- writes to a domain that is **not** in :data:`packages.storage_authority.DOMAINS`
  (i.e. it is not part of the R1 migration set), OR
- writes to a domain in :data:`DOMAINS` AND the surrounding module
  imports :func:`packages.storage_authority.is_rust_authority` (so a
  future R1 authority switch PR can gate writes via that helper).

The guard does NOT enforce runtime behavior. It only checks structural
readiness. The actual write-path gating lands in PR12+ per ADR-011.

Run::

    python scripts/check_rust_authority_migration.py

Exits 0 on success, 1 on any violation, 2 on missing prerequisites.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from packages.storage_authority import DOMAINS  # noqa: E402

CORE = REPO / "core"
SQLITE_CONNECT = re.compile(r"sqlite3\.connect\s*\(")
STORAGE_AUTHORITY_IMPORT = re.compile(
    r"from\s+packages\.storage_authority\s+import|"
    r"from\s+\.\.storage_authority\s+import|"
    r"from\s+\.storage_authority\s+import|"
    r"import\s+packages\.storage_authority|"
    r"from\s+packages\s+import\s+storage_authority"
)

# Map each R1 domain to the file path(s) that own it. The mapping is
# explicit (not heuristic) so the guard is stable across refactors.
DOMAIN_TO_FILES: dict[str, tuple[str, ...]] = {
    "idempotency": ("core/idemlog.py",),
    "ledger": ("core/ledger.py",),
    "run_state": ("core/ledger.py",),
    "task_identity": ("core/automation/store.py",),
}


def _file_owns_domain(path: Path, domain: str) -> bool:
    """Whether ``path`` is the canonical owner of ``domain``.

    Determined by the explicit :data:`DOMAIN_TO_FILES` table — not a
    filename heuristic. Two domains can share a file (e.g. ``ledger`` and
    ``run_state`` both live in ``core/ledger.py``).
    """
    try:
        rel = path.relative_to(REPO).as_posix()
    except ValueError:
        return False
    return rel in DOMAIN_TO_FILES.get(domain, ())


def _scan() -> list[tuple[Path, str]]:
    """Return list of (file, domain) pairs that need a guard import."""
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


def main() -> int:
    if not CORE.is_dir():
        print(f"error: {CORE} is not a directory", file=sys.stderr)
        return 2

    violations = _scan()
    if not violations:
        print("storage-authority guard: all R1 candidate modules import is_rust_authority")
        return 0

    print("storage-authority guard violations:", file=sys.stderr)
    for path, domain in violations:
        rel = path.relative_to(REPO)
        print(
            f"  - {rel}: writes to {domain} domain "
            f"but does not import packages.storage_authority.is_rust_authority",
            file=sys.stderr,
        )
    print(
        f"\n  {len(violations)} file(s) need an import like:",
        file=sys.stderr,
    )
    print(
        "    from packages.storage_authority import is_rust_authority",
        file=sys.stderr,
    )
    print(
        "  before they can land an R1 authority switch for their domain.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
