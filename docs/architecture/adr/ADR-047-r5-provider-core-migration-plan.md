# ADR-047: R5 Provider Core Migration Plan

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-11 |
| **Related** | ADR-009 (Delta Core Architecture), ADR-003 (Provider Protocol Model), ADR-033 (R3 Plan), ADR-041 (R4 Plan), ADR-046 (R4 Final Convergence) |

## Context

R4 (Runtime) is complete (ADR-046). All runtime control authority — run
lifecycle transitions, automation run completion, scheduler due query, resume
orchestration — is Rust-authoritative. Protocol version is 14.

R5 (Provider Core) migrates the provider (model access) domain. Per
`docs/governance/rust-core-migration.md` the R5 domains are:

- OpenAI-compatible
- Anthropic-compatible
- streaming
- tool calls
- reasoning metadata
- usage
- retry
- timeout
- routing
- fallback

The Rust Core is declared (ADR-009) the authority over Provider Core, with
Python demoted to Capability Worker. This is the first R that migrates a
**network-facing, streaming** capability — qualitatively different from
R1–R4, which migrated DB write + decision authority over the existing
synchronous stdio protocol.

## Audit Findings

### Rust side today

`delta_core` is a synchronous request/response DB + decision service. It has
**no async runtime, no HTTP client, no streaming**. `Cargo.toml` deps: csv,
regex, rusqlite, serde/serde_json, sha2, thiserror, uuid, time. The binary
speaks line-delimited JSON over stdio (not JSON-RPC — no ids, no streaming;
`delta_core.rs:49-60` "minimum host interface"). `main()` is a blocking
`for line in stdin` loop. Protocol version 14.

### Python side today (the R5 surface)

| Domain | Current Python authority | Wire protocol | Authority gap |
|---|---|---|---|
| OpenAI Chat Completions | `OpenAIProvider` (openai_provider.py) — SDK call, param-fix retry, endpoint caps, health, tool-call salvage | official `openai` SDK | All in Python |
| OpenAI Responses | `OpenAIResponsesProvider` (openai_responses.py) — /v1/responses, reasoning sidecar, param-fix retry | official `openai` SDK | All in Python |
| Anthropic Messages | `AnthropicProvider` (anthropic_provider.py) — thinking, cache_control, document/pdf, beta fallback | official `anthropic` SDK | All in Python |
| Routing | `ProviderRouter` (router.py) — `prefix:rest` to client, cached behind lock, `invalidate()` | — | All in Python |
| Capabilities | `capabilities.py` heuristics + `matrix.py` static table | — | Pure data/heuristics |
| Endpoint caps (learned) | `endpoint.py` — `record_rejection` to `endpoint_caps.json`, `merge` declared>learned>defaults | file I/O | Stateful, in Python |
| Health | `health.py` — rolling per-(endpoint,model) `provider_health.json`, `route_healthy`, `degraded` | file I/O | Stateful, in Python |
| Retry / timeout | retry classification **already Rust** (`retry.classify`); backoff math + Retry-After parse + engine loop in Python | — | Retry decision already Rust (ADR-039) |
| Model list / key verify | `registry.verify_provider_key` / `fetch_provider_models` — raw httpx GET /models | httpx | In Python |

### Key architectural gap: streaming over stdio

The single biggest obstacle to a Rust-first Provider Core is that the current
`delta_core` host protocol is **synchronous, non-streaming, line-delimited
JSON**. Provider streaming is the core feature of the provider layer (token
streaming to the GUI, `reasoning_delta` for thinking display, `text_delta`
for live text). The audit found no stdio equivalent for:

- incremental `StreamChunk` deltas (currently a Python generator pushed
  through an executor thread into an `asyncio.Queue`);
- in-flight cancellation (`chunks.close()` to GeneratorExit teardown of the
  upstream HTTP request, engine.py:903-907);
- TTFT stall abort (separate `threading.Event`, engine.py:837/935).

Any Rust-first Provider Core therefore requires a **protocol capability
upgrade** before authority can move.

## Key Distinction

Same principle as R3/R4 (ADR-033/041):

> **Pure functions and runtime mechanics stay Python; decision authority
> (state transitions with persisted consequences) goes to Rust.**

For Provider Core the split is:

- **(b) Wire-protocol mechanics** — the actual HTTP transport: SDK calls,
  chunk iteration, event dispatch. The *natural Rust target* (a language
  designed for networked, concurrent I/O), and what ADR-009 intends by
  "Rust Core = Provider Core".
