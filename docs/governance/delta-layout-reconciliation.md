# Delta Layout Reconciliation

This document closes the historical layout work by recording the semantic
destination of the latest `refactor/repository-normalization` branch. It is a
responsibility split, not a merge of an obsolete directory tree.

| Historical change | Status | Final location | Verification |
| --- | --- | --- | --- |
| `surfaces/gui/` desktop application | Already in main | `apps/desktop/` | Desktop build and Tauri configuration use `apps/desktop`. |
| `coworker/` Python runtime | Reimplemented | `core/`, `providers/`, `integrations/`, `packages/`, `services/server/`, `apps/tui/` | No production imports or source files retain the old package path. |
| Root `stt/` service | Already in main | `services/stt/` | Tauri keeps a relative Cargo dependency on `services/stt`. |
| Provider runtime | Reimplemented | `providers/` | Provider registry and protocol adapters are isolated from UI code. |
| Connectors, MCP, skills, tools, and web access | Reimplemented | `integrations/` | Core and HTTP service imports target the integration boundary. |
| Shared configuration and private persistence | Reimplemented | `packages/` | Runtime modules import `packages.config`, `packages.secrets`, and related foundations. |
| Desktop localization | Reimplemented | `packages/i18n/` | Desktop TypeScript resolves the `@delta/i18n/*` alias. |
| API sidecar | Reimplemented | `services/server/` | `delta-server` now targets `services.server.run:main`. |
| Terminal application | Reimplemented | `apps/tui/` | Its imports target core and provider boundaries. |
| Legacy compatibility directories | Obsolete | Removed | CI rejects top-level `surfaces`, `coworker`, `src`, `stt`, `assets`, and `crates`. |

The historical `delta-layout-v2` merge provided the earlier desktop, STT, asset,
and package-name migrations. The latest branch's functional extraction of
connector and engine helpers remains in the responsibility boundaries above;
no additional merge of a legacy layout branch is required.
