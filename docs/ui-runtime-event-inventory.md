# Delta UI Runtime Event Inventory (UI-002)

This document inventories the WebSocket and SSE boundary used by `surfaces/gui`
in the current Delta working tree. It records the strict current v1 boundary and its
validation/diagnostic behavior; reconnect and domain event normalization remain later work.

## Scope and notation

- Snapshot: 2026-08-23, local commit `0db7225d` plus the current uncommitted working tree.
- Producers: `coworker/events.py`, `coworker/engine.py`, `coworker/server/app.py`,
  and `coworker/server/manager.py`.
- Transport and consumers: `surfaces/gui/src/api.ts`, `types.ts`, `App.tsx`, and
  the components reached from `App` state.
- All current session and app-wide event frames use the strict v1 envelope
  `{type, version, sessionId, sequence, payload}`. A `data` field is not accepted.
- `sessionId` is a string on every session-socket event. App-wide events use their related
  session id when one exists, otherwise `null`; they do not use a second envelope shape.
- Payload notation uses `?` for optional fields and `|` for alternatives.

## Transport surfaces

| Transport | Direction | Lifecycle | Parsing and failure behavior |
|---|---|---|---|
| `/ws/session/{sessionId}?workspace=...&agent=...` | bidirectional | A `Session` instance is created when the selected session/agent changes. It reconnects 5 seconds after an unexpected close; explicit cleanup cancels reconnection. Commands created while waiting reconnect are queued, but commands already sent are never replayed. | `api.ts` requires the strict v1 envelope, the selected session id, and a known string event type. Malformed, invalid, forbidden-`data`, mismatched-session, and unknown frames are ignored with a deduplicated diagnostic. Additive fields are discarded before `App` receives `WsEvent`; `payload` remains `any`. |
| `/ws/events` | server to UI in normal use | One app-wide socket; reconnects 5 seconds after close until its cleanup runs. | `api.ts` requires the same strict v1 envelope and the known app-wide event type. The server ignores inbound text frames. |
| SSE / `EventSource` | none | No SSE route or client was found. | No `EventSource`, `text/event-stream`, or SSE response implementation was found in the audited source. |

Both WebSocket consumers keep a bounded recent-sequence window per `sessionId`.
Duplicate sequences are discarded across reconnects. A previously unseen lower sequence is
diagnosed as out of order but still delivered, so finalized messages and terminal events are
not lost. A session `ready` event resets its sequence epoch when the runtime process restarts.

## Session WebSocket: server to UI

The **Consumer / behavior** column describes current runtime behavior, including
events that are known to the server but not consumed by the UI.

