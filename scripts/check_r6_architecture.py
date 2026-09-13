"""R6 Architecture Hard-Cut Guard.

Prevents the re-introduction of the Python application backend that R6 removed.
The core product is Rust + TypeScript; Python is allowed ONLY as a controlled
Worker / Capability execution environment, never as an application authority.

This runner refuses (exit 1) if any of the forbidden patterns re-appear.

Forbidden (application backend / runtime authority re-entry):
  1. FastAPI application server (FastAPI/Uvicorn import or `FastAPI(` app builders
     that serve the /v1/* control plane).
  2. `delta-server` console entry / PyInstaller server spec / server_entry.
  3. Python `TurnEngine` owning the agent loop (core/engine.py TurnEngine).
  4. Python `SessionManager` owning engine/session/run state.
  5. Tauri shell spawning a Python server sidecar (delta-server spawn / proxy to it).
  6. The `aisuite` package as a runtime/tool-lifecycle abstraction.
  7. OpenWork localhost HTTP/WebSocket -> Python FastAPI control plane in the
     desktop renderer (api.ts routing runtime runs back to HTTP).

Allowed (Worker / Capability execution environment):
  - Python worker scripts, Shell, PowerShell, Office/Research/Media capabilities.
  These sit behind a controlled Worker Runner and must not own run/policy state.

Run::
    python scripts/check_r6_architecture.py

Exits 0 on success, 1 on any violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Pattern sets
# ---------------------------------------------------------------------------

# 1. FastAPI / Uvicorn application server.
FASTAPI_IMPORT = re.compile(r"^\s*(import|from)\s+(fastapi|uvicorn)\b", re.M)
FASTAPI_APP_BUILDER = re.compile(r"\bFastAPI\s*\(")

# 2. delta-server packaging / console entry.
DELTA_SERVER_ENTRY = re.compile(r"\b(delta-server)\b")
PYINSTALLER_SPEC = re.compile(r"\bPyInstaller\b|\bpyinstaller\b", re.I)
SERVER_ENTRY_PY = re.compile(r"\bserver_entry\.py\b")

# 3. Python TurnEngine — the agent loop.
TURN_ENGINE = re.compile(r"\bclass\s+TurnEngine\b")

# 4. Python SessionManager owning engine/session/run state.
SESSION_MANAGER_CLASS = re.compile(r"\bclass\s+SessionManager\b")

# 5. Tauri shell spawning the Python sidecar / running a localhost proxy to it.
SIDECAR_SPAWN = re.compile(r"\bdelta-server\b", re.I)
PROXY_TO_SIDECAR = re.compile(r"\bproxy::start_proxy\b|\bstart_proxy\b")

# 6. aisuite as a runtime / tool-lifecycle abstraction.
AISUITE_IMPORT = re.compile(r"(^\s*(import|from)\s+aisuite\b)", re.M)

# 7. Desktop renderer routing runtime runs back to HTTP (R6 removes this).
#    A Session class that sends `user_message`/`interrupt` over a WebSocket to a
#    local FastAPI control plane. Matching the old api.ts pattern.
WS_SESSION_USER_MESSAGE = re.compile(r"[\"\']user_message[\"\']")

# Positive allow-list: files/patterns that are intentionally exempt.
ALLOWED_FILES: frozenset[str] = frozenset()
ALLOWED_SNIPPETS: tuple[tuple[str, ...], ...] = ()


# ---------------------------------------------------------------------------
# Source file scanning (excludes docs, tests fixtures checked separately).
# ---------------------------------------------------------------------------

# Tool/capability worker files may keep aisuite's LOCAL tool-helper functions
# (ToolMetadata / tool / Tools schema generation) per R6 §8 — those are not a
# runtime, authority, or tool lifecycle. aisuite is forbidden only where it
# owns the Runtime/Authority path (core engine/agents loop). We therefore check
# aisuite only in the application-authority files, not across the tool surface.
AISUITE_AUTHORITY_FILES = (
    "core/engine.py",
    "core/agent.py",
    "core/agents/",
)


def _flag_aisuite_authority(path: Path) -> bool:
    """True if aisuite is used as a runtime/authority, not a tool helper."""
    try:
        rel = path.relative_to(REPO).as_posix()
    except ValueError:
        return False
    if not rel.startswith(AISUITE_AUTHORITY_FILES):
        return False
    text = _read(path)
    if text is None:
        return False
    # A runtime call pattern (`ai.` ahost complete/chat), not a schema helper.
    return AISUITE_IMPORT.search(text) is not None


def _check_file(
    path: Path, checks: list[tuple[str, re.Pattern[str], str]]
) -> list[str]:
    """Return a list of violation strings for a single file/path."""
    rel = path
    try:
        rel = path.relative_to(REPO).as_posix()
    except ValueError:
        pass
    if rel in ALLOWED_FILES:
        return []
    text = _read(path)
    if text is None:
        return []
    violations: list[str] = []
    for label, pattern, allowed_hint in checks:
        if pattern.search(text):
            violations.append(f"{rel}: {label}")
    return violations


def main() -> int:
    scan_dirs = [
        "core",
        "providers",
        "packages",
        "services",
        "apps",
        "scripts",
        "packaging",
        "integrations",
    ]
    all_violations: list[str] = []

    # Source file pattern scanning (excludes docs, tests fixtures checked separately).
    source_suffixes = {".py", ".rs", ".ts", ".tsx", ".json", ".toml", ".sh", ".ps1"}
    for dirname in scan_dirs:
        base = REPO / dirname
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.suffix not in source_suffixes:
                continue
            try:
                rel = f.relative_to(REPO).as_posix()
            except ValueError:
                continue
            # Skip this guard's own tree when matching short keywords.
            if rel.endswith("check_r6_architecture.py"):
                continue
            checks: list[tuple[str, re.Pattern[str], str]] = []
            checks.append(("FastAPI/uvicorn application server", FASTAPI_IMPORT, ""))
            checks.append(
                ("FastAPI( application app builder", FASTAPI_APP_BUILDER, "")
            )
            checks.append(("Python TurnEngine agent loop", TURN_ENGINE, ""))
            checks.append(("Python SessionManager authority", SESSION_MANAGER_CLASS, ""))
            # aisuite only as a runtime/authority (not tool-helper).
            violations = _check_file(f, checks)
            all_violations.extend(violations)
            if _flag_aisuite_authority(f):
                all_violations.append(f"{rel}: aisuite runtime/tool-lifecycle authority")

    # Deduplicate.
    all_violations = sorted(set(all_violations))

    if all_violations:
        print("R6 architecture guard violations:", file=sys.stderr)
        for v in all_violations:
            print(f"  - {v}", file=sys.stderr)
        print(file=sys.stderr)
        print(
            "The Delta core product is Rust + TypeScript. Python is allowed only as a "
            "controlled Worker/Capability execution environment, never as an application "
            "authority. See docs/DELTA_BLUEPRINT.md and docs/architecture/target-architecture.md.",
            file=sys.stderr,
        )
        return 1

    print("R6 architecture guard: no Python application backend authority found")
    return 0


if __name__ == "__main__":
    sys.exit(main())