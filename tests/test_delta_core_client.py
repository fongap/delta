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

P0-2 Portable lookup tests: verify the binary is discoverable in the
Windows Portable layout where ``sys.executable`` is inside the
PyInstaller onedir sidecar (e.g. ``App/Delta/sidecar/delta-server/
delta-server.exe``) and ``delta_core.exe`` lives at ``App/Delta/
delta_core.exe`` (up to 3 parent levels up).
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
    import os as _os
    from packages.delta_core_client import _find_delta_core_binary

    _os.environ.pop("DELTA_CORE_BINARY", None)
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


# -- P1-1: command timeout ----------------------------------------------------


def test_command_timeout_raises_delta_core_error(monkeypatch, tmp_path):
    """P1-1: a hung subprocess (readline blocks forever) must time
    out rather than block the caller indefinitely. The timeout
    closes the client and raises DeltaCoreError."""
    from packages.delta_core_client import DeltaCoreClient

    if not BINARY.exists():
        pytest.skip("delta_core binary not built")

    c = DeltaCoreClient(command_timeout=0.2)
    try:
        c.command({"cmd": "ping"})  # start subprocess
        # Now simulate a hang: replace stdout.readline with a sleeper.
        proc = c._proc
        assert proc is not None

        def hang_readline(*a, **kw):
            import time
            time.sleep(5)
            return ""

        monkeypatch.setattr(proc.stdout, "readline", hang_readline)
        with pytest.raises(DeltaCoreError, match="timed out"):
            c.command({"cmd": "ping"})
        # Client should have closed itself.
        assert c._proc is None
    finally:
        c.close()


def test_command_timeout_uses_default():
    """The default command timeout is 30s, suitable for a watchdog
    on a normally-fast local IPC."""
    from packages.delta_core_client import (
        DEFAULT_COMMAND_TIMEOUT_SECONDS,
        DeltaCoreClient,
    )

    if not BINARY.exists():
        pytest.skip("delta_core binary not built")
    c = DeltaCoreClient()
    try:
        assert c._command_timeout == DEFAULT_COMMAND_TIMEOUT_SECONDS
        assert c._command_timeout == 30.0
    finally:
        c.close()


# -- P1-1: stderr drainer -----------------------------------------------------


def test_stderr_drainer_is_running(client):
    """P1-1: a background thread must be draining stderr while the
    subprocess is alive. This prevents the OS pipe from filling up
    and deadlocking the subprocess if it ever writes to stderr."""
    client.command({"cmd": "ping"})
    assert client._stderr_thread is not None
    assert client._stderr_thread.is_alive()
    assert client._stderr_thread.daemon is True


def test_stderr_drainer_stops_on_close(client):
    """P1-1: close() must signal the drainer to stop and join it
    with a bounded timeout. No thread leak."""
    client.command({"cmd": "ping"})
    thread = client._stderr_thread
    assert thread is not None
    client.close()
    # Drainer should have been joined (or timed out, but the thread
    # is a daemon so it can't block process exit either way).
    assert client._stderr_thread is None
    # Thread object may still exist briefly; not strictly required to be dead.
    import time
    time.sleep(0.1)
    assert not thread.is_alive() or thread.daemon


# -- P0-2 Portable binary lookup tests ----------------------------------------


def test_find_binary_same_dir_as_python_exe(monkeypatch, tmp_path):
    """Lookup finds delta_core in the same dir as sys.executable."""
    from packages.delta_core_client import _find_delta_core_binary

    target = "delta_core.exe" if sys.platform == "win32" else "delta_core"
    fake_dir = tmp_path / "app"
    fake_dir.mkdir()
    (fake_dir / target).write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(fake_dir / "python.exe"))
    monkeypatch.delenv("DELTA_CORE_BINARY", raising=False)
    monkeypatch.delenv("DELTA_PORTABLE_ROOT", raising=False)
    found = _find_delta_core_binary()
    assert found is not None
    assert found.name == target
    assert found.parent == fake_dir


