"""Secret store — one canonical, file-backed store for connector/MCP credentials.

Design (from OpenClaw): secrets **never enter the model's context, prompts, or traces**.
The store holds profiles keyed by `connector[:account]`; values may be literals OR
`${ENV_VAR}` references resolved at read time from the process env / `~/.config/delta/.env`.

v1 is a `0600` JSON file behind this interface; the interface is what callers depend on, so
a Keychain / age-encrypted backend can swap in later without touching them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ._jsonstate import load_json_state

logger = logging.getLogger(__name__)

_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_IS_WINDOWS = sys.platform == "win32"


def state_dir() -> Path:
    """Where delta keeps its state — the one cross-platform source of truth.

    Resolution order:
    1. `$DELTA_STATE_DIR` — explicit override on any OS (used by tests/sidecars).
    2. Windows: `%APPDATA%\\delta` (e.g. `C:\\Users\\You\\AppData\\Roaming\\delta`),
       the native per-user app-data location.
    3. macOS / Linux: `~/.config/delta` (XDG-style, unchanged from prior behavior).
    """
    base = os.environ.get("DELTA_STATE_DIR")
    if base:
        return Path(base).expanduser()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "delta"
    return Path.home() / ".config" / "delta"


def _load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _restrict_to_user(path: Path, *, is_dir: bool) -> bool:
    """Restrict a path so only the current user can access it, then VERIFY it took effect.

    POSIX expresses this with mode bits (0700 dir / 0600 file). Windows has no such bits —
    `os.chmod` there only toggles the read-only flag, so a 0600 chmod is a silent no-op and
    the file inherits broad ACLs (SYSTEM, Administrators, …). Use an ACL instead: strip
    inherited entries and grant the current user alone. Best-effort on Windows so a transient
    icacls failure never blocks saving a key.

    Returns True when the restriction is verified in place, False when it was applied
    best-effort but could not be confirmed (degraded — callers should surface that).
    """
    if _IS_WINDOWS:
        user = os.environ.get("USERNAME")
        if not user:
            return False
        domain = os.environ.get("USERDOMAIN")
        account = f"{domain}\\{user}" if domain else user
        # A directory grant MUST be inheritable — (OI) object-inherit for files, (CI)
        # container-inherit for subdirs — so everything created inside (the SQLite stores,
        # conversations, …) inherits the user's access. Without these flags, /inheritance:r
        # leaves the directory with a non-inheritable ACE and any child file ends up with an
        # empty DACL → sqlite3 "unable to open database file", crashing the server on launch.
        grant = f"{account}:(OI)(CI)F" if is_dir else f"{account}:F"
        try:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
                capture_output=True,
                check=False,
            )
        except OSError:
            return False
        return _windows_acl_ok(path)
    os.chmod(path, 0o700 if is_dir else 0o600)
    try:
        return stat.S_IMODE(os.stat(path).st_mode) == (0o700 if is_dir else 0o600)
    except OSError:
        return False


def _windows_acl_ok(path: Path) -> bool:
    """Re-read the ACL via `icacls <path>` and confirm only the current user is granted.

    Mirrors the assertions in tests/test_secrets.py::test_secrets_file_is_restricted."""
    try:
        proc = subprocess.run(
            ["icacls", str(path)], capture_output=True, text=True, check=False
        )
    except OSError:
        return False
    if proc.returncode != 0:
        return False
    out = proc.stdout
    user = os.environ.get("USERNAME", "")
    if not user or user not in out:
        return False
    # Inherited broad principals must be gone after /inheritance:r.
    return "NT AUTHORITY\\SYSTEM" not in out and "BUILTIN\\Administrators" not in out


def write_private_text(path: str | Path, content: str) -> Path:
    """Atomically write a user-only text file using the SecretStore's OS protections."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _restrict_to_user(target.parent, is_dir=True)
    except OSError:
        pass
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    _restrict_to_user(tmp, is_dir=False)
    os.replace(tmp, target)
    return target


def verify_user_restricted(path: str | Path) -> bool:
    """Verify (without mutating) that `path` is restricted to the current user.

    The read-only counterpart of `_restrict_to_user`: lets stores that share the
    best-effort private-write path (workspace trust, future JSON stores) detect a
    degraded ACL and surface it instead of silently assuming protection."""
    p = Path(path)
    if _IS_WINDOWS:
        return _windows_acl_ok(p)
    try:
        return stat.S_IMODE(os.stat(p).st_mode) == 0o600
    except OSError:
        return False


class SecretStore:
    """File-backed secret store. Reads resolve `${VAR}` refs; status never leaks values."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else state_dir() / "secrets.json"
        self._dotenv_path = self.path.parent / ".env"
        self._lock = threading.Lock()

    # -- reads ------------------------------------------------------------------
    def get(self, profile: str) -> dict[str, Any] | None:
        """Return a profile with `${VAR}` refs resolved, or None if absent."""
        data = self._read().get(profile)
        if data is None:
            return None
        return self.resolve(data)

    def resolve(self, value: Any) -> Any:
        """Resolve `${VAR}` refs in a value (recursively) from env + the local `.env`."""
        env = _load_dotenv(self._dotenv_path)

        def _walk(v: Any) -> Any:
            if isinstance(v, str):
                return _REF.sub(
                    lambda m: os.environ.get(m.group(1))
                    or env.get(m.group(1))
                    or m.group(0),
                    v,
                )
            if isinstance(v, dict):
                return {k: _walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [_walk(x) for x in v]
            return v

        return _walk(value)

    def status(self) -> list[dict[str, Any]]:
        """Profile metadata only — **never** the secret values themselves."""
        out: list[dict[str, Any]] = []
        for profile, data in self._read().items():
            data = data if isinstance(data, dict) else {}
            expires = data.get("expires")
            expired = isinstance(expires, (int, float)) and expires < time.time()
            out.append(
                {
                    "profile": profile,
                    "type": data.get("type"),
                    "account": data.get("account_id"),
                    "expired": bool(expired),
                }
            )
        return out

    # -- writes -----------------------------------------------------------------
    def put(self, profile: str, data: dict[str, Any]) -> None:
        with self._lock:
            store = self._read()
            store[profile] = data
            self._write(store)

    def delete(self, profile: str) -> bool:
        with self._lock:
            store = self._read()
            if profile not in store:
                return False
            del store[profile]
            self._write(store)
            return True

    # -- internals --------------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        # Tolerant load: a corrupt file degrades to an empty state but is preserved as a
        # `.corrupt-<ts>` sibling (same convention as _jsonstate) so the next save cannot
        # silently overwrite the only copy of the user's secrets.
        return load_json_state(self.path, {})

    def _write(self, store: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _restrict_to_user(self.path.parent, is_dir=True)
        except OSError:
            pass
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
        protected = _restrict_to_user(tmp, is_dir=False)
        os.replace(tmp, self.path)
        self._record_acl_state(protected)

    def _record_acl_state(self, ok: bool) -> None:
        """Persist whether the secrets file is ACL-protected so callers/UI can surface it."""
        marker = self._acl_marker_path()
        try:
            if ok:
                marker.unlink(missing_ok=True)
            else:
                logger.warning(
                    "secrets stored WITHOUT ACL protection (icacls hardening failed): %s",
                    self.path,
                )
                marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n", encoding="utf-8")
        except OSError:
            pass

    def _acl_marker_path(self) -> Path:
        return self.path.with_name(self.path.name + ".acl-unprotected")

    def acl_unprotected(self) -> bool:
        """True when the last write could not verify OS-level protection on this file.

        Callers/UI can use this to show "secrets stored without ACL protection"."""
        return self._acl_marker_path().is_file()
