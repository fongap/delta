// Add-model family dropdown for the cloud-account providers: the family choice folds
// into the model id (`bedrock:claude/…`, `vertex:openweight/…`); plain providers keep
// the bare add-model row.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ModelChecklist } from "./ModelChecklist";
import { I18nProvider } from "@delta/i18n/I18nContext";

vi.mock("../api", () => ({
  addModel: vi.fn(async (id: string) => ({ ok: true, models: [id], model: id })),
  removeModel: vi.fn(async () => ({ ok: true, models: [], model: "" })),
  setDefaultModel: vi.fn(async () => ({ ok: true })),
  getSettings: vi.fn(async () => ({ models: [], model: "" })),
}));

import { addModel } from "../api";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const KNOWN = ["openai", "anthropic", "bedrock", "vertex", "openrouter"];

function renderList(provider: string) {
  return render(
    <I18nProvider locale="en-US">
      <ModelChecklist
        provider={provider}
        knownProviders={KNOWN}
        suggested={[]}
        curated={[]}
        defaultModel=""
        onChanged={() => {}}
      />
    </I18nProvider>,
  );
}

async function addTyped(id: string) {
  // The manual-add row is collapsed by default (spec: 手动添加模型改为折叠式操作) —
  // open it, then type into the revealed Model ID field. Add() collapses the form
  // again asynchronously, so wait for the toggle to come back between adds.
  fireEvent.click(screen.getByTestId("mlist-add-toggle"));
  fireEvent.change(screen.getByPlaceholderText("Model ID"), {
    target: { value: id },
  });
  fireEvent.click(screen.getByText("Add"));
  await waitFor(() =>
    expect(screen.queryByTestId("mlist-add-form")).toBeNull(),
  );
}

describe("ModelChecklist add-model family dropdown", () => {
  it("folds the selected vertex family into the id", async () => {
    renderList("vertex");
    fireEvent.click(screen.getByTestId("mlist-add-toggle"));
    fireEvent.change(screen.getByTestId("mlist-family"), {
      target: { value: "openweight" },
    });
    fireEvent.change(screen.getByPlaceholderText("Model ID"), {
      target: { value: "meta/llama-4-maverick-17b-128e-instruct-maas" },
    });
    fireEvent.click(screen.getByText("Add"));
    await waitFor(() =>
      expect(addModel).toHaveBeenCalledWith(
        "vertex:openweight/meta/llama-4-maverick-17b-128e-instruct-maas",
      ),
    );
  });

  it("defaults bedrock to the Claude family", async () => {
    renderList("bedrock");
    await addTyped("anthropic.claude-sonnet-4-6-v1:0");
    expect(addModel).toHaveBeenCalledWith(
      "bedrock:claude/anthropic.claude-sonnet-4-6-v1:0",
    );
  });

  it("keeps a typed family verbatim", async () => {
    renderList("bedrock");
    await addTyped("other/amazon.nova-2-pro-v1:0");
    expect(addModel).toHaveBeenLastCalledWith("bedrock:other/amazon.nova-2-pro-v1:0");
  });

  it("shows no family dropdown for plain providers", async () => {
    renderList("openrouter");
    expect(screen.queryByTestId("mlist-family")).toBeNull();
    addTyped("z-ai/glm-5.2");
    expect(addModel).toHaveBeenCalledWith("openrouter:z-ai/glm-5.2");
  });
});