- **(a) Decision/authority logic** — param negotiation policy
  (`_apply_endpoint_caps`, `_param_fix_retry`, `_pin_reasoning_effort`),
  thinking/max_tokens interplay, endpoint-caps merge precedence, health
  routing, model routing (`_provider_name`/`_bare`), capability heuristics.
- **(c) Pure parsing/conversion** — `_usage_from`, `_parse_tool_calls`,
  `convert_messages`/`convert_tools`, tool-call salvage, `_STOP_REASON_MAP`,
  `matrix.py` static table. Unit-testable in either language.

The R5 decision is **how much** of the provider layer moves. Two candidate
scopes:

### Scope A — Rust transport (thin decision)

Rust implements the **wire protocol only** (OpenAI Chat Completions +
Responses + Anthropic Messages over an HTTP client, streaming + tool-call
parse + usage + reasoning extraction). Python keeps routing, capabilities,
endpoint-caps, health, and salvage as capability-plane logic calling into
Rust for the actual completion.

- Pro: smallest Rust surface; streaming stays tractable behind a new
  stream-capable protocol.
- Con: Python still owns the *decision* surface (caps, health, routing),
  which ADR-009 assigns to Rust. Does not satisfy "Rust = Provider Core".

### Scope B — Rust decision + transport (full Provider Core)

Rust owns the whole provider layer: routing, capabilities, endpoint-caps,
health, retry policy, timeout, streaming, tool-call parse, usage, reasoning
— exposing `provider.complete` / `provider.stream` / `provider.capabilities`
/ `provider.routes` to Python. Python (engine) becomes a thin consumer of
`AssistantTurn` / `StreamChunk`, and the SecretStore stays the credential
source Python feeds in.

- Pro: matches ADR-009's Provider Core authority assignment.
- Con: largest Rust surface; the streaming protocol upgrade is mandatory;
  higher regression risk on the hot path (every model call).

Both scopes are valid for R5. Scope B is the end-goal; the question is
phasing. Because the streaming protocol gap is the gating dependency for
either scope, the migration must first solve **how provider I/O crosses the
Rust/Python boundary**, then move authority in layers.

## Phases

R5 is the first R to touch a streaming, network-facing, stateful capability.
Phasing differs from R2 (authority switch) / R3-R4 (decision migration): it
must first establish the transport boundary, then move decision authority,
then migrate the pure-parsing surface.

### Phase 0 — Streaming ABI (pre-plumbing)

Establish a stream-capable, cancellable capability ABI between Rust and
Python, reusing `docs/architecture/capability-abi.md` (JSON-RPC / NDJSON
over stdio + Progress / Cancellation). `delta_core`'s line protocol is
upgraded to support a streaming request/response pair where one command
yields incremental result lines (deltas) and a terminal line.

Transport options:

- **Option 1 (recommended)**: extend the existing `delta_core` stdio
  protocol with a streamed response framing (a `start` + many `delta` +
  `done`/`error` lines per command). Minimal new machinery; `DeltaCoreClient`
  learns an `iter` command mode.
- Option 2: a separate provider subprocess speaking NDJSON deltas. Cleaner
  isolation, but two processes and a second wire contract to version.
- Option 3: Rust HTTP client *in process* behind `provider.stream`, with
  cancellation via a `provider.cancel` command + connection drop.

Option 1 is recommended: the in-process `delta_core` already owns
retry/policy authority (`retry.classify`), and keeping provider calls in the
same process avoids a second process-supervision and protocol-version
surface.

Must define, per capability-abi.md: progress deltas, cancellation
(`provider.cancel`), typed errors, timeout, version compatibility. Must
preserve the `StreamChunk` / `AssistantTurn` Python contract unchanged
(base.py) so the engine keeps working.

Protocol bump to v15.

### Phase 1 — Rust transport authority (wire protocol)

Implement in Rust the actual provider wire protocols (Scope A / the
mechanics layer):

- OpenAI Chat Completions (`/chat/completions`)
- OpenAI Responses (`/v1/responses`) — reasoning + sidecar items
- Anthropic Messages (`/v1/messages`) — thinking, cache_control, document

Rust owns: HTTP request/response, chunk iteration, tool-call parse, usage
extraction, reasoning extraction, streaming, truncation guard, param-fix
retry (wire-level), in-flight cancel. Adds an HTTP client dep (e.g.
`reqwest` + a blocking or tokio runtime).

Python providers become thin callers: build the profile/credentials, then
`provider.complete` / `provider.stream` to Rust, get back `AssistantTurn` /
`StreamChunk`. The `_bare`/`_provider_name` routing stays Python for now
(decision), but the *call* is Rust.