| Event type | Payload emitted by the server | Producer path | Consumer / behavior |
|---|---|---|---|
| `ready` | `{session_id, agent, model, mode, workspace, command_trust}` | `server/app.py` after socket setup | `App`: marks connected, adopts model/mode/workspace, and may show `WorkspaceTrustPrompt`. |
| `turn_start` | `{input, source?, display?}`; retry uses empty input, durable resume uses `"(resumed)"` | `engine.run/retry/resume` | `App`: marks running, clears stream buffers, then adds a user or connector item when it was not already rendered; reaches `Transcript` / `ConnectorMessageCard`. |
| `assistant_delta` | `{text}` | `engine._loop` streaming provider output | `App`: appends to the live answer buffer; rendered by `Transcript`/`Markdown`. |
| `reasoning_delta` | `{text}` | `engine._loop` streaming reasoning output | `App`: appends to the live reasoning buffer; rendered by `ThinkingBlock`. |
| `assistant_message` | `payload` = `{text, tool_calls: string[], reasoning?, usage?: {model, input, output, cache_read, cache_write}}` | `engine._loop` finalized model turn; `SessionManager.broadcast_session` adds envelope metadata | `App` consumes v1 `payload`, accumulates usage, finalizes an assistant `Item`, and clears stream buffers. |
| `tool_proposed` | `{name, arguments}` | `engine._handle_tool_calls` before authorization | `App`: appends a pending tool item for `Transcript`; `todo_write` also updates `TodoPanel`. |
| `permission_required` | `{name, arguments, reason, category, standing_target}` | `engine._authorize` | `App`: ignored when unattended; otherwise creates an approval item rendered by `ApprovalCard` and later folded into `Transcript`. |
| `directory_requested` | `{reason, path, writable}` | `engine._handle_directory_request` | `App`: ignored when unattended; otherwise creates a request rendered by `DirectoryRequestCard`. |
| `question_requested` | `{question, options, allow_text, multi, header, questions}` | `server/app.py` attended `ask_user` bridge | `App`: creates a question item rendered through `InboxItemCard`. |
| `plan_proposed` | `{plan}` | `engine._handle_plan_proposal` | `App`: ignored when unattended; otherwise creates a request rendered by `PlanCard`. |
| `tool_started` | `{name}` | `engine._handle_tool_calls` before execution | Declared in `EventType`, but `App` has no switch case. Silently ignored, except that it clears an active compacting indicator. |
| `tool_finished` | `{name, status, result_preview?, reason?, display?, standing_rule?}` where production statuses include `ok`, `error`, `denied`, `interrupted` | engine result, denial, interruption, directory/plan/question completion paths | `App`: updates the most recent matching tool in `Transcript`; browser/file-write tools also refresh `RightRail`. |
| `iteration_end` | `{iteration}` | `engine._loop` after a tool iteration; durable resume can emit iteration `0` | Declared in `EventType`, but `App` has no switch case. Silently ignored, except that it clears an active compacting indicator. |
| `turn_end` | `{status, iterations}` where status is `completed` or `max_iterations_exceeded` | `engine._loop` | `App`: only renders a warning for `max_iterations_exceeded`; otherwise no visible action. Final running-state cleanup is handled by `turn_done`. |
| `error` | `{error, error_type?, raw?}`; setup/background failures may emit only `{error}` | engine provider failure, invalid workspace setup, or background-turn exception | `App`: flushes partial streams and appends a retriable warning notice to `Transcript`. |
| `interrupted` | `{iterations}` | engine cancellation paths | `App`: flushes partial streams and appends an interruption notice to `Transcript`. |
| `compacting` | `{}` | `engine._loop` before automatic or overflow compaction | `App`: sets the compacting indicator. Any later event other than another `compacting` clears it. |
| `compacted` | `{text}` | successful summary or trim in `engine._loop` | `App`: appends an informational compaction marker to `Transcript`. |
| `input_rejected` | `{error}` | `server/app.py` WebSocket validation/rate-limit/unknown-command path | `App`: appends a non-retriable warning notice to `Transcript`. |
| `model_changed` | `{model, text}` | `server/app.py` after a mid-session model switch | `App`: updates the active model and appends an informational marker to `Transcript`. |
| `memory_saved` | `{id, scope, summary, content, previous}` | `server/manager.py` memory-save notifier | `App`: appends an undoable memory item to `Transcript` and emits `MEMORY_CHANGED` for `MemorySection`. |
| `turn_done` | `{}` | `server/app.py` and background delivery `finally` blocks | `App`: clears running state, refreshes sessions/messages/artifacts, and finalizes a manual automation run when applicable. |
| `task_done` | `{task, id, text, run_id}` | `server/manager.py` scheduled-task completion notification | **Server-emitted but absent from UI `EventType` and `App` cases.** The API boundary diagnoses and ignores it before `App`; it no longer changes the compacting indicator. |
| `session_title` | `{session_id, title}` | `server/manager.py` best-effort auto-title broadcast | **Server-emitted but absent from UI `EventType` and `App` cases.** The API boundary diagnoses and ignores it before `App`; sidebar polling/post-turn refresh still supplies the title later. |

## App-wide WebSocket: server to UI

| Event type | Payload | Producer path | Consumer / behavior |
|---|---|---|---|
| `automation_run_started` | `{task_id, task_title, session_id, workspace, agent, trigger}` | `server/manager.py` when a scheduled run starts | `App`: creates the five-second run toast and emits `AUTOMATIONS_CHANGED` so scheduled-task badges refresh. |
| any other parseable type | unconstrained | future or unexpected producer | `connectEvents` diagnoses it once per event type and does not invoke the consumer. |
| malformed JSON frame | not applicable | transport corruption or invalid producer | `api.ts` diagnoses the stream once and ignores the frame. |

