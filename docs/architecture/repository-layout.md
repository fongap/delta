# Repository Layout

This is the canonical layout for the Delta monorepo. `scripts/layout-check` (or
the CI `layout-check` job) guards the structure below. There is no "layout v3" —
this is the only layout.

## Top-level directories

| Directory | Responsibility |
| --- | --- |
| `apps/` | Frontend apps (currently `apps/desktop`, the Tauri GUI). |
| `services/` | Standalone backend services (currently `services/stt`). |
| `src/delta/` | The Python runtime package (the engine + CLI + providers + tools). |
| `tests/` | Python/runtime test suite, fakes, and test-only support. |
| `resources/` | Canonical non-code assets. `resources/brand/` is the single source of truth for the brand. |
| `packaging/` | Packaging and distribution (portable launcher, server packaging). |
| `scripts/` | Developer/CI helper scripts (e.g. `generate_icons.py`, `setup_dev_env.sh`). |
| `docs/` | Documentation (`architecture/`, config examples, etc.). |
| `.github/` | CI/CD and repository automation (workflows, Dependabot). |

## Desktop (`apps/desktop`)

- **Feature-based** source: code is organized under `src/features/<feature>/`,
  with shared, cross-cutting code under `src/shared/`.
- Tests are **colocated** with the code they cover as `*.test.*` files
  (e.g. `attach.test.ts` next to `attach.ts`).

## Python runtime (`src/delta/`)

- `src/delta/` is the runtime package. **Test-only support lives in `tests/`,
  never inside the package.** Do not add `tests/`, `fakes/`, or fixtures into
  `src/delta/`.

## Tests

| Layer | Location |
| --- | --- |
| Python / runtime tests | `tests/` |
| Frontend unit tests | `apps/desktop/src/**/*.test.*` (colocated) |
| Frontend e2e | `apps/desktop/tests/` |

## Resources

- `resources/brand/` is the **single source of truth** for the Delta brand
  (`delta-logo-512x512.png`).
- Generated icons trace back to it and live in
  `apps/desktop/src-tauri/icons/` and `packaging/portable/launcher/icon.ico`.
- Regenerate/verify them with `scripts/generate_icons.py` (see
  `resources/brand/MANIFEST.md`).

## Forbidden top-level directories

These top-level directories must **never reappear**:

- `surfaces/`
- `crates/`
- `coworker/`
- `assets/`

  > Note: `apps/desktop/assets/` is a legitimate runtime asset directory — not
  > the forbidden top-level `assets/`.

Also forbidden as throwaway "don't know where this goes" directories (top-level
or otherwise): `common/`, `core/`, `misc/`, `helpers/`, `utils/`, `shared/`,
`base/`, `legacy/`. New code must be placed into an explicit home per the rules
above; if there is no good home, the layout needs a documented addition, not a
new catch-all directory.
