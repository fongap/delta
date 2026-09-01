"""User-owned trust decisions for repository-provided command allowances.

A repository may declare command prefixes in `.delta/config.toml`, but those grants
take effect only after the user trusts that exact canonical workspace root. Trust follows
the path rather than a snapshot of the config: future changes at a trusted path are
accepted until the user revokes trust.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from packages.secrets import state_dir, verify_user_restricted, write_private_text

logger = logging.getLogger("core.workspace_trust")


class WorkspaceTrustStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path) if path is not None else state_dir() / "workspace_trust.json"
        )

    @staticmethod
    def canonical(path: str | Path) -> str:
        return str(Path(path).expanduser().resolve())

    def _load(self) -> set[str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(data, dict):
            return set()
        values = data.get("trusted_workspaces", [])
        if not isinstance(values, list):
            return set()
        return {str(v) for v in values if isinstance(v, str) and v}

    def is_trusted(self, workspace: str | Path) -> bool:
        return self.canonical(workspace) in self._load()

    def list(self) -> list[str]:
        return sorted(self._load())

    def set_trusted(self, workspace: str | Path, trusted: bool) -> str:
        canonical = self.canonical(workspace)
        values = self._load()
        if trusted:
            values.add(canonical)
        else:
            values.discard(canonical)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # write_private_text = the SecretStore's atomic user-only write: 0600 chmod on
        # POSIX and an icacls user-only ACL on Windows, where os.chmod(0o600) is a no-op.
        write_private_text(
            self.path, json.dumps({"trusted_workspaces": sorted(values)}, indent=2) + "\n"
        )
        self._record_acl_state()
        return canonical

    def _record_acl_state(self) -> None:
        """Persist whether the trust file is actually protected, mirroring the
        SecretStore's degradation marker: best-effort hardening must never degrade
        silently — callers/UI can surface "trust decisions stored unprotected"."""
        try:
            protected = verify_user_restricted(self.path)
        except Exception:
            protected = False
        marker = self._acl_marker_path()
        if protected:
            # A later successful write clears an earlier degradation marker.
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
            return
        try:
            marker.write_text(
                "ACL/permission hardening could not be verified for this file.\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        logger.warning(
            "workspace trust stored WITHOUT ACL protection (hardening unverified): %s",
            self.path,
        )

    def _acl_marker_path(self) -> Path:
        return self.path.with_name(self.path.name + ".acl-unprotected")

    def acl_unprotected(self) -> bool:
        """True when the last write could not confirm user-only protection.
        Callers/UI can use this to show a degraded-security notice."""
        return self._acl_marker_path().is_file()
