"""Release version-source consistency check — the validator the release workflow runs.

Extracted verbatim from the inline `prepare` step of .github/workflows/release.yml so
the exact same checks can run locally (and in CI) without dispatching a release:

    python scripts/check_release_versions.py        # prints the version table
    python scripts/check_release_versions.py --quiet

When GITHUB_OUTPUT is set (workflow context) the resolved version/tag are appended to
it, exactly as the inline step did. Exit 1 on any inconsistency. Checks:
  - every version source reads and equals one stable SemVer X.Y.Z:
      pyproject.toml, apps/desktop/package.json, apps/desktop/package-lock.json
      (root + packages[""]), apps/desktop/src-tauri/tauri.conf.json,
      apps/desktop/src-tauri/Cargo.toml + Cargo.lock (delta-desktop),
      packaging/portable/launcher/Cargo.toml + Cargo.lock (delta-portable-launcher),
      packaging/server/delta-server-version.txt (ProductVersion)
  - the version file's filevers/prodvers numeric tuples match the same version
  - CHANGELOG.md keeps an [Unreleased] section and a dated release section for the version
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def json_version(path: str) -> str:
    return json.loads((REPO / path).read_text(encoding="utf-8"))["version"]


def toml_version(path: str) -> str:
    return tomllib.loads((REPO / path).read_text(encoding="utf-8"))["package"]["version"]


def cargo_lock_version(path: str, package: str) -> str:
    packages = tomllib.loads((REPO / path).read_text(encoding="utf-8"))["package"]
    matches = [item["version"] for item in packages if item["name"] == package]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {package!r} package in {path}")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="print only errors")
    args = parser.parse_args()

    package_lock = json.loads(
        (REPO / "apps/desktop/package-lock.json").read_text(encoding="utf-8")
    )
    server_version_text = REPO / "packaging/server/delta-server-version.txt"
    server_version_match = re.search(
        r"StringStruct\('ProductVersion', '([^']+)'\)",
        server_version_text.read_text(encoding="utf-8"),
    )
    if server_version_match is None:
        raise SystemExit("packaging/server/delta-server-version.txt has no ProductVersion")

    versions = {
        "pyproject.toml": tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "apps/desktop/package.json": json_version("apps/desktop/package.json"),
        "apps/desktop/package-lock.json": package_lock["version"],
        "apps/desktop/package-lock.json packages root": package_lock["packages"][""]["version"],
        "apps/desktop/src-tauri/tauri.conf.json": json_version("apps/desktop/src-tauri/tauri.conf.json"),
        "apps/desktop/src-tauri/Cargo.toml": toml_version("apps/desktop/src-tauri/Cargo.toml"),
        "apps/desktop/src-tauri/Cargo.lock": cargo_lock_version("apps/desktop/src-tauri/Cargo.lock", "delta-desktop"),
        "packaging/portable/launcher/Cargo.toml": toml_version("packaging/portable/launcher/Cargo.toml"),
        "packaging/portable/launcher/Cargo.lock": cargo_lock_version("packaging/portable/launcher/Cargo.lock", "delta-portable-launcher"),
        "packaging/server/delta-server-version.txt": server_version_match.group(1),
    }
    unique = set(versions.values())
    if not args.quiet or len(unique) != 1:
        for path, version in versions.items():
            print(f"{path}: {version}")
    if len(unique) != 1:
        details = ", ".join(f"{path}={version}" for path, version in versions.items())
        raise SystemExit(f"release versions are inconsistent: {details}")

    version = unique.pop()
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise SystemExit(f"release version must be stable SemVer X.Y.Z, got {version!r}")
    version_tuple = tuple(int(part) for part in version.split(".")) + (0,)
    tuple_text = ", ".join(str(part) for part in version_tuple)
    for field in ("filevers", "prodvers"):
        if f"{field}=({tuple_text})" not in server_version_text.read_text(encoding="utf-8"):
            raise SystemExit(f"packaging/server/delta-server-version.txt {field} does not match {version}")

    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    if "## [Unreleased]" not in changelog:
        raise SystemExit("CHANGELOG.md must keep an [Unreleased] section")
    if not re.search(rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE):
        raise SystemExit(f"CHANGELOG.md has no release section for {version}")

    if not args.quiet:
        print(f"release version consistent: {version}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"version={version}\n")
            fh.write(f"tag=v{version}\n")


if __name__ == "__main__":
    sys.exit(main())
