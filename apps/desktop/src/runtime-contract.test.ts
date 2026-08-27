import { describe, expect, it } from "vitest";
import {
  parseApprovalDto,
  parseArtifactDto,
  parseMessageDto,
  parseModelDto,
  parseSessionDto,
  RuntimeContractError,
} from "./runtime-contract";

describe("core runtime DTO fixtures", () => {
  it("applies contract defaults and ignores additive fields", () => {
    expect(
      parseSessionDto({
        session_id: "s1",
        workspace: "C:/work",
        agent: "cowork",
        model: "custom:model",
        mode: "interactive",
        future: true,
      }),
    ).toEqual({
      session_id: "s1",
      workspace: "C:/work",
      agent: "cowork",
      model: "custom:model",
      mode: "interactive",
      updated_at: null,
      messages: 0,
      pinned: false,
      archived: false,
      reasoning_effort: "auto",
      attention: 0,
      liveness: "idle",
      subscriptions: [],
    });
    expect(parseMessageDto({ role: "assistant", content: "hi", future: 1 })).toEqual({
      role: "assistant",
      content: "hi",
    });
    expect(parseApprovalDto({ name: "write_file", future: 1 })).toEqual({
      name: "write_file",
      arguments: {},
      reason: "",
      category: "",
      standing_target: "",
    });
    expect(
      parseArtifactDto({
        path: "a.md",
        name: "a.md",
        kind: "markdown",
        size: 2,
        modified_at: 1,
        future: true,
      }),
    ).toEqual({ path: "a.md", name: "a.md", kind: "markdown", size: 2, modified_at: 1 });
    expect(parseModelDto({ id: "custom:model", provider: "custom", future: true })).toEqual({
      id: "custom:model",
      provider: "custom",
      available: true,
      custom_provider: false,
    });
  });

  it.each([
    [parseSessionDto, { workspace: "C:/work", agent: "cowork", model: "m", mode: "interactive" }],
    [parseMessageDto, { content: "missing role" }],
    [parseApprovalDto, { arguments: {} }],
    [parseArtifactDto, { name: "a", kind: "text", size: 1, modified_at: 1 }],
    [parseModelDto, { id: "m" }],
  ])("rejects a fixture with a missing required field", (parse, fixture) => {
    expect(() => parse(fixture)).toThrow(RuntimeContractError);
  });
});
