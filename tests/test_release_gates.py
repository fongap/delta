"""Release-gate scripts (layout restructure closeout): the legacy-path gate and the
version-consistency validator must both pass, and the path gate must actually catch
the shapes that escaped into release.yml (owner-hit 2026-08-31)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repo_has_no_deprecated_paths():
    """Integration: the whole tracked tree is clean (the CI layout-check job runs the
    same script; this keeps pytest coverage on it too)."""
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_legacy_paths.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_gate_catches_every_legacy_shape():
    gate = _load("check_legacy_paths")
    for stale in (
        "packaging/delta-server-version.txt",
        "packaging\\delta-server-version.txt",
        "packaging/delta-server.spec",
        "packaging/server_entry.py",
        "packaging/build_portable.ps1",
        "packaging\\build_portable.ps1",
        "packaging/scan_portable_paths.ps1",
    ):
        violations = gate.violations_for(f"run {stale} now", "fake/file.yml")
        assert len(violations) == 1, (stale, violations)


def test_gate_ignores_current_paths_and_history():
    gate = _load("check_legacy_paths")
    assert gate.violations_for(
        "packaging/server/delta-server-version.txt + packaging/portable/build_portable.ps1",
        "fake/file.yml",
    ) == []
    # History files are exempt at the FILE level in find_violations, not by weakening
    # the patterns: the pattern still matches history content, the exemption is what
    # spares CHANGELOG/UPSTREAM/docs-governance.
    assert gate.violations_for("packaging/delta-server-version.txt", "CHANGELOG.md")
    assert gate._is_exempt(REPO / "CHANGELOG.md")
    assert not gate._is_exempt(REPO / ".github" / "workflows" / "release.yml")


def test_version_sources_are_consistent():
    """The release workflow's prepare gate, run locally: all ten version sources read
    and agree, the version file's filevers/prodvers match, and CHANGELOG carries a
    dated release section."""
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_release_versions.py"), "--quiet"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
