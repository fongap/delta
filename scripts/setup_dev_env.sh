#!/usr/bin/env bash
# One-time dev bootstrap for a fresh checkout: creates the Python venv every
# from-source flow expects at .venv — the browser dev flow runs its
# delta-server directly, and the Tauri desktop shell falls back to it when
# no packaged sidecar binary is present (src-tauri/src/lib.rs, resolution step 3).
#
# Usage: bash scripts/setup_dev_env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"

python3 -m venv "$VENV"
# The delta package (server, engine, connectors) + inbound-messaging extras.
# aisuite comes in as a regular dependency (git-pinned in pyproject.toml until
# the next PyPI release).
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$ROOT[messaging,dev]"

"$VENV/bin/python" -c 'import aisuite, delta' # fail loudly if the wiring broke
echo "Ready: $VENV"
echo "  server: $VENV/bin/delta-server --cwd /path/to/your/project --port 8765"
