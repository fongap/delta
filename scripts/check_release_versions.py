"""Release version-source consistency check — the validator the release workflow runs.

Extracted verbatim from the inline `prepare` step of .github/workflows/release.yml so
the exact same checks can run locally (and in CI) without dispatching a release:

    python scripts/check_release_versions.py                    # prints the version table
    python scripts/check_release_versions.py --quiet            # dev/CI mode
    python scripts/check_release_versions.py --quiet --release  # strict release gate

Dev mode (default, what PR / main CI runs):
  - accepts a stable version (X.Y.Z)
  - accepts a dev/prerelease version (X.Y.Z.devN | X.Y.Z-dev.N)
  - normalizes Python PEP 440 and SemVer pre-release identifiers as equivalent
  - checks every version source reads the same logical version
  - does NOT require a dated CHANGELOG release section for a pre-release line

Release mode (--release, what the release workflow runs):
  - only accepts a stable X.Y.Z version
  - rejects any pre-release (dev/alpha/rc/...)
  - requires filevers/prodvers numeric tuples to match X.Y.Z
  - requires a dated CHANGELOG release section:  ## [X.Y.Z] - YYYY-MM-DD
  - appends version/tag to GITHUB_OUTPUT when set

When GITHUB_OUTPUT is set (workflow context) the resolved version/tag are appended to
it, exactly as the inline step did. Exit 1 on any inconsistency.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_STABLE_RE = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")


@dataclass(frozen=True)
class VersionIdentity:
    """Canonical logical version, independent of ecosystem syntax.

    A stable release has ``prerelease == ""`` and ``prerelease_number == 0``.
    A pre-release line has a non-empty ``prerelease`` label and a numeric
    ``prerelease_number``.
    """

    major: int
    minor: int
    patch: int
    prerelease: str = ""
    prerelease_number: int = 0

    @property
    def is_prerelease(self) -> bool:
        return self.prerelease != ""

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if not self.prerelease:
            return base
        return f"{base}-{self.prerelease}.{self.prerelease_number}"


def parse_version_identity(version: str) -> VersionIdentity:
    """Parse a version string into a canonical :class:`VersionIdentity`.

    Accepts stable SemVer / PEP 440 (``X.Y.Z``) and pre-release forms:

    - PEP 440: ``0.4.0.dev0``, ``0.4.0.alpha1``, ``0.4.0.rc1``
    - SemVer:  ``0.4.0-dev.0``, ``0.4.0-alpha.1``, ``0.4.0-rc.1``

    The pre-release **label** is extracted case-insensitively; the label
    comparison is case-insensitive so ``dev`` / ``DEV`` are equivalent.
    """
    raw = version.strip()

    # Pre-release split: PEP 440 uses '.', SemVer uses '-'.
    # Normalize the separator: find the first '.' or '-' that begins a
    # pre-release identifier.
    core = raw
    pre = ""

    # Try to locate a pre-release part.
    m = re.search(r"[.-](dev|alpha|beta|rc)(\.?\d*)$", raw, re.IGNORECASE)
    if m:
        label = m.group(1).lower()
        num_text = m.group(2).lstrip(".")
        pre_num = int(num_text) if num_text else 0
        # The core is everything before the separator that introduced the
        # pre-release. Find the actual character.
        sep_pos = m.start()
        core = raw[:sep_pos]
        pre = f"{label}.{pre_num}"

    if not _STABLE_RE.fullmatch(core):
        raise ValueError(f"not a supported version shape: {version!r}")

    major, minor, patch = (int(part) for part in core.split("."))

    if not pre:
        return VersionIdentity(major, minor, patch)

    label, num_text = pre.split(".")
    return VersionIdentity(major, minor, patch, label, int(num_text))


def normalize_version(version: str) -> VersionIdentity:
    """Parse and canonicalize a version string (alias of :func:`parse_version_identity`)."""
    return parse_version_identity(version)


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
    parser.add_argument(
        "--release",
        action="store_true",
        help="strict release gate: only X.Y.Z, requires a dated CHANGELOG section",
    )
    args = parser.parse_args()

    package_lock = json.loads(
        (REPO / "apps/desktop/package-lock.json").read_text(encoding="utf-8")
    )
    server_version_text = REPO / "packaging/server/delta-server-version.txt"
    server_version_content = server_version_text.read_text(encoding="utf-8")
    server_version_match = re.search(
        r"StringStruct\('ProductVersion', '([^']+)'\)",
        server_version_content,
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

    if not args.quiet:
        for path, version in versions.items():
            print(f"{path}: {version}")

    # Normalize all version sources to canonical identity and compare.
    try:
        identities = {path: parse_version_identity(v) for path, v in versions.items()}
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    reference = next(iter(identities.values()))
    for path, ident in identities.items():
        if ident != reference:
            details = ", ".join(f"{p}={v}" for p, v in versions.items())
            raise SystemExit(f"release versions are inconsistent: {details}")

    version_identity = reference
    version_str = str(version_identity)

    # In release mode, the version must be stable (no pre-release).
    if args.release and version_identity.is_prerelease:
        raise SystemExit(
            f"release gate requires a stable X.Y.Z version, got {versions['pyproject.toml']!r}"
        )

    # The server-version file's filevers/prodvers numeric tuples must match.
    version_tuple = (
        version_identity.major,
        version_identity.minor,
        version_identity.patch,
        0,
    )
    tuple_text = ", ".join(str(part) for part in version_tuple)
    for field in ("filevers", "prodvers"):
        if f"{field}=({tuple_text})" not in server_version_content:
            raise SystemExit(
                f"packaging/server/delta-server-version.txt {field} does not match {version_str}"
            )

    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    if "## [Unreleased]" not in changelog:
        raise SystemExit("CHANGELOG.md must keep an [Unreleased] section")

    # In release mode, require a dated CHANGELOG release section for the stable version.
    if args.release:
        if not re.search(
            rf"^## \[{re.escape(version_str)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
            changelog,
            re.MULTILINE,
        ):
            raise SystemExit(f"CHANGELOG.md has no release section for {version_str}")

    if not args.quiet:
        print(f"release version consistent: {version_str}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"version={version_str}\n")
            fh.write(f"tag=v{version_str}\n")


if __name__ == "__main__":
    sys.exit(main())