## Session WebSocket: UI to server commands

These are commands sent over the same session socket. They are included to make the
bidirectional WebSocket boundary complete, although the UI-002 acceptance matrix above
focuses on server events consumed by components.

| Command type | Payload sent by `Session` | UI origin | Server behavior for unknown/invalid input |
|---|---|---|---|
| `user_message` | `{text, model?, attachments?, skill?}` | `App` composer / automation run prompt | Validates text, attachment shape/size, model, and skill; emits `input_rejected` on invalid input. |
| `approval` | `{decision}` | `ApprovalCard` via `App` | Resolves the oldest pending session Inbox prompt; missing decision defaults to `deny`. |
| `directory_response` | `{granted, path?, writable}` | `DirectoryRequestCard` via `App` | Serializes the decision and resolves the pending prompt. |
| `plan_response` | `{approved, mode?, feedback?}` | `PlanCard` via `App` | Serializes the decision and resolves the pending prompt. |
| `question_response` | `{answer}` | `InboxItemCard` via `App` | Resolves the pending question with a string answer. |
| `interrupt` | `{}` | stop action in `App` | Requests engine interruption. |
| `retry` | `{}` | retriable error action in `Transcript` | Claims a retry turn; the engine ignores it unless history ends in a retriable error. |
| `set_mode` | `{mode}` | `App` mode control | Invalid enum values are silently ignored by the server. |
| `set_model` | `{model}` | `App` model control | Non-string values produce `input_rejected`; a valid mid-session change may emit `model_changed`. |
| any other type | unconstrained | invalid or future client | Server emits `input_rejected` with `Unknown WebSocket message type: ...`. |
| malformed/non-object frame | not applicable | invalid client | Server emits `input_rejected`; rate-limit excess also emits rejection and closes with code `1008`. |

## Type and consumer coverage matrix

| Classification | Event types | Current behavior |
|---|---|---|
| Server-emitted and handled by `App` | `ready`, `turn_start`, `assistant_delta`, `reasoning_delta`, `assistant_message`, `tool_proposed`, `permission_required`, `directory_requested`, `question_requested`, `plan_proposed`, `tool_finished`, `turn_end`, `error`, `input_rejected`, `interrupted`, `model_changed`, `memory_saved`, `compacting`, `compacted`, `turn_done` | Explicit switch cases. Payload remains `any`; payload-specific schemas are deferred to the event normalizer task. |
| Server-emitted, declared, but not handled | `tool_started`, `iteration_end` | Silently ignored; both incidentally clear `compacting`. |
| Server-emitted but not declared or handled | `task_done`, `session_title` | Diagnosed once per type and ignored at the API boundary; neither reaches `App` or clears `compacting`. |
| Declared by UI but no server producer found | `inbound` | If received, silently ignored and clears `compacting`. It appears to be a stale/reserved type. |
| App-wide handled type | `automation_run_started` | Explicit `connectEvents` filter and toast behavior. |
| Arbitrary unknown session type | any other string | Diagnosed once per type and ignored at the API boundary; it does not reach `App`. |
| Arbitrary unknown app-wide type | any other string | Diagnosed once per type and ignored before the app-wide consumer is invoked. |

## Observed contract gaps

- `WsEvent.payload` is `any`; there is no discriminated payload type per event.
- There is no duplicate or out-of-order detection.
- Unknown, invalid, and malformed events are ignored with deduplicated diagnostics;
  there is not yet a structured diagnostics sink.
- The app-wide socket reconnects after close; the session socket does not reconnect
  independently of React session lifecycle changes.
- `task_done` and `session_title` demonstrate server/UI event-list drift.

These are inventory findings only. Versioning, typed DTO payloads, a domain event
normalizer, ordering, and reconnect semantics remain later contract tasks.
