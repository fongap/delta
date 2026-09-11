# ADR-048: R5 Final Convergence

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-11 |
| **Related** | ADR-047 (R5 Plan), ADR-009 (Delta Core Architecture), ADR-003 (Provider Protocol Model), ADR-046 (R4 Final Convergence) |

## Context

R5 (Provider Core) migrates the provider (model access) domain from Python
SDKs to Rust `delta_core`. This ADR records the final authority matrix,
protocol version history, and completion status.

R5 was planned in ADR-047 and executed in 5 phases across 14 PRs
(#183–#196).

## Phases Completed

| Phase | ADR/PR | What moved to Rust |
|---|---|---|
| 0: Streaming ABI | #184 | delta_core stdio protocol extended with `start`/`delta`/`done` streaming frames. Protocol 14→15. |
| 1a: OpenAI Chat transport | #185 | `provider.complete`/`provider.stream` (openai_chat): HTTP, SSE, tool-call parse, usage, reasoning. Added `ureq` dep. |
| 1b: Anthropic Messages transport | #186 | `provider.complete`/`provider.stream` (anthropic): x-api-key, thinking, cache_control, content_block SSE. |
| 1c: OpenAI Responses transport | #188 | `provider.complete`/`provider.stream` (openai_responses): /v1/responses, output items, event-based SSE. |
| 1d: openai_chat complete() delegation | #189 | `OpenAIProvider.complete()` delegates to Rust when `core` is set. `http_error()` carries response body for param-fix retry. |
| 1e: openai_chat stream() delegation | #190 | `OpenAIProvider.stream()` delegates to Rust. TTFT/health/truncation/salvage stay Python. |
| 1f: all providers + production wiring | #191 | `AnthropicProvider` + `OpenAIResponsesProvider` delegate. `registry`/`router` accept `core`. `agent.py`/`manager.py` pass `core=maybe_core_client()`. |
| 2a: capabilities | #192 | `provider.capabilities` command: matrix (30 entries) + heuristics. |
| 2b: endpoint_caps | #193 | `endpoint.caps` (read) + `endpoint.reject` (write). Rust owns `endpoint_caps.json`. |
| 2c: health | #194 | `health.record` + `health.profile` + `health.all`. Rust owns `provider_health.json`. |
| 2d: routing | #195 | `provider.routes` command: prefix-split + membership + bare-strip. |
| 3: friendly_model_error | #196 | `provider.friendly_error`: access/quota marker matching. Tool-call salvage regex deferred (pure parsing, no authority value). |

## Authority Matrix After R5

| Domain | Rust Authority | Python Capability |
|---|---|---|
| OpenAI Chat Completions transport | `provider.complete`/`stream` (openai_chat) | profile/credential build |
| Anthropic Messages transport | `provider.complete`/`stream` (anthropic) | profile/credential build |
| OpenAI Responses transport | `provider.complete`/`stream` (openai_responses) | profile/credential build |
| Streaming / deltas | Rust (stream protocol, cancel placeholder) | engine delta→UI event mapping |
| Tool-call parse | Rust (complete + stream) | — |
| Usage / reasoning extraction | Rust | — |
| Retry policy | Rust `retry.classify` (ADR-039) | backoff math, engine loop |
| Routing | Rust `provider.routes` | known-provider name set (static + custom) |
| Capabilities | Rust `provider.capabilities` | — |
| Endpoint caps (learned) | Rust (`endpoint_caps.json`) | — |
| Health | Rust (`provider_health.json`) | Settings/diagnostics display |
| Friendly model error | Rust `provider.friendly_error` | — |
| Credentials | — | Python SecretStore (source of truth) |
| Turn loop / compaction / context | — | Python engine (reasoning) |
| Tool-call salvage | — | Python (pure regex, deferred per ADR-047) |
| Message conversion (convert_messages) | — | Python (pure parsing, deferred) |

## Protocol Version History

| Version | ADR | Change |
|---|---|---|
| 14 | ADR-043 | task.complete_run (R4 Phase 2) |
| 15 | ADR-047 | Streaming ABI (R5 Phase 0) |

## Deferred Items

The following pure-parsing functions remain in Python, explicitly deferred
per ADR-047 Phase 3 ("or deferred"):

- **Tool-call salvage** (`_maybe_salvage_tool_calls`, ~280 lines): complex
  regex recovery of structured calls from text. Pure, deterministic, no
  state/persistence. Decision value is marginal (recovers from non-conforming
  endpoints only).
- **Message conversion** (`convert_messages`/`convert_tools`): canonical
  history → Anthropic/OpenAI Responses format. Pure, deterministic. Called
  at provider build time, not in the hot path.
- **`_STOP_REASON_MAP`**, **`_uses_budget_thinking`**, cache breakpoints:
  small, pure functions deeply intertwined with provider-specific message
  conversion. Migrating them would split the conversion logic awkwardly.

These can be migrated in a future R5.1 if the Python parsing surface becomes
a bottleneck or a correctness concern.

## R5 Completion Criteria

Per ADR-047, R5 is complete when the provider transport, decision, and
parsing authority has moved to Rust. The transport (Phase 1) and decision
(Phase 2) are fully migrated. The parsing surface (Phase 3) has its
decision-like component (`friendly_model_error`) migrated; the pure regex
parsing is deferred.

The production wiring (Phase 1f) makes the Rust transport the default when
the delta_core binary is available (production/desktop). The SDK path is
retained for dev/test environments without the binary.

## Rollback

Each phase is an independent PR with independent rollback (Git revert). No
user data migration — provider state is `endpoint_caps.json` /
`provider_health.json` (best-effort state files, not user history).
