# Delta Brand Manifest

`resources/brand/` is the **single source of truth** for the Delta brand.

## Canonical source

- `delta-logo-512x512.png` — master logo raster. All generated logo derivatives trace back to this file.
- `delta-logo-256x256.png` — 256px convenience export of the master logo.

Brand assets must be edited **here** (or upstream, then synced here), never by hand-editing a generated icon. Check generated icon assets with `scripts/check_brand_icons.py`.

## Generated artifact locations (trace back to the source)

| Location | Purpose |
| --- | --- |
| `apps/desktop/src-tauri/icons/` | Desktop app icons: `icon.png`, `icon.ico`, `icon.icns`, Tauri square/store logos (`32x32.png` … `Square310x310Logo.png`, `StoreLogo.png`), `tray.png` / `tray.rgba` |
| `packaging/portable/launcher/icon.ico` | Portable launcher window icon |

Use `python scripts/check_brand_icons.py --verify` to confirm every generated artifact is present and newer than the brand source.
