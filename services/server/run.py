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

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
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
    # Cold-start recovery (docs/architecture/adr/ADR-001-run-event-ledger.md): any run left without a terminal
    # event by a crash/quit gets a synthetic `run.interrupted` — its durable prefix
    # survives as the factual record of what it did before dying.
    recovered = list(manager.run_ledger.recover_stale())
    if recovered:
        logging.getLogger("services.server").warning(
            "run ledger: recovered %d stale run(s) with synthetic interrupted events",
            len(recovered),
        )
    # P0-A Side Effect Crash Safety: transition any Planned/Executing side
    # effects in interrupted runs to Uncertain so they are NEVER auto-replayed.
    interrupted_ids = [r["run_id"] for r in recovered]
    uncertain = manager.idem_log.sweep_stale(
        interrupted_ids, ledger=manager.run_ledger
    )
    if uncertain:
        logging.getLogger("services.server").warning(
            "side-effect log: %d uncertain side effect(s) across %d run(s) — "
            "surfacing for user resolution",
            len(uncertain),
            len(interrupted_ids),
        )
        for entry in uncertain:
            manager.inbox.add_run_issue(
                entry["run_id"],
                f"Uncertain side effect: {entry['tool_name']}",
                body=(
                    f"Run {entry['run_id']} was interrupted after planning a "
                    f"side effect ({entry['tool_name']}, operation "
                    f"{entry['operation_id']}). It is unknown whether the "
                    "operation executed. Please confirm, re-execute, or "
                    "dismiss."
                ),
                data={
                    "run_id": entry["run_id"],
                    "kind": "side_effect_uncertain",
                    "tool_call_id": entry["tool_call_id"],
                    "tool_name": entry["tool_name"],
                    "operation_id": entry["operation_id"],
                },
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


# Modules that MUST import cleanly in every official release build. The Delta runtime
# is wired through these packages; a missing import here means the sidecar will fail
# at the first request, not at startup. Kept aligned with the messaging/PDF/timezone
# extras declared in pyproject.toml and the packages delta-server.spec bundles.
_SELF_TEST_REQUIRED_MODULES: tuple[tuple[str, str], ...] = (
    # core responsibility packages — a missing one means the engine cannot run.
    ("core", "core runtime"),
    ("providers", "providers"),
    ("integrations", "integrations"),
    ("packages", "packages"),
    ("services", "services"),
    # Foundation deps used by the bundled sidecar.
    ("mcp", "MCP runtime"),
    ("pypdf", "PDF text extraction"),
    ("pypdfium2", "PDF rasterization"),
    ("croniter", "automation scheduler math"),
    ("certifi", "TLS CA bundle"),
    ("uvicorn", "ASGI server"),
    ("websockets", "WS client (managed capability port tests)"),
    ("aisuite", "provider abstraction"),
    # Windows-only: IANA tz database ships via this package; without it every named
    # timezone silently falls back to local time. Harmless on POSIX, fatal on Windows.
    ("tzdata", "IANA tz database (Windows)"),
    # Messaging runtime. The official release artifact promises Slack + Telegram; if
    # any of these is missing the sidecar degrades silently and the published product
    # loses the capability — exactly what R1 forbids.
    ("slack_bolt", "Slack Socket Mode runtime"),
    ("telegram", "Telegram bot runtime"),
    ("aiohttp", "Slack Socket Mode transport"),
)


def _self_test() -> int:
    """Verify the bundled Python runtime / dependency graph is intact.

    Offline: does not import any provider client, open a listening port, or touch
    user data. Walks the module list that the official release is contracted to ship
    and reports PASS/FAIL with a non-zero exit on any missing module. Intended to
    be called from `delta-server --self-test` in CI and from the post-build
    verification step in the release workflow.
    """
    missing: list[tuple[str, str]] = []
    for module, label in _SELF_TEST_REQUIRED_MODULES:
        try:
            __import__(module)
        except Exception as exc:
            missing.append((module, f"{label}: {exc}"))

    if missing:
        print("Delta sidecar self-test: FAIL", flush=True)
        for module, detail in missing:
            print(f"  MISSING {module} — {detail}", flush=True)
        return 1

    print("Delta sidecar self-test: PASS", flush=True)
    return 0


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
    parser.add_argument(
        "--self-test",
        action="store_true",
        help=(
            "Verify the bundled Python runtime / dependency graph is intact. "
            "Offline, no model/network calls, no user data. Exits 0 on PASS, non-zero on FAIL."
        ),
    )
    args = parser.parse_args(argv)
    if args.self_test:
        sys.exit(_self_test())

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
