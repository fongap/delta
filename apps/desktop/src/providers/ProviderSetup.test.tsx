// Auth-method segmented choice + show_when field visibility (Bedrock's "Connect with"):
// only the selected method's fields render, and clicking a segment switches them.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ProviderCards, ProviderForm, CustomCreateForm, useProviderSetup, type ProviderSetupState } from "./ProviderSetup";
import { I18nProvider } from "@delta/i18n/I18nContext";
import type { ProviderInfo } from "../api";

vi.mock("../tauri", () => ({ openExternal: vi.fn() }));

// ProviderForm calls useI18n() — wrap every render in the provider (Sidebar.test.tsx pattern).
const wrap = (ui: React.ReactElement) => <I18nProvider locale="en-US">{ui}</I18nProvider>;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const BEDROCK: ProviderInfo = {
  name: "bedrock",
  title: "AWS Bedrock",
  needs_key: true,
  configured: false,
  values: {},
  suggested_models: [],
  recommended_model: null,
  fields: [
    { key: "region", label: "AWS region", secret: false, required: true, help: "", placeholder: "us-east-1" },
    {
      key: "auth_method",
      label: "Connect with",
      secret: false,
      required: false,
      help: "",
      placeholder: "",
      default: "api_key",
      choices: [
        { value: "api_key", label: "Bedrock API key" },
        { value: "profile", label: "AWS profile" },
        { value: "iam", label: "IAM keys" },
      ],
    },
    { key: "bedrock_api_key", label: "Bedrock API key", secret: true, required: false, help: "", placeholder: "ABSK…", show_when: { auth_method: "api_key" } },
    { key: "aws_profile", label: "AWS profile", secret: false, required: false, help: "", placeholder: "default", show_when: { auth_method: "profile" } },
    { key: "aws_secret_access_key", label: "Secret access key", secret: true, required: false, help: "", placeholder: "", show_when: { auth_method: "iam" } },
  ],
};

function makePs(fields: Record<string, string>, setFieldValue = vi.fn()): ProviderSetupState {
  return {
    providers: [BEDROCK],
    ordered: [BEDROCK],
    customProviders: [],
    orderedCustom: [],
    refreshProviders: async () => {},
    sel: "bedrock",
    info: BEDROCK,
    fields,
    setFieldValue,
    dirty: false,
    verify: { state: "idle" },
    showEndpoint: false,
    setShowEndpoint: () => {},
    keylessOk: new Set(),
    credentialed: false,
    savedState: false,
    secretFilled: true,
    openProvider: () => {},
    backToGallery: () => {},
    runTestAndSave: async () => true,
    removeKey: async () => {},
    removeCustom: async () => {},
    cancelBackTimer: () => {},
    statusFor: () => null,
    saveField: async () => {},
    fieldSaved: null,
    protocols: [],
    protocolsLoading: false,
    protocolsErr: null,
    protocolErrorMessage: null,
    creating: false,
    alias: "",
    setAlias: () => {},
    protoId: "openai-compatible",
    setProtoId: () => {},
    protoDef: undefined,
    openNewCustom: () => {},
    runCustomCreate: async () => false,
    fetchCustomModels: async () => {},
    fetching: false,
    fetchMsg: null,
    fetchedModels: [],
    pickFetchedDefault: async () => {},
  };
}

describe("ProviderForm auth-method choice", () => {
  it("renders only the selected method's fields", () => {
    render(wrap(<ProviderForm ps={makePs({ auth_method: "api_key" })} tp="t" />));
    expect(screen.getByTestId("t-field-bedrock_api_key")).toBeTruthy();
    expect(screen.queryByTestId("t-field-aws_profile")).toBeNull();
    expect(screen.queryByTestId("t-field-aws_secret_access_key")).toBeNull();
    expect(screen.getByTestId("t-choice-auth_method-api_key").getAttribute("aria-checked")).toBe("true");
  });

  it("switching the segment swaps the visible fields", () => {
    const setFieldValue = vi.fn();
    const { rerender } = render(
      wrap(<ProviderForm ps={makePs({ auth_method: "api_key" }, setFieldValue)} tp="t" />),
    );
    fireEvent.click(screen.getByTestId("t-choice-auth_method-profile"));
    expect(setFieldValue).toHaveBeenCalledWith("auth_method", "profile");
    rerender(wrap(<ProviderForm ps={makePs({ auth_method: "profile" }, setFieldValue)} tp="t" />));
    expect(screen.getByTestId("t-field-aws_profile")).toBeTruthy();
    expect(screen.queryByTestId("t-field-bedrock_api_key")).toBeNull();
  });

  it("iam segment shows the key-pair fields", () => {
    render(wrap(<ProviderForm ps={makePs({ auth_method: "iam" })} tp="t" />));
    expect(screen.getByTestId("t-field-aws_secret_access_key")).toBeTruthy();
    expect(screen.queryByTestId("t-field-bedrock_api_key")).toBeNull();
  });
});

describe("CustomCreateForm header shows the alias", () => {
  it("renders the generic title until an alias is typed", () => {
    render(wrap(<CustomCreateForm ps={makePs({})} tp="t" inline />));
    expect(screen.getByTestId("t-custom-title").textContent).toContain("Add provider");
  });

  it("renders the live alias name at the top when set", () => {
    render(wrap(<CustomCreateForm ps={{ ...makePs({}), alias: "my-gateway" }} tp="t" inline />));
    expect(screen.getByTestId("t-custom-title").textContent).toContain("my-gateway");
  });
});

