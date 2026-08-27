# ADR: Stable Delta UI Runtime Boundary (UI-003)

- Status: Accepted
- Date: 2026-08-23
- Scope: the core session UI in `apps/desktop`

## Context

Delta currently centralizes REST and WebSocket transport in `apps/desktop/src/api.ts`,
but transport types and UI types are still interleaved. Components import API response
types directly and event payloads are not yet discriminated by type. This makes it easy
for backend Provider or Agent implementation details to become accidental UI dependencies.

Delta must remain a minimal, locally authoritative derivative of OpenWorker. The goal
is therefore not to copy an upstream architecture, rewrite the GUI, or make the UI
insensitive to every backend change. The goal is a small, stable semantic boundary that
allows intentional contract decisions.

## Decision

The stable **core UI Runtime Contract** is limited to six semantic domains:

| Domain | UI may depend on | UI must not infer |
|---|---|---|
| `session` | identity, title, workspace reference, public agent identity, selected model, user-selectable mode, lifecycle/attention state | engine instances, executor state, persistence records, scheduler state, permission-engine objects |
| `message` | normalized user/assistant/tool/notice content, attachments, source metadata, timestamps, reasoning text, normalized usage | provider-native message blocks, canonical replay history, raw tool-call JSON, provider streaming chunks |
| `approval` | prompt identity, displayable tool/target summary, category, allowed decisions, resolved state | risk evaluator internals, registry metadata, raw permission rules, authorization storage layout |
| `artifact` | stable path/name/kind metadata plus read/reveal capabilities and content result | executor filesystem objects, scratch implementation, platform shell commands |
| `model` | stable model identity, display label, selection state, normalized limits/features | Provider SDK objects, vendor response shapes, protocol-specific request parameters |
| `capability` | named supported features, protocol-version facts, and optional normalized parameters | presence of backend fields as an implicit capability or Provider/Agent class inspection |

The transport direction is one-way:

```text
REST / WebSocket wire payload
          ↓
runtime adapter and event normalizer
          ↓
session/message/approval/artifact/model/capability DTOs
          ↓
React components and UI state
```

Core components may send user intent back through the same boundary, but they must not
construct endpoint paths, authentication headers, raw WebSocket frames, or Provider
request objects themselves.

## Provider and Agent boundary

The following are forbidden dependencies for core UI components and core UI state:

### Provider internals

- Provider SDK clients, SDK exceptions, and raw `/models` or completion responses.
- Protocol implementation names used as behavior switches inside session/transcript UI.
- Credential storage fields, API keys, base URLs, custom headers, and OAuth token shapes.
- Vendor request fields such as native thinking/reasoning blocks, token parameter names,
  cache controls, tool-call encodings, and Provider-specific error bodies.
- Backend routing details such as Provider class names, registry entries, recommended-model
  heuristics, or fallback order.

The Delta custom Provider setup screen is a deliberate **management extension**, not part
of the six-domain core runtime contract. It may consume a Delta-owned Provider setup DTO
through the runtime adapter, including protocol and credential-form metadata required to
preserve current custom Provider behavior. Those fields must not leak into session,
message, approval, artifact, model, or capability DTOs, and core components must not
branch on them.

### Agent internals

- Engine, executor, registry, provider, audit-context, compaction-state, scheduler, or
  persistence objects and their serialized forms.
- Canonical provider history, raw assistant `tool_calls`, tool result storage records,
  and internal message roles used only for replay.
- Permission evaluation rules, risk overrides, standing-grant storage, or tool metadata
  beyond the normalized approval DTO.
- Python class/module names, exception classes, coroutine state, and internal liveness
  bookkeeping.

A stable public agent identity or display label may remain in `session`; this does not
authorize exposing the Agent implementation object or using its internal fields.

## DTO and contract rules

1. DTOs express UI semantics, not a renamed copy of backend objects.
2. Required, optional, and defaulted fields are defined at the adapter boundary; UI
   components do not invent defaults for transport fields.
3. Unknown response fields are ignored. Unknown capabilities and events remain
   diagnosable and must not crash the UI.
4. Missing optional fields use explicit adapter defaults. Missing required fields produce
   a normalized runtime error instead of leaking an arbitrary backend exception.
5. Model and Provider changes must not alter the message/session DTO shape.
6. Wire naming may remain backend-defined; components consume normalized UI naming.
7. Contract behavior is validated by fixtures and backend schema tests before a
   transport field becomes a component dependency.

## Current exceptions and migration rule

The present code only partially enforces this decision. The v1 contract models now live
in `src/delta/server/contracts.py` and `apps/desktop/src/runtime-contract.ts`, but:

- components import functions and DTOs directly from `api.ts`;
- `WsEvent.payload` is untyped;
- most management DTOs still live beside transport code;
- components still depend directly on transport helpers rather than a public runtime client.

These are recorded migration inputs, not permission for a broad refactor. Enforcement
must be incremental: define DTOs, migrate one vertical slice through an adapter, normalize
events, then establish one public runtime export boundary. Each step must preserve Delta
branding, localization, custom Provider semantics, Tauri sidecar defaults, packaging, and
current user-visible behavior.

## Consequences

- Backend Provider/Agent internals may change without automatically becoming UI contracts.
- Intentional UI-affecting backend changes still require an explicit DTO or capability
  change; the UI is not promised absolute insulation from runtime evolution.
- Temporary type translation at the adapter boundary is acceptable for the current
  canonical DTO shape, but duplicate business logic is not.
- The repository remains intact. This ADR does not authorize moving large component sets,
  splitting packages/repositories, redesigning the UI, or importing upstream team,
  subscription, or collaboration features.

## Follow-up gates

This decision becomes enforceable only through separately authorized tasks:

1. Define the core DTO rules and fixtures.
2. Introduce the runtime client/adapter and migrate one domain at a time.
3. Normalize session and app-wide events before component consumption.
4. Add a single public runtime export and an automated boundary-import check.

Physical package or repository separation may be considered only after the contract is
versioned, prohibited imports are zero, and frontend-only smoke tests are stable.
