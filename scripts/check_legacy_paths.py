"""Legacy-path consistency gate — blocks references to directories that were migrated
during the repository restructure (see docs/architecture/repository-layout.md).

The layout is frozen at the current structure; old hardcoded paths that survive in
workflows, build scripts, configs, or tests reappear as release-time FileNotFoundErrors
(owner-hit 2026-08-31: release.yml still read the server version file at its OLD
packaging-root location after the file had moved to packaging/server/). This gate makes
any return of a deprecated path a CI failure instead of a release-night surprise.

Scope and exemptions, deliberately:
  - Scans every GIT-TRACKED file (git ls-files), so .gitignore'd build output can't
    noise the result — and so this gate's own pattern table and its pytest wrapper are
    scanned too. Neither may contain a matchable literal legacy path: the patterns are
    regexes that don't match their own source, and tests must build their fixtures at
    runtime (keep it that way, or the gate flags itself).
  - Exempts HISTORY files only: CHANGELOG.md, UPSTREAM.md, and docs/governance/** —
    migration records and attribution must keep describing the old paths verbatim.
    Everything else (including docs and tests) must use current paths.
  - Only precise file-level patterns are listed here (never bare directory names like
    "surfaces" or "src" — those are ordinary English words and current subpaths such as
    apps/desktop/src; the forbidden TOP-LEVEL roots stay covered by the directory-
    existence check in the CI layout-check job).

Run:  python scripts/check_legacy_paths.py     (exit 1 on any hit; used by CI)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Legacy path → the current canonical path. Matched with [\\/] so Windows-style and
# POSIX-style leftovers are both caught; anchored to "packaging[/\\]" (or the exact
# shown prefix) so the canonical paths themselves never match.
FORBIDDEN: dict[str, str] = {
    r"packaging[/\\]delta-server-version\.txt": "packaging/server/delta-server-version.txt",
    r"packaging[/\\]delta-server\.spec": "packaging/server/delta-server.spec",
    r"packaging[/\\]server_entry\.py": "packaging/server/server_entry.py",
    r"packaging[/\\]build_portable\.ps1": "packaging/portable/build_portable.ps1",
    r"packaging[/\\]scan_portable_paths\.ps1": "packaging/portable/scan_portable_paths.ps1",
}

# Files where history/attribution legitimately records old paths (keep verbatim).
HISTORY_EXEMPT = ("CHANGELOG.md", "UPSTREAM.md")
HISTORY_EXEMPT_DIRS = ("docs/governance",)

# Directories never worth scanning (build output / vendored trees).
_SKIP_PARTS = {".git", "node_modules", "target", "__pycache__", ".venv", "dist", "releases"}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [REPO / line for line in out.splitlines() if line]


def _is_exempt(path: Path) -> bool:
    rel = path.relative_to(REPO).as_posix()
    return rel in HISTORY_EXEMPT or rel.startswith(HISTORY_EXEMPT_DIRS)


def violations_for(text: str, rel_path: str) -> list[str]:
    """Deprecated-path violations in one file's text (`rel_path` is repo-relative and
    only used for reporting)."""
    return [
        f"{rel_path}:{text.count(chr(10), 0, match.start()) + 1}: deprecated path "
        f"-> use {canonical}"
        for pattern, canonical in FORBIDDEN.items()
        for match in re.finditer(pattern, text)
    ]


def find_violations() -> list[str]:
    violations: list[str] = []
    for path in _tracked_files():
        if _is_exempt(path) or _SKIP_PARTS & set(path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary asset
        violations.extend(violations_for(text, path.relative_to(REPO).as_posix()))
    return violations


def main() -> int:
    violations = find_violations()
    if violations:
        print("legacy-path gate FAILED — migrated paths referenced outside history files:")
        for violation in violations:
            print(f"  {violation}")
        return 1
    print("legacy-path gate clean: no deprecated path references")
    return 0


if __name__ == "__main__":
    sys.exit(main())