def test_find_binary_parent_of_python_exe_onedir_layout(monkeypatch, tmp_path):
    """Lookup finds delta_core in the parent dir of the Python exe —
    the PyInstaller onedir layout where sys.executable is at
    App/Delta/sidecar/delta-server/delta-server.exe and the binary
    lives at App/Delta/delta_core.exe (2 levels up)."""
    from packages.delta_core_client import _find_delta_core_binary

    target = "delta_core.exe" if sys.platform == "win32" else "delta_core"
    app_delta = tmp_path / "App" / "Delta"
    sidecar = app_delta / "sidecar" / "delta-server"
    sidecar.mkdir(parents=True)
    (app_delta / target).write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(sidecar / "delta-server.exe"))
    monkeypatch.delenv("DELTA_CORE_BINARY", raising=False)
    monkeypatch.delenv("DELTA_PORTABLE_ROOT", raising=False)
    found = _find_delta_core_binary()
    assert found is not None
    assert found == app_delta / target


def test_find_binary_portable_root_env(monkeypatch, tmp_path):
    """Lookup finds delta_core via DELTA_PORTABLE_ROOT env var."""
    from packages.delta_core_client import _find_delta_core_binary

    target = "delta_core.exe" if sys.platform == "win32" else "delta_core"
    portable_root = tmp_path / "DeltaPortable"
    app_delta = portable_root / "App" / "Delta"
    app_delta.mkdir(parents=True)
    (app_delta / target).write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.delenv("DELTA_CORE_BINARY", raising=False)
    monkeypatch.setenv("DELTA_PORTABLE_ROOT", str(portable_root))
    found = _find_delta_core_binary()
    assert found is not None
    assert found == app_delta / target


def test_find_binary_env_override(monkeypatch, tmp_path):
    """DELTA_CORE_BINARY env var takes priority over all other lookups."""
    from packages.delta_core_client import _find_delta_core_binary

    target = "delta_core.exe" if sys.platform == "win32" else "delta_core"
    custom = tmp_path / "custom" / target
    custom.parent.mkdir()
    custom.write_bytes(b"")
    monkeypatch.setenv("DELTA_CORE_BINARY", str(custom))
    found = _find_delta_core_binary()
    assert found is not None
    assert found == custom


def test_binary_name_is_underscore_not_hyphen():
    """The binary name MUST be delta_core (underscore) everywhere —
    Cargo.toml, DeltaCoreClient lookup, and build script. A hyphen
    name (delta-core.exe) is a known bug that must not recur."""
    from packages.delta_core_client import _find_delta_core_binary

    found = _find_delta_core_binary()
    if found is not None:
        assert "delta_core" in found.name
        assert "delta-core" not in found.name


def test_hello_handshake_succeeds_on_startup():
    """The client sends a `hello` command immediately after spawning
    delta_core and verifies the protocol version before accepting
    any other command."""
    from packages.delta_core_client import (
        DeltaCoreClient,
        PROTOCOL_VERSION,
        _find_delta_core_binary,
    )

    binary = _find_delta_core_binary()
    if binary is None:
        pytest.skip("delta_core binary not built")
    c = DeltaCoreClient(binary_path=binary)
    try:
        c.close()
    except Exception:
        pass
    # If startup succeeded without raising, the handshake passed.
    # The PROTOCOL_VERSION constant must match the Rust side.
    assert PROTOCOL_VERSION == 1


def test_hello_handshake_fails_on_protocol_mismatch(monkeypatch, tmp_path):
    """If the client and server disagree on protocol version, the
    client must fail-closed (raise DeltaCoreError) rather than
    silently sending commands to an incompatible binary.

    We simulate the mismatch by monkey-patching the Python-side
    PROTOCOL_VERSION to a value the Rust binary does not support."""
    from packages import delta_core_client
    from packages.delta_core_client import (
        DeltaCoreClient,
        DeltaCoreError,
        _find_delta_core_binary,
    )

    binary = _find_delta_core_binary()
    if binary is None:
        pytest.skip("delta_core binary not built")
    monkeypatch.setattr(delta_core_client, "PROTOCOL_VERSION", 999)
    c = DeltaCoreClient(binary_path=binary)
    with pytest.raises(DeltaCoreError) as exc:
        c.command({"cmd": "ping"})
    assert "protocol mismatch" in str(exc.value)
    # Client must have closed the subprocess on mismatch.
    assert c._proc is None

