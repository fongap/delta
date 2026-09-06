#!/usr/bin/env bash
# scripts/ci_path_selector.sh
#
# Given a list of file paths (one per line on stdin), outputs the CI
# area flags: python, desktop, rust, python_advisories,
# rust_advisories, full.
#
# This script is extracted from .github/workflows/ci.yml so the path
# detection logic can be unit-tested independently.
#
# Usage:
#   echo "core/runtime-native/src/lib.rs" | bash scripts/ci_path_selector.sh
#   printf "core/ledger.py\nservices/server/manager.py\n" | bash scripts/ci_path_selector.sh

set -euo pipefail

python=false
desktop=false
rust=false
python_advisories=false
rust_advisories=false
full=false

while IFS= read -r path; do
  case "$path" in
    # CI workflow changes are validated against the complete suite.
    .github/workflows/*)
      full=true
      ;;

    # Delta Core Rust runtime (R1 shadow-read).
    # MUST precede core/* to avoid being shadowed.
    core/runtime-native/*)
      rust=true
      ;;

    # Tauri Rust workspace.
    # MUST precede apps/desktop/* to avoid being shadowed.
    apps/desktop/src-tauri/*)
      rust=true
      ;;

    # Standalone STT Rust workspace.
    services/stt/*)
      rust=true
      ;;

    # Portable launcher Rust workspace.
    packaging/portable/launcher/*)
      rust=true
      ;;

    # Python runtime and repository-level Python tests.
    core/*|providers/*|integrations/*|packages/*|services/server/*|apps/tui/*|tests/*)
      python=true
      ;;

    # Python dependency graph changes require runtime and advisory checks.
    pyproject.toml|uv.lock)
      python=true
      python_advisories=true
      ;;

    # Release-version gate implementation is covered by Python tests.
    scripts/check_release_versions.py)
      python=true
      ;;

    # Desktop frontend.
    apps/desktop/*)
      desktop=true
      ;;

    # cargo-deny policy only affects Rust advisory validation.
    deny.toml)
      rust_advisories=true
      ;;

    # Other packaging changes are release-critical and receive full CI.
    packaging/*)
      full=true
      ;;
  esac
done

# Full validation overrides selective detection.
if [ "$full" = true ]; then
  python=true
  desktop=true
  rust=true
  python_advisories=true
  rust_advisories=true
fi

echo "python=$python"
echo "desktop=$desktop"
echo "rust=$rust"
echo "python_advisories=$python_advisories"
echo "rust_advisories=$rust_advisories"
echo "full=$full"
