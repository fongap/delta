"""Delta Core host client — Python interface to the delta-core Rust process.

R1 (P1-E): the unified Delta Core process entrypoint. Instead of
spawning a fresh `write_idemlog` / `write_ledger` / `write_tasks`
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


def _find_delta_core_binary() -> Path | None:
    """Locate the delta_core binary in the standard search order.

    Lookup order:

    1. ``DELTA_CORE_BINARY`` env var (explicit override).
    2. Same directory as the running Python executable — matches the
       Windows Portable layout (``App/Delta/delta-core.exe`` next to
       ``App/Delta/Delta.exe``).
    3. The Tauri resource path (``sys._MEIPASS`` when frozen).
    4. The repo's dev build (``core/runtime-native/target/...``).

    Returns ``None`` if no candidate is found. The caller decides
    whether to raise or to fall back to the per-operation CLI
    binaries.
    """
    target = "delta_core.exe" if sys.platform == "win32" else "delta_core"

    env = os.environ.get("DELTA_CORE_BINARY")
    if env and Path(env).exists():
        return Path(env)

    py_dir = Path(sys.executable).resolve().parent / target
    if py_dir.exists():
        return py_dir

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
    when the unified process is available; they fall back to the
    per-operation CLI binaries otherwise.
    """

    def __init__(self, binary_path: Path | None = None) -> None:
        if binary_path is None:
            binary_path = _find_delta_core_binary()
            if binary_path is None:
                raise DeltaCoreError(
                    "delta_core binary not found in any standard location; "
                    "set DELTA_CORE_BINARY or build core/runtime-native"
                )
        self._binary_path = binary_path
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def binary_path(self) -> Path:
        return self._binary_path

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
            bufsize=1,
        )

    def close(self) -> None:
        """Close the subprocess. Subsequent commands will restart it."""
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass
                try:
                    self._proc.wait(timeout=2)
                except Exception:
                    self._proc.kill()
                self._proc = None

    def command(self, payload: dict[str, Any]) -> Any:
        """Send one command, return the ``result`` field of the response.

        Raises :class:`DeltaCoreError` if the subprocess returns
        ``ok: false`` or dies before responding.
        """
        with self._lock:
            self._ensure_started()
            assert self._proc is not None
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None
            line = json.dumps(payload)
            try:
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._proc = None
                raise DeltaCoreError(f"delta_core stdin write failed: {exc}") from exc
            response_line = self._proc.stdout.readline()
            if not response_line:
                self._proc = None
                raise DeltaCoreError("delta_core closed stdout (crash?)")
            try:
                response = json.loads(response_line)
            except json.JSONDecodeError as exc:
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
