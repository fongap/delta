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
    sys.modules[name] = module
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
    # Fixtures are assembled at RUNTIME: the gate scans every tracked file including
    # this one, so a literal legacy path here would make the gate flag itself (CI-hit
    # 2026-08-31). The segments below never appear joined in this file's source.
    stale_tail = "delta-server" + "-version.txt"
    shapes = [
        "packaging/" + stale_tail,
        "packaging\\" + stale_tail,
        "packaging/" + "delta-server" + ".spec",
        "packaging/" + "server" + "_entry.py",
        "packaging/" + "build" + "_portable.ps1",
        "packaging\\" + "build" + "_portable.ps1",
        "packaging/" + "scan" + "_portable_paths.ps1",
    ]
    for stale in shapes:
        violations = gate.violations_for(f"run {stale} now", "fake/file.yml")
        assert len(violations) == 1, (stale, violations)


def test_gate_ignores_current_paths_and_history():
    gate = _load("check_legacy_paths")
    canonical = (
        "packaging/server/delta-server-version.txt"
        + " + packaging/portable/build_portable.ps1"
    )
    assert gate.violations_for(canonical, "fake/file.yml") == []
    # History files are exempt at the FILE level in find_violations, not by weakening
    # the patterns: the pattern still matches history content (built at runtime — the
    # gate scans this file too), the exemption is what spares CHANGELOG/UPSTREAM/
    # docs-governance.
    stale = "packaging/" + "delta-server" + "-version.txt"
    assert gate.violations_for(stale, "CHANGELOG.md")
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


def _version_module():
    return _load("check_release_versions")


# --- Version identity normalization tests (Cases 1-4) ---

def test_case1_stable_version_equivalent():
    """Case 1: stable version is equivalent across ecosystems.

    Python: 0.4.0, SemVer: 0.4.0 → dev PASS, release PASS.
    """
    m = _version_module()
    a = m.parse_version_identity("0.4.0")
    b = m.parse_version_identity("0.4.0")
    assert a == b
    assert not a.is_prerelease
    assert str(a) == "0.4.0"


def test_case2_dev_version_cross_ecosystem_equivalent():
    """Case 2: PEP 440 dev and SemVer dev are logically equivalent.

    Python: 0.4.0.dev0, SemVer: 0.4.0-dev.0 → dev PASS, release FAIL.
    """
    m = _version_module()
    pep440 = m.parse_version_identity("0.4.0.dev0")
    semver = m.parse_version_identity("0.4.0-dev.0")
    assert pep440 == semver
    assert pep440.is_prerelease
    assert semver.is_prerelease
    assert pep440.prerelease == "dev"
    assert pep440.prerelease_number == 0


def test_case3_real_version_mismatch_fails():
    """Case 3: different major/minor/patch must not be equal.

    Python: 0.4.0.dev0, Desktop: 0.4.1-dev.0 → FAIL.
    """
    m = _version_module()
    a = m.parse_version_identity("0.4.0.dev0")
    b = m.parse_version_identity("0.4.1-dev.0")
    assert a != b


def test_case4_different_prerelease_numbers_fails():
    """Case 4: same base but different prerelease number must not be equal.

    0.4.0.dev0 vs 0.4.0-dev.1 → FAIL.
    """
    m = _version_module()
    a = m.parse_version_identity("0.4.0.dev0")
    b = m.parse_version_identity("0.4.0-dev.1")
    assert a != b
    assert a.prerelease_number == 0
    assert b.prerelease_number == 1


def test_case5_release_mode_rejects_prerelease():
    """Case 5: release mode must reject any prerelease version.

    The current repo version is 0.4.0.dev0 / 0.4.0-dev.0 (dev line).
    Running --release must fail because the version is not stable X.Y.Z.
    """
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_release_versions.py"), "--quiet", "--release"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "stable" in (result.stdout + result.stderr).lower()


def test_release_mode_output_includes_version_and_tag(monkeypatch, tmp_path):
    """When GITHUB_OUTPUT is set, the script appends version= and tag=."""
    import os
    gh_out = tmp_path / "gh_out"
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_release_versions.py"), "--quiet"],
        capture_output=True,
        text=True,
        env={**os.environ, "GITHUB_OUTPUT": str(gh_out)},
    )
    assert result.returncode == 0
    content = gh_out.read_text()
    assert "version=" in content
    assert "tag=" in content


def test_portable_zip_structure_if_present():
    """If a portable ZIP has been built locally (releases/Delta-Windows-Portable.zip),
    verify its structure: exactly one top-level Delta/ directory and the required
    portable files. This is a lightweight local check that complements the full
    E2E build + smoke verification in .github/workflows/release.yml (build-portable job).

    Skips if no ZIP exists locally (the full E2E verification only runs in CI)."""
    import zipfile
    import pytest

    zip_path = REPO / "releases" / "Delta-Windows-Portable.zip"
    if not zip_path.exists():
        pytest.skip(
            f"No local portable ZIP at {zip_path}. "
            "Full E2E portable build + smoke verification runs in "
            ".github/workflows/release.yml (build-portable job)."
        )
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        # Must contain exactly one top-level directory.
        tops = {n.split("/")[0] for n in names if n}
        assert len(tops) == 1, f"expected one top-level dir, got {tops}"
        assert "Delta" in tops, f"top-level dir must be Delta/, got {tops}"
        # Required files per packaging/portable/build_portable.ps1.
        required = [
            "Delta/",
            "Delta/Delta.exe",
            "Delta/App/",
            "Delta/Data/",
        ]
        for req in required:
            assert any(n.startswith(req) for n in names), f"missing required: {req}"
