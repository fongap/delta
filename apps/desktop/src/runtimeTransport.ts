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

export interface DirectRuntimeAcceptance {
  ok: boolean;
  accepted?: boolean;
  runId?: string;
  state?: string;
  error?: string;
}

// -----------------------------------------------------------------------------
// Runtime controls (agent loop verbs)
// -----------------------------------------------------------------------------

export async function directRun(args: {
  sessionId: string;
  modelId: string;
  userInput: string;
  tools?: unknown;
  systemPrompt?: string;
  workspace?: string;
  messages?: unknown;
  maxIterations?: number;
  maxRetries?: number;
  source?: unknown;
  onEvent: (event: WsEventEnvelope) => void;
  onError?: (error: unknown) => void;
}): Promise<DirectRuntimeAcceptance> {
  const {
    sessionId, modelId, userInput,
    tools, systemPrompt, workspace, messages, maxIterations, maxRetries, source,
  } = args;
  try {
    const out = await invoke("runtime_run", {
      sessionId, modelId, userInput,
      tools, systemPrompt, workspace, messages, maxIterations, maxRetries, source,
    });
    if (out && (out as any).error) {
      args.onError?.((out as any).error);
      return { ok: false, error: (out as any).error };
    }
    return {
      ok: true,
      accepted: (out as any).accepted === true,
      runId: typeof (out as any).runId === "string" ? (out as any).runId : undefined,
      state: typeof (out as any).state === "string" ? (out as any).state : undefined,
    };
  } catch (e) {
    args.onError?.(e);
    return { ok: false, error: String(e) };
  }
}

export async function directResume(
  sessionId: string,
): Promise<DirectRuntimeAcceptance> {
  try {
    const out = await invoke("runtime_resume", { sessionId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return {
      ok: true,
      accepted: (out as any).accepted === true,
      runId: typeof (out as any).runId === "string" ? (out as any).runId : undefined,
    };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directRetry(
  sessionId: string,
): Promise<DirectRuntimeAcceptance> {
  try {
    const out = await invoke("runtime_retry", { sessionId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return {
      ok: true,
      accepted: (out as any).accepted === true,
      runId: typeof (out as any).runId === "string" ? (out as any).runId : undefined,
    };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directSteer(
  sessionId: string,
  text: string,
  source?: unknown,
): Promise<DirectRuntimeAcceptance> {
  try {
    const out = await invoke("runtime_steer", { sessionId, text, source });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return {
      ok: true,
      accepted: (out as any).accepted === true,
      runId: typeof (out as any).runId === "string" ? (out as any).runId : undefined,
    };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

export async function directFollowUp(
  sessionId: string,
  text: string,
  source?: unknown,
): Promise<DirectRuntimeAcceptance> {
  try {
    const out = await invoke("runtime_follow_up", { sessionId, text, source });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return {
      ok: true,
      accepted: (out as any).accepted === true,
      runId: typeof (out as any).runId === "string" ? (out as any).runId : undefined,
    };
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
  modelId: string,
): Promise<{ ok: boolean; error?: string; notice?: unknown }> {
  try {
    const out = await invoke("runtime_switch_model", { sessionId, modelId });
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

export async function directApproval(
  sessionId: string,
  decision: string,
  toolCallId?: string,
): Promise<{ ok: boolean; error?: string; toolCallId?: string }> {
  try {
    const out = await invoke("runtime_approval", { sessionId, decision, toolCallId });
    if (out && (out as any).error) return { ok: false, error: (out as any).error };
    return {
      ok: true,
      toolCallId: typeof (out as any).toolCallId === "string" ? (out as any).toolCallId : undefined,
    };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

async function invokeAuthority(command: string, args: Record<string, unknown> = {}): Promise<any> {
  try {
    return await invoke(command, args);
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

export const directGetSettings = () => invokeAuthority("settings_get");
export const directSetModelKey = (apiKey: string) =>
  invokeAuthority("settings_set_model_key", { apiKey });
export const directSetDefaultModel = (modelId: string) =>
  invokeAuthority("settings_set_default_model", { modelId });
export const directAddModel = (modelId: string) =>
  invokeAuthority("settings_add_model", { modelId });
export const directRemoveModel = (modelId: string) =>
  invokeAuthority("settings_remove_model", { modelId });
export const directSetOnboarded = (value: boolean) =>
  invokeAuthority("settings_set_onboarded", { value });
export const directSetLanguage = (language: string) =>
  invokeAuthority("settings_set_language", { language });
export const directSetContextBar = (shown: boolean) =>
  invokeAuthority("settings_set_context_bar", { shown });
export const directSetSessionsPeek = (count: number) =>
  invokeAuthority("settings_set_sessions_peek", { count });
export const directSetScratchBase = (path: string) =>
  invokeAuthority("settings_set_scratch_base", { path });
export const directSetNavLayout = (layout: "flat" | "grouped") =>
  invokeAuthority("settings_set_nav_layout", { layout });
export const directSetPdfSettings = (patch: Record<string, unknown>) =>
  invokeAuthority("settings_set_pdf", { patch });
export const directSetCompactionSettings = (patch: Record<string, unknown>) =>
  invokeAuthority("settings_set_compaction", { patch });
export const directSetSurfaces = () => invokeAuthority("settings_set_surfaces");
export const directGetProviders = () => invokeAuthority("providers_list");
export const directGetProtocols = () => invokeAuthority("provider_protocols");
export const directSetProvider = (
  name: string,
  fields: Record<string, string>,
  protocol?: string,
) => invokeAuthority("provider_set", { name, fields, protocol });
export const directRemoveProvider = (name: string) =>
  invokeAuthority("provider_remove", { name });
export const directFetchProviderModels = (name: string, fields: Record<string, string>) =>
  invokeAuthority("provider_fetch_models", { name, fields });
export const directVerifyProvider = (name: string, fields: Record<string, string>) =>
  invokeAuthority("provider_verify", { name, fields });

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
