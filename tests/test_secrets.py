"""Tests for the SecretStore (C0)."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time

from coworker.secrets import SecretStore


def test_put_get_round_trip(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("slack:default", {"type": "token", "bot_token": "xoxb-123"})
    assert store.get("slack:default") == {"type": "token", "bot_token": "xoxb-123"}
    assert store.get("missing") is None


def test_env_ref_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOK", "from-env")
    store = SecretStore(tmp_path / "secrets.json")
    store.put("slack:default", {"type": "token", "bot_token": "${MY_TOK}"})
    assert store.get("slack:default")["bot_token"] == "from-env"


def test_dotenv_ref_resolution(tmp_path):
    (tmp_path / ".env").write_text('DOCS_TOKEN = "shhh"\n', encoding="utf-8")
    store = SecretStore(tmp_path / "secrets.json")
    store.put("docs:default", {"headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}})
    assert store.get("docs:default")["headers"]["Authorization"] == "Bearer shhh"


def test_unresolved_ref_left_intact(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("x", {"v": "${NOPE_NOT_SET}"})
    assert store.get("x")["v"] == "${NOPE_NOT_SET}"


def test_status_hides_values(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put(
        "gmail:default",
        {
            "type": "oauth",
            "access": "secret",
            "account_id": "me@x.com",
            "expires": time.time() - 10,
        },
    )
    store.put("slack:default", {"type": "token", "bot_token": "xoxb"})
    status = {row["profile"]: row for row in store.status()}
    assert status["gmail:default"]["type"] == "oauth"
    assert status["gmail:default"]["account"] == "me@x.com"
    assert status["gmail:default"]["expired"] is True
    assert status["slack:default"]["expired"] is False
    # No secret material anywhere in the status payload.
    blob = str(store.status())
    assert "secret" not in blob and "xoxb" not in blob


def test_secrets_file_is_restricted(tmp_path):
    """The secrets file must be restricted to the current user. POSIX expresses this as mode
    0600; Windows has no such bits, so we assert the ACL instead (inheritance stripped, only
    the current user granted)."""
    path = tmp_path / "secrets.json"
    SecretStore(path).put("x", {"a": 1})
    if sys.platform == "win32":
        out = subprocess.run(
            ["icacls", str(path)], capture_output=True, text=True
        ).stdout
        user = os.environ.get("USERNAME", "")
        assert user and user in out  # current user is granted
        # Inherited broad principals must be gone after /inheritance:r.
        assert "NT AUTHORITY\\SYSTEM" not in out
        assert "BUILTIN\\Administrators" not in out
    else:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_delete(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("x", {"a": 1})
    assert store.delete("x") is True
    assert store.delete("x") is False
    assert store.get("x") is None


def test_acl_verification_failure_marks_degraded_without_raising(tmp_path, monkeypatch):
    """When ACL hardening can't be verified, saving must still succeed but the degraded
    state must be persisted (marker file) so callers/UI can surface it.

    The Windows ACL path runs `icacls` to APPLY the restriction then `_windows_acl_ok`
    to VERIFY it. On a Linux runner `icacls` doesn't exist, so the apply `subprocess.run`
    raises FileNotFoundError and `_restrict_to_user` returns False via its `except OSError`
    branch — WITHOUT ever reaching the mocked `_windows_acl_ok`, so the "verified" write
    below would never clear the marker. Simulate the Windows shell: the icacls APPLY call
    succeeds (no raise), leaving the mocked `_windows_acl_ok` as the sole verify oracle.
    The real Windows path (test_secrets_file_is_restricted) still exercises live icacls."""
    import coworker.secrets as secrets_mod

    def _fake_icacls_apply(args, *a, **kw):
        # The apply call (`icacls <path> /inheritance:r /grant:r ...`) must not raise on
        # Linux; return a clean CompletedProcess so the verify oracle controls the outcome.
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    path = tmp_path / "secrets.json"
    store = SecretStore(path)
    monkeypatch.setattr(secrets_mod, "_IS_WINDOWS", True)
    # Linux runners have no USERNAME env (they use USER); the Windows branch reads it
    # before any icacls call, so without it _restrict_to_user returns False early and
    # the apply/verify mocks below never run. Simulate the Windows shell's env too.
    monkeypatch.setenv("USERNAME", "testuser")
    monkeypatch.setattr(secrets_mod.subprocess, "run", _fake_icacls_apply)
    monkeypatch.setattr(secrets_mod, "_windows_acl_ok", lambda p: False)
    store.put("x", {"a": 1})  # must not raise
    assert store.get("x") == {"a": 1}
    assert store.acl_unprotected() is True
    # A later verified write clears the degraded flag.
    monkeypatch.setattr(secrets_mod, "_windows_acl_ok", lambda p: True)
    store.put("y", {"b": 2})
    assert store.acl_unprotected() is False


def test_corrupt_file_backed_up_not_overwritten(tmp_path):
    """A corrupt secrets file must be preserved as a `.corrupt-<ts>` sibling before a later
    save can overwrite it; loading degrades to empty state."""
    path = tmp_path / "secrets.json"
    path.write_text('{"slack": {"bot_token": "xoxb', encoding="utf-8")  # truncated JSON
    store = SecretStore(path)
    assert store._read() == {}
    backups = list(tmp_path.glob("secrets.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8").startswith('{"slack"')
    # A subsequent save produces a clean file; the damaged copy is retained.
    store.put("x", {"a": 1})
    assert store.get("x") == {"a": 1}
    assert len(list(tmp_path.glob("secrets.json.corrupt-*"))) == 1


def test_healthy_store_reports_acl_protected(tmp_path):
    """Happy path unchanged: a normal write verifies protection and sets no degraded flag."""
    path = tmp_path / "secrets.json"
    store = SecretStore(path)
    store.put("x", {"a": 1})
    assert store.acl_unprotected() is False
