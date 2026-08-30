# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the bundled `delta-server` (desktop sidecar).

One-DIR bundle (exe + `_internal/` support folder) shipped via Tauri's `resources` slot.
It used to be a onefile binary in the externalBin slot, but onefile self-extracts its whole
archive to a temp dir on EVERY launch — 6-7s of "Starting delta…" splash (measured; the
actual Python import is ~0.5s). The wrinkles handled here:
  - aisuite is a regular pip dependency (git-pinned in pyproject.toml); collect the
    responsibility packages + aisuite submodules from the venv.
  - uvicorn loads its protocol/lifespan impls dynamically → collect_all.
  - certifi's CA bundle must ship for TLS (OpenAI, web search, Telegram/Slack).
  - messaging extras (slack_bolt, telegram) are optional; collected if importable.

Cross-platform: paths are derived from this spec's own location (SPECPATH), never hardcoded,
so the same spec builds native binaries on macOS, Windows, and Linux. On Windows PyInstaller
appends `.exe` to `name`. The binary is built as a normal console app on every OS — a windowed
(console=False) build leaves sys.stdout/stderr as None, which breaks uvicorn's startup logging
and hangs the server. To avoid a console window flashing in the desktop app, the Tauri shell
spawns this sidecar with the Windows CREATE_NO_WINDOW flag (see src-tauri/src/lib.rs), which
hides the window while keeping stdio intact.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is injected by PyInstaller and points at this file's directory
# (<repo>/packaging/server). Derive everything else from it — no hardcoded paths.
PACKAGING = SPECPATH                 # packaging/server
ROOT = os.path.dirname(PACKAGING)    # packaging
REPO_ROOT = os.path.dirname(ROOT)    # <repo>
PYTHON_ROOT = REPO_ROOT  # responsibility packages live at the repository root

IS_WINDOWS = sys.platform == "win32"

# Experimental (use-at-your-own-risk) connectors are excluded from official builds: the code
# is stripped, not just disabled. Self-builders opt in with DELTA_EXPERIMENTAL=1; the
# loader in integrations/connectors/descriptors.py treats the missing package as a no-op.
INCLUDE_EXPERIMENTAL = os.environ.get("DELTA_EXPERIMENTAL") == "1"

hiddenimports = []
datas = []
binaries = []

for pkg in (
    "core",
    "providers",
    "integrations",
    "packages",
    "services",
    "apps",
    "aisuite",
    "mcp",
    "ddgs",
    "croniter",
    "docstring_parser",
):
    hiddenimports += collect_submodules(pkg)

if not INCLUDE_EXPERIMENTAL:
    hiddenimports = [
        m for m in hiddenimports if not m.startswith("integrations.connectors.experimental")
    ]

# `websockets` powers the managed Slack relay client (relay_client.py). It is
# lazy-imported inside a function, so PyInstaller's static analysis misses it —
# collect it explicitly or the packaged relay adapter fails to open its socket.
# `pypdf`/`pypdfium2` are lazy-imported the same way (pdf_support.py) — and pypdfium2
# carries the libpdfium binary, which collect_all is what actually stages.
for pkg in ("uvicorn", "certifi", "anyio", "websockets", "pypdf", "pypdfium2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Windows has no system tz database; tzdata ships the zoneinfo files the scheduler needs.
if IS_WINDOWS:
    try:
        d, b, h = collect_all("tzdata")
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# [bedrock] extra — boto3 is lazy-imported (bedrock_provider.py) so static analysis
# misses it, and botocore's service-model JSON data dir only ships via collect_all.
for pkg in ("boto3", "botocore"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

for pkg in ("slack_bolt", "telegram"):  # [messaging] extra — optional
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

a = Analysis(
    [os.path.join(PACKAGING, "server_entry.py")],
    pathex=[PYTHON_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL", "PyQt5", "PySide6"]
    + ([] if INCLUDE_EXPERIMENTAL else ["integrations.connectors.experimental"]),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="delta-server",
    # Embed version info so Windows Task Manager shows "Delta Server" (FileDescription)
    # instead of the bare filename; the process groups visually under the Delta family.
    version=os.path.join(PACKAGING, "delta-server-version.txt") if IS_WINDOWS else None,
    # Same Delta logo as the app/launcher — the Python-icon default read as an unrelated
    # process in Task Manager's process tree.
    icon=os.path.join(ROOT, "portable", "launcher", "icon.ico") if IS_WINDOWS else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console on every OS: a windowed build nulls stdout/stderr and hangs uvicorn. The Tauri
    # shell hides the window on Windows via CREATE_NO_WINDOW when spawning the sidecar.
    console=True,
    # target_arch left unset → PyInstaller builds for the host architecture.
)
# Onedir: dist/delta-server/{delta-server[.exe], _internal/}. The build scripts stage
# this whole folder into src-tauri/binaries/sidecar/ for Tauri's `resources` bundling.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="delta-server",
)
