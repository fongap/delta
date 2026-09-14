import { beforeEach, describe, expect, it, vi } from "vitest";

const { invokeMock, listenMock } = vi.hoisted(() => ({
  invokeMock: vi.fn(),
  listenMock: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));
vi.mock("@tauri-apps/api/event", () => ({ listen: listenMock }));
vi.mock("./tauri", () => ({ isTauri: () => true }));

import {
  directApproval,
  directDirectoryResponse,
  directFollowUp,
  directPlanResponse,
  directQuestionResponse,
  directRun,
  directSteer,
} from "./runtimeTransport";

describe("direct runtime IPC contract", () => {
  beforeEach(() => {
    invokeMock.mockReset();
    listenMock.mockReset();
  });

  it("treats runtime_run as an asynchronous acceptance", async () => {
    invokeMock.mockResolvedValue({
      ok: true,
      accepted: true,
      runId: "run-1",
      state: "running",
    });

    await expect(
      directRun({
        sessionId: "session-1",
        modelId: "test-model",
        userInput: "hello",
        workspace: "C:/work",
        attachments: [{ kind: "text", name: "notes.txt", text: "facts" }],
        skill: "research",
        mode: "plan",
        onEvent: vi.fn(),
      }),
    ).resolves.toEqual({
      ok: true,
      accepted: true,
      runId: "run-1",
      state: "running",
    });
    expect(invokeMock).toHaveBeenCalledWith(
      "runtime_run",
      expect.objectContaining({ sessionId: "session-1", modelId: "test-model", userInput: "hello" }),
    );
    expect(invokeMock.mock.calls[0][1]).not.toHaveProperty("apiKey");
    expect(invokeMock.mock.calls[0][1]).not.toHaveProperty("baseUrl");
    expect(invokeMock.mock.calls[0][1]).not.toHaveProperty("protocol");
    expect(invokeMock.mock.calls[0][1]).not.toHaveProperty("tools");
    expect(invokeMock.mock.calls[0][1]).not.toHaveProperty("messages");
    expect(invokeMock.mock.calls[0][1]).toEqual(expect.objectContaining({
      workspace: "C:/work",
      attachments: [{ kind: "text", name: "notes.txt", text: "facts" }],
      skill: "research",
      mode: "plan",
    }));
  });

  it("keeps steering and queued follow-up as distinct commands", async () => {
    invokeMock
      .mockResolvedValueOnce({ ok: true, accepted: true })
      .mockResolvedValueOnce({ ok: true, accepted: true, runId: "run-next" });

    await expect(directSteer("session-1", "adjust")).resolves.toEqual({
      ok: true,
      accepted: true,
      runId: undefined,
    });
    await expect(directFollowUp("session-1", "continue later")).resolves.toEqual({
      ok: true,
      accepted: true,
      runId: "run-next",
    });
    expect(invokeMock.mock.calls.map(([command]) => command)).toEqual([
      "runtime_steer",
      "runtime_follow_up",
    ]);
  });

  it("resolves live approval through an explicit Rust command", async () => {
    invokeMock.mockResolvedValue({ ok: true, toolCallId: "call-1" });
    await expect(directApproval("session-1", "once")).resolves.toEqual({
      ok: true,
      toolCallId: "call-1",
    });
    expect(invokeMock).toHaveBeenCalledWith("runtime_approval", {
      sessionId: "session-1",
      decision: "once",
      toolCallId: undefined,
    });
  });

  it("uses explicit Rust commands for every attended interaction", async () => {
    invokeMock.mockResolvedValue({ ok: true });
    await directDirectoryResponse("session-1", true, "C:/work", true);
    await directPlanResponse("session-1", true, "interactive", undefined);
    await directQuestionResponse("session-1", "Use option A");
    expect(invokeMock.mock.calls.map(([command]) => command)).toEqual([
      "runtime_directory_response",
      "runtime_plan_response",
      "runtime_question_response",
    ]);
  });
});
