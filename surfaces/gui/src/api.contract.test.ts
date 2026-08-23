import { afterEach, expect, it, vi } from "vitest";
import { connectEvents, getHealth, Session } from "./api";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function stubJsonResponse(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ json: async () => body }) as Response));
}

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];
  readyState = FakeWebSocket.CONNECTING;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = FakeWebSocket.CLOSED;
  });

  constructor(public readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  emit(data: string) {
    this.onmessage?.({ data } as MessageEvent);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  disconnect() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
}

it("rejects a health response from outside the current runtime contract", async () => {
  stubJsonResponse({ status: "ok" });

  await expect(getHealth()).rejects.toThrow("current runtime contract");
});

it("accepts protocol v1 and safely ignores unknown capabilities or additive fields", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  stubJsonResponse({
    status: "ok",
    default_workspace: "C:/work",
    model: "demo",
    protocolVersion: 1,
    capabilities: ["provider.custom", "future.feature"],
    futureField: { enabled: true },
  });

  await expect(getHealth()).resolves.toEqual({
    status: "ok",
    default_workspace: "C:/work",
    model: "demo",
    protocolVersion: 1,
    capabilities: ["provider.custom"],
  });
  expect(warn).toHaveBeenCalledWith(expect.stringContaining("future.feature"));
});

it("rejects a runtime with a different protocol version", async () => {
  stubJsonResponse({
    status: "ok",
    default_workspace: "C:/work",
    model: "demo",
    protocolVersion: 2,
    capabilities: [],
  });

  await expect(getHealth()).rejects.toThrow("unsupported runtime protocolVersion 2");
});

it("delivers strict v1 session events and diagnoses unknown or malformed frames once", () => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const onEvent = vi.fn();
  new Session("s1", "/workspace", "cowork", { onEvent });
  const socket = FakeWebSocket.instances[0];

  socket.emit(JSON.stringify({
    type: "ready",
    version: 1,
    sessionId: "s1",
    sequence: 1,
    payload: { model: "demo", extra: true },
    extra: 1,
  }));
  socket.emit(JSON.stringify({
    type: "future_event",
    version: 1,
    sessionId: "s1",
    sequence: 2,
    payload: { value: 1 },
  }));
  socket.emit(JSON.stringify({
    type: "future_event",
    version: 1,
    sessionId: "s1",
    sequence: 3,
    payload: { value: 2 },
  }));
  socket.emit("not json");

  expect(onEvent).toHaveBeenCalledOnce();
  expect(onEvent).toHaveBeenCalledWith({
    type: "ready",
    version: 1,
    sessionId: "s1",
    sequence: 1,
    payload: { model: "demo", extra: true },
  });
  expect(warn.mock.calls.filter(([message]) => String(message).includes("future_event"))).toHaveLength(1);
  expect(warn).toHaveBeenCalledWith(expect.stringContaining("malformed"));
});

it("rejects the forbidden data field and accepts only the strict v1 envelope", () => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const onEvent = vi.fn();
  new Session("s1", "/workspace", "cowork", { onEvent });
  const socket = FakeWebSocket.instances[0];

  socket.emit(JSON.stringify({ type: "turn_start", data: { input: "removed shape" } }));
  socket.emit(JSON.stringify({
    type: "turn_start",
    version: 1,
    sessionId: null,
    sequence: 1,
    payload: { input: "invalid session event" },
  }));
  socket.emit(
    JSON.stringify({
      type: "assistant_message",
      version: 1,
      sessionId: "s1",
      sequence: 1,
      payload: { text: "v1", tool_calls: [] },
      futureEnvelopeField: true,
    }),
  );

  expect(onEvent).toHaveBeenCalledOnce();
  expect(onEvent).toHaveBeenCalledWith({
    type: "assistant_message",
    payload: { text: "v1", tool_calls: [] },
    version: 1,
    sessionId: "s1",
    sequence: 1,
  });
  expect(warn).toHaveBeenCalledWith(expect.stringContaining("forbidden data"));
  expect(warn).toHaveBeenCalledWith(expect.stringContaining("null sessionId"));
});

