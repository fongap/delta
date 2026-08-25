import { afterEach, expect, it, vi } from "vitest";
import { getHealth, Session } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

// P0-A2: in desktop mode the shell injects only proxy endpoints â€?no token global exists
// and none may be consulted. The renderer attaches auth ONLY when a dev token is present.
it("desktop mode: sends no token header and connects WS without subprotocols, even if a stale token global exists", async () => {
  vi.stubGlobal("__COWORKER_API_TOKEN__", "stale-launch-token");
  const request = vi.fn(async (_url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-OpenWorker-Token")).toBeNull();
    return {
      json: async () => ({
        status: "ok",
        default_workspace: null,
        model: "demo",
        protocolVersion: 1,
        capabilities: [],
      }),
    } as Response;
  });
  vi.stubGlobal("fetch", request);

  class FakeWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    readyState = FakeWebSocket.CONNECTING;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    send = vi.fn();

    constructor(
      public readonly url: string,
      public readonly protocols?: string | string[],
    ) {}
  }
  vi.stubGlobal("WebSocket", FakeWebSocket);

  await getHealth();
  expect(request).toHaveBeenCalledOnce();

  const session = new Session("s1", "/workspace", "code", { onEvent: vi.fn() });
  const socket = (session as unknown as { ws: FakeWebSocket }).ws;
  expect(socket.protocols).toBeUndefined();
});

// Pure-browser development against a directly-started sidecar keeps working: the token
// comes from the dev sources (vite define / VITE_COWORKER_API_TOKEN / runtime override).
it("browser dev mode: authenticates REST and session WebSocket calls with the dev token", async () => {
  vi.stubGlobal("__OCW_BROWSER_DEV_TOKEN__", "launch-token");
  const request = vi.fn(async (_url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-OpenWorker-Token")).toBe("launch-token");
    return {
      json: async () => ({
        status: "ok",
        default_workspace: null,
        model: "demo",
        protocolVersion: 1,
        capabilities: [],
      }),
    } as Response;
  });
  vi.stubGlobal("fetch", request);

  class FakeWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    readyState = FakeWebSocket.CONNECTING;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    send = vi.fn();

    constructor(
      public readonly url: string,
      public readonly protocols?: string | string[],
    ) {}
  }
  vi.stubGlobal("WebSocket", FakeWebSocket);

  await getHealth();
  expect(request).toHaveBeenCalledOnce();

  const session = new Session("s1", "/workspace", "code", { onEvent: vi.fn() });
  const socket = (session as unknown as { ws: FakeWebSocket }).ws;
  expect(socket.protocols).toEqual(["openworker", "launch-token"]);
});
