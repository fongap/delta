// R6 direct IPC bridge: React -> Tauri invoke/listen -> Rust Runtime.
//
// This is the desktop transport. In the browser it is inert (isTauri() === false),
// and api.ts falls back to the HTTP/WebSocket path. In the Tauri shell it replaces
// the localhost FastAPI + proxy entirely: commands via `invoke`, runtime events
// via `listen("delta-runtime-event")`.
//
// The event payloads match the existing RuntimeEventEnvelopeV1 contract so the
// frontend's `parseRuntimeEvent` / sequence gate work unchanged.

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { RuntimeEventEnvelopeV1 } from "./runtime-contract";
import { isTauri } from "./tauri";

/** The Tauri event name carrying runtime event frames. */
const RUNTIME_EVENT_CHANNEL = "delta-runtime-event";

export const canUseDirectIpc = (): boolean => isTauri();

// -----------------------------------------------------------------------------
// Runtime controls (agent loop verbs)
// -----------------------------------------------------------------------------

export async function directRun(args: {
  sessionId: string;
  model: string;
  protocol: string;
  apiKey: string;
  baseUrl: string;
  userInput: string;
  tools?: unknown;
  settings?: unknown;
  systemPrompt?: string;
  workspace?: string;
  messages?: unknown;
  maxIterations?: number;
  maxRetries?: number;
  source?: unknown;
  onEvent: (event: WsEventEnvelope) => void;
  onError?: (error: unknown) => void;
}): Promise<{ ok: boolean; error?: string; result?: unknown }> {
  const {
    sessionId, model, protocol, apiKey, baseUrl, userInput,
    tools, settings, systemPrompt, workspace, messages, maxIterations, maxRetries, source,
  } = args;
  try {
    const out = await invoke("runtime_run", {
      sessionId, model, protocol, apiKey, baseUrl, userInput,
      tools, settings, systemPrompt, workspace, messages, maxIterations, maxRetries, source,
    });
    if (out && (out as any).error) {
      args.onError?.((out as any).error);
      return { ok: false, error: (out as any).error };
    }
    return { ok: true, result: (out as any).result };
  } catch (e) {
    args.onError?.(e);
    return { ok: false, error: String(e) };
  }
}

export async function directResume(
  sessionId: string,
): Promise<{ ok: boolean; error?: string; result?: unknown }> {
  try {
    const out = await invoke("runtime_resume", { sessionId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return { ok: true, result: (out as any).result };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directRetry(
  sessionId: string,
): Promise<{ ok: boolean; error?: string; result?: unknown }> {
  try {
    const out = await invoke("runtime_retry", { sessionId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return { ok: true, result: (out as any).result };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directSteer(
  sessionId: string,
  text: string,
  source?: unknown,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const out = await invoke("runtime_steer", { sessionId, text, source });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directFollowUp(
  sessionId: string,
  text: string,
  source?: unknown,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const out = await invoke("runtime_follow_up", { sessionId, text, source });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directCancel(
  sessionId: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const out = await invoke("runtime_cancel", { sessionId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directMessages(
  sessionId: string,
): Promise<unknown[]> {
  try {
    const out = await invoke("runtime_messages", { sessionId });
    return (out && (out as any).messages) || [];
  } catch {
    return [];
  }
}

export async function directSwitchModel(
  sessionId: string,
  model: string,
): Promise<{ ok: boolean; error?: string; notice?: unknown }> {
  try {
    const out = await invoke("runtime_switch_model", { sessionId, model });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return { ok: true, notice: (out as any).notice };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directTruncate(
  sessionId: string,
  index: number,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const out = await invoke("runtime_truncate", { sessionId, index });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directHealth(): Promise<unknown> {
  try {
    return await invoke("health");
  } catch (e) {
    return { status: "error", error: String(e) };
  }
}

// -----------------------------------------------------------------------------
// Runtime event subscription
// -----------------------------------------------------------------------------

/** A raw runtime event envelope as delivered by Tauri events. */
export interface WsEventEnvelope extends RuntimeEventEnvelopeV1<Record<string, unknown>> {}

/**
 * Subscribe to a session's runtime events via Tauri events.
 * Returns an unsubscribe function.
 */
export function directListenSession(
  sessionId: string,
  onEvent: (event: WsEventEnvelope) => void,
): () => void {
  let disposed = false;
  let unlisten: (() => void) | undefined;
  listen<WsEventEnvelope>(RUNTIME_EVENT_CHANNEL, (event) => {
    if (disposed) return;
    const payload = event.payload as WsEventEnvelope;
    // Filter to this session only.
    if (payload && payload.sessionId === sessionId) {
      onEvent(payload);
    }
  }).then((fn) => {
    if (disposed && fn) fn();
    else if (fn) unlisten = fn;
  });
  return () => {
    disposed = true;
    unlisten?.();
  };
}

const RUNTIME_SESSION_EVENT_TYPES = new Set<string>([
  "ready",
  "inbound",
  "turn_start",
  "assistant_delta",
  "reasoning_delta",
  "assistant_message",
  "tool_proposed",
  "permission_required",
  "directory_requested",
  "question_requested",
  "plan_proposed",
  "tool_started",
  "tool_finished",
  "iteration_end",
  "turn_end",
  "error",
  "input_rejected",
  "interrupted",
  "model_changed",
  "memory_saved",
  "compacting",
  "compacted",
  "turn_done",
]);

export { RUNTIME_SESSION_EVENT_TYPES };

// -----------------------------------------------------------------------------
// R6 Application Control Plane — Session/Workspace via direct IPC.
// Mirrors the removed FastAPI /v1/sessions + /v1/workspaces read/write shapes so
// api.ts callers can switch to IPC unchanged.
// -----------------------------------------------------------------------------

export async function directListSessions(workspace?: string): Promise<unknown> {
  try {
    return await invoke("sessions_list", workspace ? { workspace } : {});
  } catch (e) {
    return { sessions: [], error: String(e) };
  }
}

export async function directSessionMessages(sessionId: string): Promise<unknown> {
  try {
    return await invoke("session_messages", { sessionId });
  } catch (e) {
    return { messages: [], error: String(e) };
  }
}

export async function directSessionRename(
  sessionId: string,
  title: string,
): Promise<unknown> {
  try {
    return await invoke("session_rename", { sessionId, title });
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directSessionSetFlags(
  sessionId: string,
  flags: { pinned?: boolean; archived?: boolean },
): Promise<unknown> {
  try {
    return await invoke("session_set_flags", { sessionId, ...flags });
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directSessionDelete(sessionId: string): Promise<unknown> {
  try {
    return await invoke("session_delete", { sessionId });
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directRecentWorkspaces(): Promise<unknown> {
  try {
    return await invoke("workspaces_recent");
  } catch (e) {
    return { workspaces: [], error: String(e) };
  }
}