it("ignores unknown app-wide events without invoking the consumer", () => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const onEvent = vi.fn();
  const close = connectEvents(onEvent);
  const socket = FakeWebSocket.instances[0];

  socket.emit(JSON.stringify({
    type: "future_global",
    version: 1,
    sessionId: null,
    sequence: 1,
    payload: {},
    extra: true,
  }));
  socket.emit(JSON.stringify({
    type: "automation_run_started",
    version: 1,
    sessionId: "run-s1",
    sequence: 2,
    payload: { task_id: "t1" },
  }));

  expect(onEvent).toHaveBeenCalledOnce();
  expect(onEvent).toHaveBeenCalledWith({
    type: "automation_run_started",
    version: 1,
    sessionId: "run-s1",
    sequence: 2,
    payload: { task_id: "t1" },
  });
  close();
  expect(socket.close).toHaveBeenCalledOnce();
});

it("accepts a session-independent app event with a null sessionId", () => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  const onEvent = vi.fn();
  const close = connectEvents(onEvent);
  const socket = FakeWebSocket.instances[0];

  socket.emit(JSON.stringify({
    type: "automation_run_started",
    version: 1,
    sessionId: null,
    sequence: 1,
    payload: { task_id: "t1" },
  }));

  expect(onEvent).toHaveBeenCalledWith({
    type: "automation_run_started",
    version: 1,
    sessionId: null,
    sequence: 1,
    payload: { task_id: "t1" },
  });
  close();
});

it("reconnects a session, suppresses duplicate sequences, and delivers unseen out-of-order terminal events", () => {
  vi.useFakeTimers();
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const onEvent = vi.fn();
  const onOpen = vi.fn();
  const onClose = vi.fn();
  const session = new Session("s1", "/workspace", "cowork", { onEvent, onOpen, onClose });
  const first = FakeWebSocket.instances[0];
  first.open();

  first.emit(JSON.stringify({
    type: "ready",
    version: 1,
    sessionId: "s1",
    sequence: 1,
    payload: {},
  }));
  first.emit(JSON.stringify({
    type: "assistant_delta",
    version: 1,
    sessionId: "s1",
    sequence: 4,
    payload: { text: "partial" },
  }));
  const terminal = JSON.stringify({
    type: "assistant_message",
    version: 1,
    sessionId: "s1",
    sequence: 3,
    payload: { text: "complete", tool_calls: [] },
  });
  first.emit(terminal);
  first.emit(terminal);
  first.emit(JSON.stringify({
    type: "turn_done",
    version: 1,
    sessionId: "s1",
    sequence: 2,
    payload: {},
  }));

  expect(onEvent.mock.calls.map(([event]) => event.sequence)).toEqual([1, 4, 3, 2]);
  expect(warn).toHaveBeenCalledWith(expect.stringContaining("out-of-order sequence 3"));
  expect(warn).toHaveBeenCalledWith(expect.stringContaining("duplicate sequence 3"));

  first.disconnect();
  session.userMessage("queued while reconnecting");
  expect(onClose).toHaveBeenCalledOnce();
  vi.advanceTimersByTime(4999);
  expect(FakeWebSocket.instances).toHaveLength(1);
  vi.advanceTimersByTime(1);
  expect(FakeWebSocket.instances).toHaveLength(2);
  const second = FakeWebSocket.instances[1];
  second.open();
  expect(onOpen).toHaveBeenCalledTimes(2);
  expect(second.send).toHaveBeenCalledWith(JSON.stringify({
    type: "user_message",
    text: "queued while reconnecting",
  }));

  second.emit(terminal);
  expect(onEvent).toHaveBeenCalledTimes(4);
  session.close();
  vi.advanceTimersByTime(5000);
  expect(FakeWebSocket.instances).toHaveLength(2);
});

it("keeps app-wide sequence de-duplication across reconnects without dropping out-of-order events", () => {
  vi.useFakeTimers();
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const onEvent = vi.fn();
  const stop = connectEvents(onEvent);
  const first = FakeWebSocket.instances[0];
  const event = (sequence: number) => JSON.stringify({
    type: "automation_run_started",
    version: 1,
    sessionId: "run-s1",
    sequence,
    payload: { task_id: `t${sequence}` },
  });

  first.emit(event(2));
  first.emit(event(2));
  first.emit(event(1));
  expect(onEvent.mock.calls.map(([message]) => message.sequence)).toEqual([2, 1]);

  first.disconnect();
  vi.advanceTimersByTime(5000);
  const second = FakeWebSocket.instances[1];
  second.emit(event(2));
  second.emit(event(3));
  expect(onEvent.mock.calls.map(([message]) => message.sequence)).toEqual([2, 1, 3]);

  stop();
  vi.advanceTimersByTime(5000);
  expect(FakeWebSocket.instances).toHaveLength(2);
});
