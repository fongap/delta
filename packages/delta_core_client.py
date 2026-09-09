"""Delta Core host client — Python interface to the delta-core Rust process.

R1 (P1-E): the unified Delta Core process entrypoint. Instead of
spawning a fresh `write_idemlog` / `write_ledger`
subprocess for every command, the Python delegate modules hold a
persistent connection to a single long-running ``delta_core``
process.

This module provides the :class:`DeltaCoreClient` that wraps a
``delta_core`` subprocess. Commands are line-delimited JSON on
stdin; responses are line-delimited JSON on stdout.

Public Contract (R1):

* The client is **process-local**; one client per
  :class:`SessionManager` (or equivalent). Multiple threads may
  share a single client; access is serialized by a lock.
* The protocol is fire-and-await: a command blocks until its
  response arrives, then returns the ``result`` or raises the
  ``error``.
* Crashes (subprocess exit) raise :class:`DeltaCoreError`. The
  caller is responsible for restart policy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CRATE_DIR = REPO_ROOT / "core" / "runtime-native"

# Default command timeout in seconds. The Rust process answers
# most commands in microseconds; this is a watchdog for hangs /
# deadlocks (e.g. lock contention on a SQLite connection). Callers
# can override per-client via the ``command_timeout`` constructor
# argument.
DEFAULT_COMMAND_TIMEOUT_SECONDS: float = 30.0

# Delta Core wire-protocol version. Must match the
# ``PROTOCOL_VERSION`` constant in
# ``core/runtime-native/src/bin/delta_core.rs``. The Python client
# sends this in a ``hello`` command immediately after subprocess
# startup; a mismatch raises :class:`DeltaCoreError` (fail-closed).
PROTOCOL_VERSION: int = 7


def _find_delta_core_binary() -> Path | None:
    """Locate the delta_core binary in the standard search order.

    Lookup order:

    1. ``DELTA_CORE_BINARY`` env var (explicit override).
    2. Same directory as the running Python executable — matches the
       Windows Portable layout (``App/Delta/delta_core.exe`` next to
       ``App/Delta/Delta.exe``).
    3. Parent directories of the Python executable — covers the
       PyInstaller onedir sidecar layout where ``sys.executable`` is
       ``App/Delta/sidecar/delta-server/delta-server.exe`` and the
       binary lives at ``App/Delta/delta_core.exe`` (up to 3 levels).
    4. ``DELTA_PORTABLE_ROOT`` env var — set by the portable launcher;
       binary is at ``App/Delta/delta_core.exe`` relative to root.
    5. The Tauri resource path (``sys._MEIPASS`` when frozen).
    6. The repo's dev build (``core/runtime-native/target/...``).

    Returns ``None`` if no candidate is found. The caller decides
    whether to raise (fail-closed) or to skip the delegate.
    """
    target = "delta_core.exe" if sys.platform == "win32" else "delta_core"

    env = os.environ.get("DELTA_CORE_BINARY")
    if env and Path(env).exists():
        return Path(env)

    py_exe = Path(sys.executable).resolve()
    py_dir = py_exe.parent
    if (py_dir / target).exists():
        return py_dir / target

    for level in range(1, 4):
        ancestor = py_exe.parents[level] if len(py_exe.parents) > level else None
        if ancestor and (ancestor / target).exists():
            return ancestor / target

    portable_root = os.environ.get("DELTA_PORTABLE_ROOT")
    if portable_root:
        candidate = Path(portable_root) / "App" / "Delta" / target
        if candidate.exists():
            return candidate

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(meipass) / target
        if meipass_path.exists():
            return meipass_path

    for profile in ("release", "debug"):
        candidate = CRATE_DIR / "target" / profile / target
        if candidate.exists():
            return candidate
    return None


class DeltaCoreError(RuntimeError):
    """Raised when the delta_core subprocess returns an error or dies."""


class DeltaCoreClient:
    """Persistent client to the delta_core Rust process.

    The client spawns ``delta_core`` on first use, sends line-delimited
    JSON commands, and parses line-delimited JSON responses. The
    underlying connection is reused for every command in the
    subprocess's lifetime.

    The ``core/idemlog_delegate.py``, ``core/ledger_delegate.py``,
    and ``core/automation/store_delegate.py`` modules use this client
    when the unified process is available. When authority is declared
    but the binary is missing, the delegates raise
    :class:`DeltaCoreError` (fail-closed) rather than falling back.

    P1-1 lifecycle guarantees:

    * **Startup failure** — a missing or non-executable binary
      raises :class:`DeltaCoreError` on first use.
    * **Subprocess crash** — if the subprocess dies (e.g. SIGSEGV,
      panic) the next ``command()`` call restarts it.
    * **Broken pipe** — ``BrokenPipeError`` on ``stdin.write`` is
      caught; the client restarts on the next call.
    * **Non-JSON response** — ``json.JSONDecodeError`` on the
      response line is caught and re-raised as
      :class:`DeltaCoreError`.
    * **Command timeout** — ``stdout.readline()`` runs under a
      timeout (``command_timeout`` constructor arg, default
      :data:`DEFAULT_COMMAND_TIMEOUT_SECONDS`). A timeout closes the
      client and raises :class:`DeltaCoreError`.
    * **Clean shutdown** — :meth:`close` closes stdin, waits for the
      subprocess to exit, kills + reaps on timeout. Idempotent.
    * **Restart policy** — after any failure, the next ``command()``
      spawns a fresh subprocess automatically.
    * **stderr draining** — a background thread reads stderr
      continuously so the Rust process's stderr pipe can never
      fill up and deadlock the subprocess. The thread exits
      cleanly on :meth:`close`.
    """

    def __init__(
        self,
        binary_path: Path | None = None,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        if binary_path is None:
            binary_path = _find_delta_core_binary()
            if binary_path is None:
                raise DeltaCoreError(
                    "delta_core binary not found in any standard location; "
                    "set DELTA_CORE_BINARY or build core/runtime-native"
                )
        self._binary_path = binary_path
        self._command_timeout = command_timeout
        self._proc: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_stop = threading.Event()
        self._lock = threading.RLock()

    @staticmethod
    def _find_binary() -> Path | None:
        """Module-level binary lookup — used by delegates to check availability."""
        return _find_delta_core_binary()

    @property
    def binary_path(self) -> Path:
        return self._binary_path

    def _stderr_drainer(self, proc: subprocess.Popen) -> threading.Thread:
        """Start a background thread that drains ``proc.stderr``.

        The Rust process is contractually silent on stderr under
        normal operation. This drainer exists as a deadlock guard:
        if the subprocess ever writes to stderr and the parent
        never reads, the OS pipe buffer fills (typically 4-64 KB)
        and the subprocess blocks on its next ``write``. The
        drainer reads line by line until EOF or stop signal.

        Returns the thread for join-on-close.
        """
        stop = self._stderr_stop

        def drain():
            try:
                stream = proc.stderr
                if stream is None:
                    return
                while not stop.is_set():
                    line = stream.readline()
                    if not line:
                        return
            except Exception:
                return

        thread = threading.Thread(
            target=drain, name="delta-core-stderr-drain", daemon=True
        )
        thread.start()
        return thread

    def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        if not self._binary_path.exists():
            raise DeltaCoreError(
                f"delta_core binary not built: {self._binary_path}"
            )
        self._proc = subprocess.Popen(
            [str(self._binary_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._stderr_stop.clear()
        self._stderr_thread = self._stderr_drainer(self._proc)
        self._handshake()

    def _handshake(self) -> None:
        """Send a ``hello`` command and verify the protocol version.

        Called immediately after subprocess startup. If the server
        reports a different protocol version, the subprocess is
        closed and :class:`DeltaCoreError` is raised (fail-closed).
        This prevents the Python runtime from silently talking to a
        stale or upgraded binary.
        """
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise DeltaCoreError("delta_core subprocess not started")
        cmd = json.dumps({"cmd": "hello", "protocol_version": PROTOCOL_VERSION})
        try:
            self._proc.stdin.write(cmd + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self._proc = None
            raise DeltaCoreError(f"delta_core handshake write failed: {e}") from e
        line = self._proc.stdout.readline()
        if not line:
            self._proc = None
            raise DeltaCoreError("delta_core handshake: empty response (subprocess died?)")
        try:
            resp = json.loads(line)
        except json.JSONDecodeError as e:
            self.close()
            raise DeltaCoreError(f"delta_core handshake: non-JSON response: {line!r}") from e
        if not resp.get("ok"):
            self.close()
            raise DeltaCoreError(
                f"delta_core handshake rejected: {resp.get('error', 'unknown error')}"
            )
        server_version = (resp.get("result") or {}).get("protocol_version")
        if server_version != PROTOCOL_VERSION:
            self.close()
            raise DeltaCoreError(
                f"delta_core protocol version mismatch: "
                f"client={PROTOCOL_VERSION}, server={server_version}"
            )

    def close(self) -> None:
        """Close the subprocess. Subsequent commands will restart it.

        Safe to call multiple times and safe against an already-dead
        process. Kill + reap on timeout so no zombie is left behind.
        The stderr drainer thread is signaled to stop and joined
        with a short timeout.
        """
        with self._lock:
            proc = self._proc
            thread = self._stderr_thread
            self._proc = None
            self._stderr_thread = None
            if proc is None:
                self._stderr_stop.set()
                return
            try:
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
            finally:
                self._stderr_stop.set()
                if thread is not None and thread.is_alive():
                    thread.join(timeout=1.0)

    def _readline_with_timeout(self, stream, timeout: float) -> str:
        """Read one line from ``stream`` with a timeout.

        Uses a background thread + ``join(timeout)`` so the
        underlying ``readline()`` is always interruptible. Returns
        the line on success, raises :class:`DeltaCoreError` on
        timeout.
        """
        result: list[str] = []
        error: list[BaseException] = []

        def reader():
            try:
                result.append(stream.readline())
            except BaseException as exc:
                error.append(exc)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise DeltaCoreError(
                f"delta_core command timed out after {timeout}s"
            )
        if error:
            raise DeltaCoreError(f"delta_core readline failed: {error[0]}")
        return result[0] if result else ""

    def command(self, payload: dict[str, Any]) -> Any:
        """Send one command, return the ``result`` field of the response.

        Raises :class:`DeltaCoreError` if the subprocess returns
        ``ok: false``, dies before responding, or exceeds the
        configured command timeout.
        """
        with self._lock:
            self._ensure_started()
            proc = self._proc
            if proc is None or proc.stdin is None or proc.stdout is None:
                self.close()
                raise DeltaCoreError("delta_core process pipes unavailable")
            line = json.dumps(payload)
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self.close()
                raise DeltaCoreError(f"delta_core stdin write failed: {exc}") from exc
            try:
                response_line = self._readline_with_timeout(
                    proc.stdout, self._command_timeout
                )
            except DeltaCoreError:
                self.close()
                raise
            if not response_line:
                self.close()
                raise DeltaCoreError("delta_core closed stdout (crash?)")
            try:
                response = json.loads(response_line)
            except json.JSONDecodeError as exc:
                self.close()
                raise DeltaCoreError(
                    f"delta_core returned non-JSON: {response_line!r}"
                ) from exc
            if not response.get("ok"):
                raise DeltaCoreError(
                    f"delta_core {payload.get('cmd')}: {response.get('error')}"
                )
            return response.get("result")


_default_client: DeltaCoreClient | None = None
_default_lock = threading.Lock()


def default_client() -> DeltaCoreClient:
    """Return the process-wide shared delta_core client.

    The client is created lazily on first use and reused for every
    subsequent command. Close it via :func:`close_default_client`
    at process shutdown.
    """
    global _default_client
    with _default_lock:
        if _default_client is None:
            _default_client = DeltaCoreClient()
        return _default_client


def close_default_client() -> None:
    """Close the process-wide shared client. Safe to call multiple times."""
    global _default_client
    with _default_lock:
        if _default_client is not None:
            _default_client.close()
            _default_client = None
