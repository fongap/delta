# Delta UI Runtime Contract v1 (UI-005/UI-006/UI-008)

## Status and scope

- Status: implemented for one REST error slice and every current session/app-wide event producer.
- Backend source of truth: `services/server/contracts.py`.
- Frontend maintained types and strict parsers:
  `apps/desktop/src/runtime-contract.ts`.
- The contract is additive for unknown fields, but it is single-version: missing required
  fields and the forbidden envelope fields `error`/`data` must be rejected.
- Provider-specific responses, Agent internals, prompt state, tool implementations, and
  SDK objects are not part of this contract.
- Delta custom Provider identifiers remain opaque strings. The UI contract does not
  reinterpret or remove custom-provider semantics.

This phase does not migrate every REST endpoint. The checked-in GUI and runtime must be
updated together; mixing an old UI with the new runtime is unsupported.
`protocolVersion` and the event-envelope `version` are both exactly `1`; the current UI
rejects any other protocol or event version instead of running a mixed contract.

## REST error envelope

| Field | Required | Default / contract rule |
|---|---:|---|
| `code: string` | yes | Stable machine-readable identifier. |
| `message: string` | yes | Safe user/developer-facing summary. |
| `details: object` | yes | `{}` when no structured detail exists. |
| `retriable: boolean` | yes | `false` unless retry is explicitly safe. |
The first migrated slice is the sidecar-token `401` response. Its HTTP status is unchanged,
but its body contains only the four stable fields. An `error` field is forbidden.
Existing HTTP 2xx business responses such as `{ "ok": false, "error": "..." }` are not
HTTP error envelopes and remain outside this phase; no partial adapter is added for them.

## Session event envelope

The v1 envelope is:

```json
{
  "type": "assistant_message",
  "version": 1,
  "sessionId": "session-id",
  "sequence": 1,
  "payload": {}
}
```

| Field | Required | Rule |
|---|---:|---|
| `type: string` | yes | Stable event discriminator. |
| `version: 1` | yes | Envelope schema version, independent of `protocolVersion`. |
| `sessionId: string \| null` | yes | Owning Delta session; `null` only for an app-wide event that has no associated session. Session-socket events must use a string. |
| `sequence: integer >= 1` | yes | Monotonic for the transport plus `sessionId` value during the current runtime process. Persistence and reorder handling are deferred to UI-017. |
| `payload: object` | yes | Versioned event payload. |

Every current session and app-wide producer emits this one envelope. App-wide automation
events currently carry their run session id; future truly session-independent events use
`null` rather than a second envelope. The GUI accepts only v1;
`{type, data}` frames, missing metadata, unsupported versions, and a `data` field on an
otherwise valid envelope are rejected with a deduplicated diagnostic.

## Core DTO schemas

All five DTOs allow additive fields. The frontend parser returns only known fields so
components cannot accidentally couple to extensions.

### SessionDTO

Required: `session_id`, `workspace`, `agent`, `model`, `mode`.

| Optional field | Default / contract rule |
|---|---|
| `title` | absent |
| `updated_at` | `null` |
| `messages` | `0` |
| `pinned`, `archived` | `false` |
| `reasoning_effort` | `"auto"` |
| `attention` | `0` |
| `liveness` | `"idle"`; only `working`, `sleeping`, `idle` are recognized |
| `subscriptions` | `[]`; non-string entries are ignored |
| `origin`, `origin_label` | absent |

### MessageDTO

Required: `role`.

Optional: `content`, `tool_calls`, `tool_call_id`, `source`, `usage`. Message payloads remain
open by role in v1 because Delta persists historical/provider-normalized message variants.
Provider SDK response objects remain forbidden even though additive canonical fields are allowed.

### ApprovalDTO

Required: `name`.

`arguments` defaults to `{}`; `reason`, `category`, and `standing_target` default to empty
strings. Provider or Agent implementation metadata is not allowed.

### ArtifactDTO

Required: `path`, `name`, `kind`, non-negative `size`, and numeric `modified_at`.
`abs_path` is optional. `path` remains the workspace-relative API/display identifier.

### ModelDTO

Required: opaque model `id` and opaque `provider` identifier. `label` is optional;
`available` defaults to `true`; `custom_provider` defaults to `false`. Vendor response
objects and credential/configuration fields are forbidden. Existing provider/settings
REST shapes will be adapted to this UI DTO in UI-011 rather than changed in this phase.

## Contract gates

- Backend Pydantic schemas reject missing required fields and accept additive fields.
- Backend tests validate current session/message REST responses against the schemas.
- Frontend fixture tests cover all five DTOs with missing, defaulted, and additive fields.
- WebSocket tests verify v1 metadata on direct, foreground, background, and app-wide
  events; contract tests explicitly reject the forbidden `data` field.
- Frontend contract tests verify session/app-wide reconnect, duplicate suppression, and
  delivery of unseen out-of-order events so terminal state is not lost.
- Error contract tests explicitly reject the forbidden `error` field while permitting
  unrelated additive fields.
- CI runs the backend schema checks and frontend contract fixtures as explicit fail-fast
  steps before the complete backend/frontend suites.
- No component, repository, Provider flow, localization path, or Tauri packaging path is
  moved by this phase.
