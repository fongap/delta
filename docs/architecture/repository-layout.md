# Repository Layout

Delta is organized by system responsibility. Directory names describe current
ownership, never an upstream source or a compatibility era.

## Top-level directories

| Directory | Responsibility |
| --- | --- |
| `apps/` | Runnable user-facing applications: the Tauri desktop app and terminal UI. |
| `core/` | Agent runtime, workflow execution, state, memory, permissions, and personas. |
| `providers/` | Model-provider protocols, discovery, capability metadata, and routing. |
| `integrations/` | Connectors, MCP, skills, tools, web access, and managed cloud integration. |
| `services/` | Independently runnable services: the HTTP API sidecar and local STT service. |
| `packages/` | Shared configuration, private persistence, sanitization, and cross-app foundations. |
| `resources/` | Non-code assets; `resources/brand/` is the Delta brand source of truth. |
| `packaging/` | Portable, desktop-sidecar, and release packaging. |
| `tests/` | Python/runtime tests and test-only fakes. |
| `docs/` | Architecture, governance, and development documentation. |
| `scripts/` | Repository maintenance and validation helpers. |

## Boundary rules

- `apps/desktop/` owns React UI, Tauri, and desktop platform integration. Its
  shared localization dependency lives in `packages/i18n/`.
- `core/` may depend on `providers/`, `integrations/`, and `packages/`, but UI
  code must not contain core runtime logic.
- `providers/` owns vendor SDK adaptation only; provider configuration UI stays
  in `apps/desktop/`.
- `integrations/` owns external capabilities. Connector presentation stays in
  the desktop application feature module.
- `services/server/` is the API boundary used by applications; `services/stt/`
  remains a standalone local speech-to-text service.
- Test-only helpers stay under `tests/`; production packages never contain
  fakes or test fixtures.

## Tests

| Layer | Location |
| --- | --- |
| Python / runtime tests | `tests/` |
| Frontend unit tests | `apps/desktop/src/**/*.test.*` and `packages/i18n/**/*.test.*` |
| Frontend e2e | `apps/desktop/tests/` |

## Resources

- `resources/brand/` is the source of truth for the Delta brand.
- Generated icons live in `apps/desktop/src-tauri/icons/` and
  `packaging/portable/launcher/icon.ico`.
- Regenerate or verify icons with `scripts/generate_icons.py`.

## Forbidden legacy roots

The following top-level directories must not reappear: `surfaces/`, `coworker/`,
`src/`, `stt/`, `assets/`, and `crates/`. Do not add blanket catch-all roots such
as `common/`, `misc/`, `helpers/`, `utils/`, `shared/`, `base/`, or `legacy/`.
Each new module must join an explicit responsibility boundary above.
