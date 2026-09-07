"""Legacy-branding consistency gate (R1.6 P1-3).

Prevents the codebase from re-introducing OpenWorker / cowork brand names in
production source. The R1.6 source-level rename (PR #128) swept cowork → delta
across the stack; this script makes sure new code doesn't regress.

Scope:
  - Scans Git-tracked files in apps/ core/ packages/ providers/ services/
    integrations/ packaging/ scripts/ (case-insensitive).
  - Skips docs/ (CHANGELOG / ADR / historical architecture records legitimately
    reference OpenWorker as a third-party / upstream project).
  - Skips LICENSE / NOTICE / UPSTREAM.md (attribution is required by MIT and
    by the project's provenance statement).
  - Skips files matching legacy_* / *_legacy* / test_*legacy* (legacy-compat
    tests that explicitly exercise old spell forms).
  - Skips lines that contain `OpenWorker` inside a docstring or comment that
    also mentions `andrewyng/openworker` or `Semantic port of` — these are
    provenance / attribution records, not branding.

Forbidden patterns (case-insensitive):
  - `OpenWorker` / `openworker`  (brand name)
  - `@ocw`                        (legacy Slack bot handle)
  - `ocw.`                        (legacy localStorage / event prefix)
  - `.openworker/`                (legacy per-user state directory)
  - `OPENWORKER_`                 (legacy env var prefix)
  - `OpenWorkerRuntime`           (legacy runtime class name)
  - `CoworkerManager`             (legacy manager class name)
  - `ocw_home`                    (legacy env var)
  - `cowork`                      (legacy agent id / module name)

Allowed patterns (kept as historical context, not violations):
  - The token `cowork` inside CHANGELOG.md, ADR-004~017, UPSTREAM.md, docs/,
    LICENSE, NOTICE — provenance / historical decision records.
  - The token `coworker` inside `tests/test_conversations_repair.py:12`
    ("imports adapted coworker.* -> core.*") — historical port attribution.
  - The token `@ocw hi` in `tests/test_fake_slack.py:269,294` — legacy
    Slack text used to verify mention resolution against historical spell
    forms (the test asserts the system rewrites `@ocw hi` correctly).
  - The token `[ocw:...]` in `tests/test_source_citation.py:19,365,368` —
    legacy inbox token explicitly tested as **terminated** (P2 removed the
    `[ow:…]` / `[ocw:…]` parse path; the test pins that termination).

Run:

    python scripts/check_legacy_branding.py

Exit code 1 on any violation.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SCAN_DIRS = ("apps", "core", "packages", "providers", "services",
             "integrations", "packaging", "scripts")

SKIP_FILES = {
    # Legacy-compat test fixtures that pin old spell forms on purpose.
    "tests/test_fake_slack.py",
    "tests/test_source_citation.py",
    "tests/test_conversations_repair.py",
    # Self — the patterns in this script mention the forbidden tokens.
    "scripts/check_legacy_branding.py",
}

# Lines that match any of these regexes are NOT checked, even inside scanned
# dirs. This lets us keep provenance / attribution comments and historical
# lineage notes that legitimately reference the OpenWorker / cowork names
# without using them as live product identity.
EXEMPT_LINE_PATTERNS = [
    re.compile(r"andrewyng/openworker", re.IGNORECASE),
    re.compile(r"Semantic port of", re.IGNORECASE),
    re.compile(r"Port of andrewyng/openworker", re.IGNORECASE),
    re.compile(r"imports adapted coworker", re.IGNORECASE),
    # Historical lineage notes: "was historically `cowork` during the
    # OpenWorker lineage" / "OpenWorker-era rebrand aliases" / etc.
    re.compile(r"was historically", re.IGNORECASE),
    re.compile(r"OpenWorker-era|OpenWorker lineage", re.IGNORECASE),
    # OpenWorker platform split / archive provenance.
    re.compile(r"archive[d]? OpenWorker|OpenWorker platform split|OpenWorker rebrand", re.IGNORECASE),
]

FORBIDDEN_PATTERNS = [
    re.compile(r"OpenWorker", re.IGNORECASE),
    re.compile(r"@ocw\b", re.IGNORECASE),
    re.compile(r"\bocw\.", re.IGNORECASE),
    re.compile(r"\.openworker[/\\]"),
    re.compile(r"OPENWORKER_"),
    re.compile(r"OpenWorkerRuntime"),
    re.compile(r"CoworkerManager"),
    re.compile(r"\bocw_home\b"),
    re.compile(r"\bcowork\b", re.IGNORECASE),
    re.compile(r"coworker/", re.IGNORECASE),
    re.compile(r"core\.agents\.cowork\b", re.IGNORECASE),
    re.compile(r"COWORK_CAPABILITIES|COWORK_INSTRUCTIONS|COWORK_TOOLS"),
    re.compile(r"cowork_agent|cowork_tool_factory"),
    # Identifier-aware: catches "Cowork" / "cowork" embedded in larger
    # identifiers that the word-boundary pattern misses (CamelCase /
    # snake_case with cowork as a component, e.g. startCoworkSession,
    # stopCoworkRun, my_cowork_helper). The leading \w ensures standalone
    # `cowork` is still caught by the word-boundary pattern above.
    re.compile(r"\w[Cc]owork"),
]

_SKIP_PARTS = {
    ".git", "node_modules", "target", "__pycache__", ".venv",
    "dist", "releases", ".pytest_cache", "build",
}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    return [REPO / line for line in out.splitlines() if line]


def _should_scan(path: Path) -> bool:
    rel = path.relative_to(REPO).as_posix()
    if rel in SKIP_FILES:
        return False
    parts = rel.split("/")
    if any(p in _SKIP_PARTS for p in parts):
        return False
    # Only scan source dirs (not docs/, not LICENSE, not UPSTREAM.md).
    if not any(rel.startswith(d + "/") for d in SCAN_DIRS):
        return False
    return True


def _is_exempt_line(line: str) -> bool:
    return any(p.search(line) for p in EXEMPT_LINE_PATTERNS)


def violations_for(text: str, rel_path: str) -> list[str]:
    out: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_exempt_line(line):
            continue
        for pat in FORBIDDEN_PATTERNS:
            for m in pat.finditer(line):
                out.append(
                    f"{rel_path}:{lineno}: forbidden legacy brand "
                    f"{pat.pattern!r} matched {m.group()!r}"
                )
    return out


def find_violations() -> list[str]:
    violations: list[str] = []
    for path in _tracked_files():
        if not _should_scan(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        violations.extend(violations_for(text, path.relative_to(REPO).as_posix()))
    return violations


def main() -> int:
    violations = find_violations()
    if violations:
        print(
            "legacy-branding gate FAILED — OpenWorker / cowork / @ocw "
            "tokens found in production source (R1.6 P1-3):"
        )
        for v in violations:
            print(f"  {v}")
        print(
            "\nIf this is a legitimate historical reference, add the line "
            "to EXEMPT_LINE_PATTERNS or the file to SKIP_FILES in "
            "scripts/check_legacy_branding.py."
        )
        return 1
    print("legacy-branding gate clean: no OpenWorker / cowork tokens in production source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
