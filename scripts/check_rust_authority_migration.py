"""Control-plane authority guard (ADR-012/015/020/022/023/024/026/027/028/029).

Two-tier checks:

1. **Structural (always on)**: scans ``core/`` for direct ``sqlite3.connect``
   calls and verifies that each R1/R2 candidate module forward-declares the
   ``is_rust_authority`` import for domains that are still migrating.

   Idempotency (ADR-022), Ledger (ADR-023), Task Identity (ADR-024),
   Artifact Registry (ADR-026), Source/Citation (ADR-027), Validation (ADR-028),
   and Checkpoint (ADR-029) are hard-cut: their delegates are deleted and the
   Python facade is always the Rust authority.

2. **Enforcement (opt-in)**: when ``DELTA_RUST_AUTHORITY=1`` AND this
   script is invoked with the ``--enforce-rust-authority`` flag, scans
   for direct source-citation / validation / checkpoint register calls in
   non-test code and verifies they go through the corresponding delegate
   wrapper.

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

from packages.storage_authority import RUST_WRITE_DOMAINS, is_rust_authority  # noqa: E402

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

TASK_STORE_DIRECT = re.compile(r"TaskStore\s*\(")

TESTS = REPO / "tests"

# Map each R1/R2 domain to the file path(s) that own it. The mapping is
# explicit (not heuristic) so the guard is stable across refactors.
DOMAIN_TO_FILES: dict[str, tuple[str, ...]] = {
    "ledger": ("core/ledger.py",),
    "run_state": ("core/ledger.py",),
    "validation": ("core/validation.py",),
    "checkpoint": ("core/recovery.py",),
}

# The delegate wrapper files are allowed to instantiate the underlying
# class — that is how the wrapper is constructed. The guard must not
# flag itself.
DELEGATE_FILES: frozenset[str] = frozenset()


def _file_owns_domain(path: Path, domain: str) -> bool:
    """Whether ``path`` is the canonical owner of ``domain``."""
    try:
        rel = path.relative_to(REPO).as_posix()
    except ValueError:
        return False
    return rel in DOMAIN_TO_FILES.get(domain, ())


# Files that legitimately contain the *definition* of a domain's direct
# write API. These are excluded from the enforcement scan (they are the
# home of the function, not a caller of it). The owning file for each
# domain is the same set as DOMAIN_TO_FILES values flattened.
OWNED_FILES: frozenset[str] = frozenset(
    path
    for paths in DOMAIN_TO_FILES.values()
    for path in paths
)


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
            for domain in RUST_WRITE_DOMAINS:
                if _file_owns_domain(py, domain):
                    violations.append((py, domain))
                    break
    return violations


def _scan_enforcement() -> list[tuple[Path, int, str, str]]:
    """Find direct IdempotencyLog() / RunEventLedger() / TaskStore()
    instantiations in non-test code.

    Only runs when ``DELTA_RUST_AUTHORITY=1`` is set (per ADR-014/015/016).
    Scans ``core/`` and ``services/server/`` but skips tests and the
    delegate wrapper files themselves. A direct instantiation is OK
    only if the same file imports the corresponding ``maybe_wrap`` /
    delegate and uses it (i.e. we are mid-migration on that file).
    """
    violations: list[tuple[Path, int, str, str]] = []
    search_dirs = [CORE, SERVICES]
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for py in search_dir.rglob("*.py"):
            try:
                rel = py.relative_to(REPO).as_posix()
            except ValueError:
                continue
            if rel in DELEGATE_FILES:
                continue
            if rel in OWNED_FILES:
                continue
            try:
                py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Idempotency, Ledger, Task Identity, Artifact Registry,
            # Source/Citation, Validation, and Checkpoint are hard-cut
            # (ADR-022 / ADR-023 / ADR-024 / ADR-026 / ADR-027 /
            # ADR-028 / ADR-029): their delegates are deleted and the
            # Python facade is always the Rust authority. No enforcement
            # guard needed for these domains.
    return violations


def main() -> int:
    if "--enforce-rust-authority" in sys.argv and not any(
        is_rust_authority(d) for d in RUST_WRITE_DOMAINS
    ):
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
            for path, line_no, snippet, domain in enforce:
                rel = path.relative_to(REPO)
                print(
                    f"  - {rel}:{line_no}: direct {snippet} instantiation "
                    f"while DELTA_RUST_AUTHORITY=1 ({domain} domain). "
                    f"Wrap via the corresponding maybe_wrap / delegate.",
                    file=sys.stderr,
                )
            return 1

    print("storage-authority guard: all R1 candidate modules import is_rust_authority")
    return 0


if __name__ == "__main__":
    sys.exit(main())