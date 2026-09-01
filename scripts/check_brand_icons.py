#!/usr/bin/env python3
"""Check Delta's generated icon assets against the brand source.

The single source of truth for the Delta brand is:

    resources/brand/delta-logo-512x512.png

From that one raster, a set of "generated" icon artifacts are produced for the
desktop app and the portable launcher. This script is the provenance record and
guard for that relationship:

  * default run   - prints a summary of brand provenance and checks that each
                    expected generated artifact exists on disk.
  * --verify      - like the default run, but exits non-zero if any artifact is
                    missing or *stale* (older than the brand source). This is
                    the mode CI would call as a guard.
  * --regenerate  - prints the exact commands/toolchain that produce each
                    generated artifact from the source, so regeneration is
                    reproducible and documented rather than a manual guess.

Design constraints:

  * Python 3 stdlib only (no Pillow, no third-party deps), so it runs on any
    Windows / macOS / Linux box that has Python.
  * No absolute paths: every path is derived relative to the repository root,
    which is itself derived from this script's location.
  * Deterministic and repeatable: same repo state -> same result.

Honesty note on regeneration:
  Faithful raster resampling (512px -> 32px, ICO/ICNS packaging, rgba tray)
  requires an image library or platform tool. stdlib cannot do this without
  lossy tricks, so this script does *not* pretend to regenerate those files in
  Python. Instead it documents the intended toolchain. The one thing stdlib CAN
  do reliably is verify provenance (existence + freshness), which is the guard
  that matters for CI.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

# Repository root is two levels above scripts/: <root>/scripts/check_brand_icons.py
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The single source of truth. Everything below traces back to this file.
BRAND_SOURCE = os.path.join(REPO_ROOT, "resources", "brand", "delta-logo-512x512.png")
BRAND_DIR = os.path.join(REPO_ROOT, "resources", "brand")

# Staleness tolerance (seconds). The generated icons are checked into the repo and the brand
# source is frequently touched alongside them in the same workflow, so its mtime can land a
# fraction of a second AFTER the icons even when the icon bytes already match the source (git
# confirms the source content is unchanged). A small window absorbs that touch artifact while
# still flagging icons that are genuinely minutes/hours out of date.
STALE_TOLERANCE_S = 5.0


@dataclass
class Artifact:
    """One expected generated icon and how it is produced from the source."""

    # Path relative to REPO_ROOT.
    relpath: str
    # Human-readable toolchain that regenerates this file from the source.
    toolchain: str

    @property
    def abspath(self) -> str:
        return os.path.join(REPO_ROOT, self.relpath)


# Manifest: every generated output the brand source is responsible for, and the
# documented toolchain that (re)produces it. Add a row here when a new icon is
# introduced so it stays traceable to the source.
MANIFEST: list[Artifact] = [
    # --- Desktop app icons (Tauri): apps/desktop/src-tauri/icons/ -----------
    Artifact("apps/desktop/src-tauri/icons/icon.png",
             "png resize: `magick resources/brand/delta-logo-512x512.png -resize 512x512 "
             "apps/desktop/src-tauri/icons/icon.png` (or ImageMagick equivalent)"),
    Artifact("apps/desktop/src-tauri/icons/icon.ico",
             "ico: `cargo tauri icon resources/brand/delta-logo-512x512.png -o "
             "apps/desktop/src-tauri/icons` (generates the .ico/.icns/png set)"),
    Artifact("apps/desktop/src-tauri/icons/icon.icns",
             "icns: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/32x32.png",
             "tauri icon png set: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/128x128.png",
             "tauri icon png set: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/128x128@2x.png",
             "tauri icon png set: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/64x64.png",
             "tauri icon png set: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/Square30x30Logo.png",
             "tauri windows store logos: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/Square44x44Logo.png",
             "tauri windows store logos: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/Square71x71Logo.png",
             "tauri windows store logos: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/Square89x89Logo.png",
             "tauri windows store logos: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/Square107x107Logo.png",
             "tauri windows store logos: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/Square142x142Logo.png",
             "tauri windows store logos: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/Square150x150Logo.png",
             "tauri windows store logos: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/Square284x284Logo.png",
             "tauri windows store logos: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/Square310x310Logo.png",
             "tauri windows store logos: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/StoreLogo.png",
             "tauri windows store logos: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/tray.png",
             "tray png: `cargo tauri icon` (see icon.ico row)"),
    Artifact("apps/desktop/src-tauri/icons/tray.rgba",
             "tray rgba: `cargo tauri icon` (see icon.ico row)"),
    # --- Portable launcher ---------------------------------------------------
    Artifact("packaging/portable/launcher/icon.ico",
             "launcher ico: `magick resources/brand/delta-logo-512x512.png -resize 256x256 "
             "-define icon:auto-resize=16,24,32,48,64,128,256 packaging/portable/launcher/icon.ico`"),
]


def require_brand_source() -> None:
    """Fail fast with a clear message if the single source of truth is missing."""
    if not os.path.isfile(BRAND_SOURCE):
        print(
            "error: brand source of truth not found:\n"
            f"  {BRAND_SOURCE}\n"
            "Nothing can be regenerated or verified without it. "
            "Restore resources/brand/delta-logo-512x512.png before running this script.",
            file=sys.stderr,
        )
        sys.exit(2)


def check_artifact(artifact: Artifact, verify_stale: bool) -> tuple[bool, str]:
    """Return (ok, detail) for one artifact. Never raises."""
    path = artifact.abspath
    if not os.path.isfile(path):
        return False, "MISSING"

    if verify_stale:
        # "Stale" means older than the brand source by more than the tolerance window: the
        # icon is genuinely out of date and must be regenerated to match the canonical logo.
        source_mtime = os.path.getmtime(BRAND_SOURCE)
        if os.path.getmtime(path) < source_mtime - STALE_TOLERANCE_S:
            return False, "STALE"

    return True, "ok"


def run_checks(verify_stale: bool) -> bool:
    """Run provenance checks over the whole manifest. Returns True if all pass."""
    require_brand_source()
    source_mtime = os.path.getmtime(BRAND_SOURCE)
    print(f"Brand source of truth : {os.path.relpath(BRAND_SOURCE, REPO_ROOT)}")
    print(f"Brand directory      : {os.path.relpath(BRAND_DIR, REPO_ROOT)}")
    print(f"Source mtime         : {source_mtime}")
    print(f"Mode                 : {'--verify (stale check on)' if verify_stale else 'existence check'}")
    print()

    all_ok = True
    missing = 0
    stale = 0
    for artifact in MANIFEST:
        ok, status = check_artifact(artifact, verify_stale)
        marker = "ok   " if ok else "FAIL "
        if status == "MISSING":
            missing += 1
        elif status == "STALE":
            stale += 1
        if not ok:
            all_ok = False
        print(f"  [{marker}] {artifact.relpath}  ({status})")

    print()
    total = len(MANIFEST)
    print(f"Checked {total} generated artifacts: "
          f"{total - missing - stale} ok, {missing} missing, {stale} stale.")
    if not all_ok:
        print("Run `python scripts/check_brand_icons.py --regenerate` for the documented "
              "toolchain commands to rebuild the failing artifacts from the source.")
    return all_ok


def print_regenerate() -> None:
    """Print the documented regeneration commands for every artifact."""
    require_brand_source()
    print("Documented regeneration commands (source of truth: "
          f"{os.path.relpath(BRAND_SOURCE, REPO_ROOT)}):\n")
    for artifact in MANIFEST:
        print(f"  -> {artifact.relpath}")
        print(f"     {artifact.toolchain}\n")
    print("Regeneration is intentionally NOT performed in Python: faithful raster "
          "resampling (512px -> smaller sizes), ICO/ICNS packaging, and rgba tray "
          "output require an image toolchain (tauri CLI / ImageMagick). Run the "
          "commands above with the tool installed, then re-run --verify.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and document Delta's generated icon assets against the brand "
            "source of truth (resources/brand/delta-logo-512x512.png)."
        ),
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Exit non-zero if any generated artifact is missing or stale "
             "(older than the brand source). CI-safe guard.",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Print the documented toolchain commands that regenerate every "
             "generated artifact from the brand source.",
    )
    args = parser.parse_args(argv)

    if args.regenerate:
        print_regenerate()
        return 0

    ok = run_checks(verify_stale=args.verify)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
