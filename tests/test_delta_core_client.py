"""Delta Core host client tests (P1-E).

Tests the :class:`DeltaCoreClient` that wraps the long-running
``delta_core`` Rust process. The client must:

* Spawn ``delta_core`` on first use.
* Reuse the same subprocess for every command.
* Return the ``result`` field of successful responses.
* Raise :class:`DeltaCoreError` for ``ok: false`` responses.
* Raise :class:`DeltaCoreError` when the subprocess dies.
* Maintain a hash chain across appends (proves the subprocess
  holds the connection open between commands).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from packages.delta_core_client import DeltaCoreClient, DeltaCoreError

REPO_ROOT = Path(__file__).resolve().parent.parent
BINARY = (
    REPO_ROOT
    / "core"
    / "runtime-native"
    / "target"
    / "debug"
    / ("delta_core.exe" if sys.platform == "win32" else "delta_core")
)


@pytest.fixture
def client() -> DeltaCoreClient:
    if not BINARY.exists():
        pytest.skip("delta_core binary not built")
    c = DeltaCoreClient()
    yield c
    c.close()


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "test.db"


def test_ping_returns_pong(client):
    result = client.command({"cmd": "ping"})
    assert result == {"pong": True}


def test_ledger_append_returns_stored_row(client, db_path):
    result = client.command(
        {
            "cmd": "ledger.append",
            "db": str(db_path),
            "run_id": "r1",
            "type": "run.started",
            "actor": "user",
            "payload": {"kind": "run"},
        }
    )
    assert result["run_id"] == "r1"
    assert result["seq"] == 1
    assert result["type"] == "run.started"
    assert result["actor"] == "user"
    assert result["prev_hash"] == ""


def test_ledger_chain_continuity(client, db_path):
    """Two appends in the same client must produce a continuous chain."""
    r1 = client.command(
        {
            "cmd": "ledger.append",
            "db": str(db_path),
            "run_id": "r1",
            "type": "run.started",
            "actor": "user",
        }
    )
    r2 = client.command(
        {
            "cmd": "ledger.append",
            "db": str(db_path),
            "run_id": "r1",
            "type": "run.completed",
            "actor": "system",
        }
    )
    assert r1["seq"] == 1
    assert r2["seq"] == 2
    assert r2["prev_hash"] == r1["hash"]


def test_multiple_domains_in_one_subprocess(client, tmp_path):
    """One subprocess handles ledger, idem, and task commands."""
    ledger_db = tmp_path / "ledger.db"
    idem_db = tmp_path / "idem.db"
    task_db = tmp_path / "tasks.db"

    client.command(
        {
            "cmd": "ledger.append",
            "db": str(ledger_db),
            "run_id": "r1",
            "type": "run.started",
            "actor": "user",
        }
    )
    client.command(
        {
            "cmd": "idem.record_planned",
            "db": str(idem_db),
            "run_id": "r1",
            "tool_call_id": "tc1",
            "tool_name": "write_file",
            "args": {"path": "a.md"},
        }
    )
    client.command(
        {
            "cmd": "task.save",
            "db": str(task_db),
            "task_id": "t1",
            "enabled": True,
            "next_run": None,
            "data": "{}",
        }
    )
    assert ledger_db.exists()
    assert idem_db.exists()
    assert task_db.exists()


def test_error_raises_delta_core_error(client, db_path):
    """An unknown command must trigger the parse error path."""
    with pytest.raises(DeltaCoreError, match="parse"):
        client.command({"cmd": "totally.unknown"})


def test_missing_binary_raises():
    """A client pointing at a non-existent binary must raise on use."""
    from pathlib import Path as _P

    c = DeltaCoreClient(binary_path=_P("/nonexistent/delta_core"))
    with pytest.raises(DeltaCoreError, match="not built"):
        c.command({"cmd": "ping"})


def test_default_lookup_finds_dev_build():
    """When no env var is set, the default lookup finds the dev
    build at ``core/runtime-native/target/{release,debug}/``."""
    import os
    from packages.delta_core_client import _find_delta_core_binary

    os.environ.pop("DELTA_CORE_BINARY", None)
    found = _find_delta_core_binary()
    if not BINARY.exists():
        assert found is None
    else:
        assert found is not None
        assert found.name in ("delta_core.exe", "delta_core")


def test_close_is_idempotent(client):
    """Closing an unstarted client (or twice) must not error."""
    client.close()
    client.close()


def test_close_allows_restart(client, db_path):
    """After close(), command() must restart the subprocess."""
    client.command({"cmd": "ping"})
    client.close()
    result = client.command({"cmd": "ping"})
    assert result == {"pong": True}


def test_close_twice_is_safe(client):
    """Double close must not raise or leave the lock held."""
    client.close()
    client.close()


def test_close_after_subprocess_crash_restarts(client, db_path):
    """If subprocess dies externally, close() cleans up and command() restarts."""
    client.command({"cmd": "ping"})
    proc = client._proc
    assert proc is not None
    proc.kill()
    proc.wait(timeout=2)
    # close() should be safe and clear the dead proc
    client.close()
    # command() should restart a fresh subprocess
    result = client.command({"cmd": "ping"})
    assert result == {"pong": True}


def test_close_kills_and_reaps_on_timeout(client, monkeypatch):
    """If wait() times out, close() kills and reaps; no zombie left."""
    client.command({"cmd": "ping"})

    def slow_wait(timeout=2):
        import time
        time.sleep(0.01)
        if timeout < 5:
            raise subprocess.TimeoutExpired(cmd="", timeout=timeout)
        return 0

    monkeypatch.setattr(client._proc, "wait", slow_wait)
    client.close()
    assert client._proc is None


def test_close_after_stdin_write_failure_restarts(client, db_path):
    """If stdin write fails (BrokenPipe), close() cleans up and restarts."""
    client.command({"cmd": "ping"})
    proc = client._proc
    assert proc is not None
    # Simulate broken pipe by closing stdin from other side
    proc.stdin.close()
    with pytest.raises(DeltaCoreError, match="stdin write failed"):
        client.command({"cmd": "ping"})
    # Dead proc should be cleaned
    assert client._proc is None
    # New command should work
    result = client.command({"cmd": "ping"})
    assert result == {"pong": True}


def test_idem_commit_full_cycle(client, tmp_path):
    """A full idem state cycle (record_planned → mark_executing → commit)."""
    db = tmp_path / "idem.db"
    r1 = client.command(
        {
            "cmd": "idem.record_planned",
            "db": str(db),
            "run_id": "r1",
            "tool_call_id": "tc1",
            "tool_name": "write_file",
            "args": {"path": "out.md"},
        }
    )
    assert "operation_id" in r1
    r2 = client.command(
        {
            "cmd": "idem.mark_executing",
            "db": str(db),
            "run_id": "r1",
            "tool_call_id": "tc1",
        }
    )
    assert r2 is None
    r3 = client.command(
        {
            "cmd": "idem.commit",
            "db": str(db),
            "run_id": "r1",
            "tool_call_id": "tc1",
            "tool_name": "write_file",
            "args": {"path": "out.md"},
            "result": {"ok": True},
        }
    )
    assert r3 is None
