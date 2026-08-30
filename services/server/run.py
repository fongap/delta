"""Launch the server with uvicorn. Used by the desktop GUI sidecar and `delta-server`."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from pathlib import Path

from packages.config import load_config
from core.permissions import Mode
from packages.secrets import state_dir, write_private_text
from services.server.app import _WS_MAX_FRAME_BYTES, create_app
from services.server.manager import SessionManager


def _exit_when_orphaned() -> None:
    """When launched as a desktop sidecar (`DELTA_EXIT_WITH_PARENT=1`), exit if the parent
    process dies — even on an abrupt kill (e.g. the Tauri dev watcher restarting the app, or a
    crash) that skips the shell's graceful child-kill. Standalone `delta-server` runs are
    unaffected.

    The GUI passes its own PID in `DELTA_PARENT_PID`. Watching that explicit PID (not
    getppid) is what makes this work under PyInstaller onefile, where this process is a
    *grandchild* of the GUI — the bootloader sits in between, so getppid() points at the
    bootloader and a re-parenting check never fires when the GUI dies (the bug that leaked
    a server pair on every app quit).

    POSIX: poll the PID with kill(pid, 0). Windows: no re-parenting semantics at all, so
    block on a process handle and exit the moment it signals (i.e. the parent exited).
    """
    if os.environ.get("DELTA_EXIT_WITH_PARENT") != "1":
        return
    import threading

    try:
        parent = int(os.environ.get("DELTA_PARENT_PID") or 0)
    except ValueError:
        parent = 0
    parent = parent or os.getppid()  # standalone fallback: our direct spawner

    if sys.platform == "win32":
        _watch_parent_windows(parent)
        return

    import time

    original_ppid = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(1.5)
            try:
                os.kill(parent, 0)  # liveness probe only; signal 0 delivers nothing
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass  # alive, but owned by someone else (shouldn't happen) — keep waiting
            # Secondary signal: our direct parent died (covers PID-reuse edge cases).
            if os.getppid() != original_ppid:
                os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def _watch_parent_windows(parent: int) -> None:
    """Block on a handle to the parent process; exit only when it actually terminates.

    Best-effort — any failure leaves the parent's RunEvent::ExitRequested kill as the primary
    cleanup path. Two correctness points that bit us before:
      - `OpenProcess` returns a 64-bit HANDLE; ctypes defaults the return type to a 32-bit int,
        which truncates the handle to garbage. Declare restype/argtypes so the handle is valid.
      - Only `os._exit` on WAIT_OBJECT_0 (the parent genuinely died). A bad handle yields
        WAIT_FAILED immediately — treating that as "parent died" would kill a perfectly healthy
        server seconds after startup (exactly the freeze we saw)."""
    import ctypes
    import threading
    from ctypes import wintypes

    SYNCHRONIZE = 0x0010_0000
    INFINITE = 0xFFFF_FFFF
    WAIT_OBJECT_0 = 0x0000_0000

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent)
    if not handle:
        return

    def watch() -> None:
        if kernel32.WaitForSingleObject(handle, INFINITE) == WAIT_OBJECT_0:
            os._exit(0)
        # Not parent-death (WAIT_FAILED on a stale handle, etc.) — release the handle
        # and let the daemon thread end; os._exit reclaims it on the death path anyway.
        kernel32.CloseHandle(handle)

    threading.Thread(target=watch, daemon=True).start()


def build_app(workspace: str | None, model: str, mode: str):
    manager = SessionManager(
        workspace=Path(workspace).expanduser().resolve() if workspace else None,
        data_dir=state_dir(),
        model=model,
        mode=Mode(mode),
    )
    # Cold-start recovery (docs/run-ledger-adr.md): any run left without a terminal
    # event by a crash/quit gets a synthetic `run.interrupted` — its durable prefix
    # survives as the factual record of what it did before dying.
    recovered = manager.run_ledger.recover_stale()
    if recovered:
        logging.getLogger("services.server").warning(
            "run ledger: recovered %d stale run(s) with synthetic interrupted events",
            len(recovered),
        )
    return create_app(manager)


def _ensure_ca_bundle() -> None:
    """Point SSL at certifi's CA bundle if the interpreter has none configured. macOS framework
    Python ships without a usable system trust store for `aiohttp` (it builds an `ssl` context with
    no CAs), so the Slack Socket-Mode client fails with CERTIFICATE_VERIFY_FAILED. `httpx`/`requests`
    bundle certifi already; aiohttp honours the SSL_CERT_FILE env var, so set it once at startup.
    """
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass


def _ensure_api_token(port: int) -> Path | None:
    """Set launch auth; standalone/dev tokens use a user-only, port-specific file."""
    if os.environ.get("DELTA_API_TOKEN"):
        return None  # Tauri supplied an in-memory token; never persist it.
    token = secrets.token_hex(32)
    os.environ["DELTA_API_TOKEN"] = token
    return write_private_text(
        state_dir() / f"sidecar-{port}.token", token + "\n"
    )


def main(argv=None) -> None:
    _ensure_ca_bundle()
    cfg = load_config()  # global config supplies defaults
    parser = argparse.ArgumentParser(prog="delta-server")
    parser.add_argument("--cwd", default=None, help="optional seed/default workspace")
    parser.add_argument("--model", default=cfg.model)
    parser.add_argument(
        "--mode",
        default=cfg.mode,
        choices=["discuss", "plan", "interactive", "auto"],
    )
    parser.add_argument("--host", default=cfg.host)
    parser.add_argument("--port", type=int, default=cfg.port)
    args = parser.parse_args(argv)

    # Publish the ACTUAL bound port so loopback URLs (the managed-OAuth callback)
    # target this process, not config.port. The desktop shell runs the sidecar on
    # a random free port (to coexist with a hand-run server on 8765), so the
    # managed-connect redirect must follow the real port, not the 8765 default.
    os.environ["DELTA_PORT"] = str(args.port)
    generated_token_path = _ensure_api_token(args.port)
    try:
        import uvicorn

        _exit_when_orphaned()
        app = build_app(args.cwd, args.model, args.mode)
        uvicorn.run(
            app, host=args.host, port=args.port, ws_max_size=_WS_MAX_FRAME_BYTES
        )
    finally:
        if generated_token_path is not None:
            generated_token_path.unlink(missing_ok=True)
            os.environ.pop("DELTA_API_TOKEN", None)


if __name__ == "__main__":
    main()
