// R6 mock transport — in-browser Tauri IPC mock for hermetic e2e.
//
// R6 removed the localhost FastAPI/WebSocket backend; the frontend now talks to Rust only
// through Tauri `invoke`/`listen`. The e2e harness runs in a plain browser (Vite dev), where
// `window.__TAURI_INTERNALS__` does not exist, so every `invoke` threw and the app never booted.
//
// This module injects a mock `__TAURI_INTERNALS__` BEFORE the SPA loads, so `isTauri()` is true
// and `invoke`/`listen` are answered from per-test in-memory state — a §8-permitted mock transport
// that never enters the production build. Command names match the real Rust commands and response
// shapes mirror the removed HTTP endpoints, so the frontend is exercised unchanged.
//
// The fake agent runs INSIDE the page: `runtime_run`/`runtime_approval`/`runtime_steer` handlers
// push envelopes via the injected `emit`, which the frontend's `listen("delta-runtime-event")`
// receives exactly like the real RuntimeHost. No WebSocket, no HTTP.

/**
 * Builds a `page.addInitScript` source string. `seed` (per-test in-memory state) and `sources`
 * (command handler bodies referencing `$state` and `emit`) are serialized into the page and
 * re-created there, so handlers are pure functions of `$state` and can mutate it across calls.
 */
export function installMockRuntimeInitScript(
  seed: Record<string, unknown>,
  sources: Record<string, string>,
): string {
  return `(${coreSourceStr.toString()})(this, ${JSON.stringify(seed)}, ${JSON.stringify(
    sources,
  )});`;
}

function coreSourceStr(
  globalThisRef: Record<string, unknown>,
  seed: Record<string, any>,
  sources: Record<string, string>,
): void {
  const $state = seed;
  try {
    const saved = globalThisRef.localStorage?.getItem("delta-e2e-mock-state");
    if (saved) Object.assign($state, JSON.parse(saved));
  } catch {
    /* first navigation or storage unavailable */
  }
  const pendingState = globalThisRef.__DELTA_MOCK_STATE_PATCH__ as
    | Record<string, unknown>
    | undefined;
  if (pendingState) {
    for (const [key, value] of Object.entries(pendingState)) {
      if (value && typeof value === "object" && !Array.isArray(value) && $state[key] && typeof $state[key] === "object" && !Array.isArray($state[key])) Object.assign($state[key], value);
      else $state[key] = value;
    }
  }
  const callbacks: Record<string, (data: unknown) => void> = {};
  const listeners: Record<string, string[]> = {};
  let nextId = 0;
  const RUNTIME_EVENT = "delta-runtime-event";

  function persist(): void {
    try {
      globalThisRef.localStorage?.setItem("delta-e2e-mock-state", JSON.stringify($state));
    } catch {
      /* state containing an in-flight browser handle can wait for the next command */
    }
  }

  function emit(eventName: string, payload: unknown): void {
    (listeners[eventName] || []).forEach((cbId) => {
      const cb = callbacks[cbId];
      if (typeof cb === "function") cb({ event: eventName, payload });
    });
  }

  const commands: Record<string, (args: Record<string, any>) => unknown> = {};
  function setCommand(name: string, src: string): void {
    try {
      // eslint-disable-next-line no-new-func
      commands[name] = new Function("$state", "emit", `"use strict"; return (${src});`)(
        $state,
        emit,
      );
    } catch (error) {
      ($state.__mockCompileErrors ||= {})[name] = String(error);
    }
  }
  for (const [name, src] of Object.entries(sources)) setCommand(name, src);
  const pendingCommands = globalThisRef.__DELTA_MOCK_COMMAND_OVERRIDES__ as
    | Record<string, string>
    | undefined;
  for (const [name, src] of Object.entries(pendingCommands || {})) setCommand(name, src);

  const internals: Record<string, unknown> = {
    invoke(cmd: string, args: Record<string, any> = {}): Promise<unknown> {
      try {
        if (cmd === "plugin:event|listen") {
          const eventName = args.event as string;
          const cbId = args.handler as string;
          listeners[eventName] = listeners[eventName] || [];
          listeners[eventName].push(cbId);
          // Boot readiness for the runtime channel: when the SPA subscribes to session runtime
          // events, answer with a `ready` envelope for the most-recent session (boot-resume
          // target) so the composer goes live — mirrors the removed socket's on-connect `ready`.
          if (eventName === RUNTIME_EVENT && !seed.__bootReady) {
            seed.__bootReady = true;
            const sessions = (seed as any).sessions as Array<{ session_id: string }> | undefined;
            const boot = sessions && sessions[0] && sessions[0].session_id;
            if (boot) {
              const runtimeSequences = ((seed as any).runtimeSequences ||= {});
              runtimeSequences[boot] = Math.max(runtimeSequences[boot] || 0, 1);
              emit(eventName, {
                type: "ready",
                version: 1,
                sessionId: boot,
                sequence: 1,
                payload: { model: "anthropic:claude-opus-4-8", mode: "interactive" },
              });
            }
          }
          return Promise.resolve(cbId);
        }
        if (cmd === "plugin:event|unlisten") {
          const eventName = args.event as string;
          const cbId = (args.handler as string) || (args.eventId as string);
          if (listeners[eventName]) {
            listeners[eventName] = listeners[eventName].filter((id) => id !== cbId);
          }
          return Promise.resolve(null);
        }
        const fn = commands[cmd];
        if (fn) {
          return Promise.resolve(fn(args || {})).then((value) => {
            persist();
            return value;
          });
        }
        return Promise.resolve({ ok: false, error: `mock: unknown command ${cmd}` });
      } catch (err) {
        return Promise.resolve({ ok: false, error: String(err) });
      }
    },
    transformCallback(cb: (data: unknown) => void): string {
      nextId += 1;
      const id = `cb_${nextId}`;
      callbacks[id] = cb;
      return id;
    },
    unregisterCallback(id: string): void {
      delete callbacks[id];
    },
  };

  globalThisRef.__TAURI_INTERNALS__ = internals;
  // @tauri-apps/api/event.js `_unlisten` reads this global directly (event.js:43) — provide it
  // so the unlisten returned by `listen()` no-ops rather than crashing.
  globalThisRef.__TAURI_EVENT_PLUGIN_INTERNALS__ = {
    unregisterListener: function unregisterListener(): void {},
  };
  // Driver handle so the Node side can push app-wide events too (sendAppEvent).
  globalThisRef.__DELTA_MOCK__ = { state: $state, commands, emit, setCommand };
}