describe("custom provider identity", () => {
  it("uses the alias as the card title and the protocol as secondary information", () => {
    const custom: ProviderInfo = {
      ...BEDROCK,
      name: "fong",
      alias: "fong",
      title: "fong",
      custom: true,
      protocol: "openai-compatible",
      blurb: "OpenAI compatible",
      configured: true,
    };
    const ps = {
      ...makePs({}),
      providers: [custom],
      ordered: [custom],
      customProviders: [custom],
      orderedCustom: [custom],
      info: custom,
      sel: null,
    };
    render(wrap(<ProviderCards ps={ps} tp="t" customOnly hideAdd />));
    const card = screen.getByTestId("t-provider-fong");
    expect(card.textContent).toContain("fong");
    expect(card.textContent).toContain("OpenAI compatible");
    expect(card.textContent).not.toContain("AWS Bedrock");
  });

  it("keeps fetched models visible after a successful fetch in create mode", async () => {
    let savedProvider: ProviderInfo | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      const json = (value: unknown) => ({ json: async () => value });
      if (url.endsWith("/v1/protocols")) {
        return json([{
          id: "openai-compatible",
          title: "OpenAI compatible",
          needs_key: false,
          recommended_model: null,
          fields: [
            { key: "api_key", label: "API key (optional)", secret: true, required: false, help: "", placeholder: "sk-…" },
            { key: "base_url", label: "Server address", secret: false, required: true, help: "", placeholder: "https://…/v1" },
          ],
        }]);
      }
      if (url.endsWith("/v1/providers/fetch")) {
        return json({ ok: true, models: ["code-max", "code-mini"], added: ["code-max", "code-mini"] });
      }
      if (url.endsWith("/v1/providers/verify")) return json({ ok: true });
      if (url.endsWith("/v1/providers") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        savedProvider = {
          ...BEDROCK,
          name: body.name,
          alias: body.name,
          title: body.name,
          custom: true,
          protocol: body.protocol,
          blurb: "OpenAI compatible",
          needs_key: false,
          configured: true,
          fields: [],
        };
        return json({ ok: true, provider: body.name, protocol: body.protocol });
      }
      if (url.endsWith("/v1/providers")) return json(savedProvider ? [savedProvider] : []);
      throw new Error(`unexpected request: ${url}`);
    }));

    function Harness() {
      const ps = useProviderSetup();
      return <CustomCreateForm ps={ps} tp="set" inline />;
    }
    render(wrap(<Harness />));
    const alias = await screen.findByTestId("set-alias");
    fireEvent.change(alias, { target: { value: "fong" } });
    fireEvent.click(screen.getByTestId("set-fetch"));
    // Success: the result + model chips stay visible — the form does NOT reset and wipe
    // them the instant they were set. The alias stays so the user can pick a default
    // model, then close the form explicitly when done.
    await screen.findByText(/Fetched 2 model/);
    expect((screen.getByTestId("set-alias") as HTMLInputElement).value).toBe("fong");
    const chips = await screen.findByTestId("fetched-models");
    expect(chips.textContent).toContain("code-max");
    expect(chips.textContent).toContain("code-mini");
  });

  it("shows an error message and clears models on a failed fetch", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      const json = (value: unknown) => ({ json: async () => value });
      if (url.endsWith("/v1/protocols")) {
        return json([{ id: "openai-compatible", title: "OpenAI compatible", needs_key: false, recommended_model: null, fields: [{ key: "api_key", label: "API key", secret: true, required: false, help: "", placeholder: "" }, { key: "base_url", label: "Server address", secret: false, required: true, help: "", placeholder: "" }] }]);
      }
      if (url.endsWith("/v1/providers") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return json({ ok: true, provider: body.name, protocol: body.protocol });
      }
      if (url.endsWith("/v1/providers/fetch")) return json({ ok: false, error: "Invalid API key." });
      if (url.endsWith("/v1/providers")) return json([]);
      throw new Error(`unexpected request: ${url}`);
    }));

    function Harness() {
      const ps = useProviderSetup();
      return <CustomCreateForm ps={ps} tp="set" inline />;
    }
    render(wrap(<Harness />));
    fireEvent.change(await screen.findByTestId("set-alias"), { target: { value: "fong" } });
    fireEvent.click(screen.getByTestId("set-fetch"));
    // A failed fetch surfaces a stable error line and never renders model chips.
    expect(await screen.findByTestId("set-fetch-msg")).toBeTruthy();
    expect(screen.queryByTestId("fetched-models")).toBeNull();
  });

  it("reports an up-to-date list and no chips when fetch returns no models", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      const json = (value: unknown) => ({ json: async () => value });
      if (url.endsWith("/v1/protocols")) {
        return json([{ id: "openai-compatible", title: "OpenAI compatible", needs_key: false, recommended_model: null, fields: [{ key: "api_key", label: "API key", secret: true, required: false, help: "", placeholder: "" }, { key: "base_url", label: "Server address", secret: false, required: true, help: "", placeholder: "" }] }]);
      }
      if (url.endsWith("/v1/providers") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return json({ ok: true, provider: body.name, protocol: body.protocol });
      }
      if (url.endsWith("/v1/providers/fetch")) return json({ ok: true, models: [], added: [] });
      if (url.endsWith("/v1/providers")) return json([]);
      throw new Error(`unexpected request: ${url}`);
    }));

    function Harness() {
      const ps = useProviderSetup();
      return <CustomCreateForm ps={ps} tp="set" inline />;
    }
    render(wrap(<Harness />));
    fireEvent.change(await screen.findByTestId("set-alias"), { target: { value: "fong" } });
    fireEvent.click(screen.getByTestId("set-fetch"));
    const emptyMsg = await screen.findByTestId("set-fetch-msg");
    expect(emptyMsg.textContent).toContain("up to date");
    expect(screen.queryByTestId("fetched-models")).toBeNull();
  });
});