### Phase 2 — Rust decision authority (routing / caps / health)

Move the decision surface to Rust (Scope B completion):

- Model routing (`provider.routes` — `_provider_name`/`_bare`, client cache
  + `invalidate`).
- Capabilities (`provider.capabilities` — `capabilities.py` heuristics +
  `matrix.py` table).
- Endpoint caps (`endpoint.py` — `record_rejection`/`merge`/`learned_caps`,
  `endpoint_caps.json`).
- Health (`health.py` — `record_call`/`profile`/`route_healthy`/`degraded`,
  `provider_health.json`).
- Retry policy integration (already Rust — `retry.classify`).

Python (engine + settings + diagnostics) becomes a thin consumer: asks Rust
for the routed client, capabilities, health, route order. SecretStore stays
the credential source Python feeds in.

### Phase 3 — Rust parsing surface (salvage / conversion)

Move the pure-parsing/conversion surface to Rust (portable, unit-testable):

- Tool-call salvage machinery (openai_provider.py:459-736).
- `convert_messages`/`convert_tools` (both protocols) + sidecar replay.
- `_STOP_REASON_MAP`, `_uses_budget_thinking`, cache breakpoints.
- `friendly_model_error` mapping (errors.py).

### Phase 4 — R5 Final Convergence

Document final R5 authority matrix, protocol version history, mark R5
complete.

## Migration Order

```
Phase 0 (Streaming ABI)  ->  Phase 1 (Transport)  ->  Phase 2 (Decision)
                                                          |
                                                 Phase 3 (Parsing)
                                                          |
                                                 Phase 4 (Convergence)
```

Phase 0 before everything: no provider authority can move until the streaming
boundary exists. Phase 1 before Phase 2: transport must be proven stable
before decision logic rides on it. Phase 3 (pure parsing) can be folded into
Phase 1/2 where natural, or deferred.

## Authority After R5 (Scope B)

| Domain | Rust Authority | Python Capability |
|---|---|---|
| OpenAI Chat Completions transport | Rust `provider.complete`/`stream` | profile/credential build |
| OpenAI Responses transport | Rust (reasoning sidecar) | profile/credential build |
| Anthropic Messages transport | Rust (thinking, cache_control) | profile/credential build |
| Streaming / deltas | Rust (stream protocol, cancel) | engine delta-to-UI event mapping |
| Tool-call parse / salvage | Rust | — |
| Usage / reasoning extraction | Rust | — |
| Retry policy | Rust `retry.classify` (already) | backoff math, engine loop |
| Routing | Rust `provider.routes` (client cache + invalidate) | — |
| Capabilities | Rust `provider.capabilities` | — |
| Endpoint caps (learned) | Rust (`endpoint_caps.json`) | — |
| Health | Rust (`provider_health.json`, `route_healthy`) | Settings/diagnostics display |
| Credentials | — | Python SecretStore (source of truth) |
| Turn loop / compaction / context | — | Python engine (reasoning) |

## Risk Assessment

- **Phase 0** is the highest-risk change: it introduces a streaming/cancel
  capability to the stdio protocol for the first time. Must not break the
  existing synchronous command surface. Mitigation: the streamed framing is
  additive (a new command mode); existing commands unchanged.
- **Phase 1** is high risk: it moves the actual model call (hot path, every
  turn) to Rust HTTP. Regression on wire-format edge cases (tool-call JSON,
  reasoning fields, cache_control, truncation) is the main risk. Mitigation:
  the Python providers are a thin caller, so a behavior diff is isolated to
  the transport; extensive cross-language tests comparing parsed output
  against a recording HTTP mock.
- **Phase 2** is medium risk: routing/caps/health decision moves; the
  stateful JSON files (`endpoint_caps.json`, `provider_health.json`) must be
  read/written by Rust, or migrated. Must preserve the learned-caps + health
  semantics exactly.
- **Phase 3** is low risk: pure parsing, unit-testable.
- **Credential security**: API keys must never cross the stdio boundary in
  logs. Rust reads keys from the SecretStore file / env the same way Python
  does today (`resolve_api_key`), or Python passes them in-memory per
  request. Must be decided explicitly.

## Rollback

Each phase is an independent PR with independent rollback (Git revert). No
user data migration required — provider state is `endpoint_caps.json` /
`provider_health.json` (rewritable, best-effort state files, not user
history). The `StreamChunk` / `AssistantTurn` Python contract is unchanged,
so the engine and GUI keep working regardless of which side owns the wire.