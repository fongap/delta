import { useState } from "react";
import { addModel, getSettings, removeModel, setDefaultModel } from "../api";
import { useI18n } from "@delta/i18n/I18nContext";

// One provider's models as a compact list. Two ORTHOGONAL states per row:
//   checkbox = shown in the composer's model picker (display)
//   radio    = the model new sessions start with (default; exactly one)
// The default model can never be unchecked — the backend rejects it and the row
// explains why (spec: 默认模型不能处于隐藏状态). Manual adds live behind a collapsed
// "＋ 手动添加模型" row so discovery-first flow stays clean. Shared by Onboarding
// and Settings ▸ Models.
export function ModelChecklist({
  provider,
  knownProviders,
  suggested,
  curated,
  defaultModel,
  labels,
  onChanged,
  onRefresh,
  refreshing,
}: {
  provider: string; // decides the id prefix; OpenAI models stay bare
  knownProviders: string[]; // all provider names, to parse prefixes in curated ids
  suggested: string[]; // bare model names suggested by the provider
  curated: string[]; // the full curated list (all providers, full ids)
  defaultModel: string;
  labels?: Record<string, string>; // curated display names (full id → label); raw id when absent
  onChanged: (next: { models: string[]; model: string }) => void;
  onRefresh?: () => void; // re-pull /models from the provider (custom providers)
  refreshing?: boolean;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [hideBlocked, setHideBlocked] = useState(false);

  const provOf = (id: string) => {
    const i = id.indexOf(":");
    return i > 0 && knownProviders.includes(id.slice(0, i)) ? id.slice(0, i) : "openai";
  };
  const prefixed = (m: string) => (provider === "openai" || provOf(m) !== "openai" ? m : `${provider}:${m}`);
  const bare = (id: string) => (id.startsWith(`${provider}:`) ? id.slice(provider.length + 1) : id);

  const rows = [
    ...suggested.map(prefixed),
    ...curated.filter((id) => provOf(id) === provider),
  ].filter((id, i, a) => a.indexOf(id) === i);

  const checked = (id: string) => curated.includes(id);
  const refresh = async () => {
    const s = await getSettings();
    onChanged({ models: s.models, model: s.model });
  };

  const tick = async (id: string, on: boolean) => {
    if (!on && id === defaultModel) {
      // The default can never be hidden (data-layer invariant: default ∈ enabled).
      // Say so instead of silently failing or greying out without explanation.
      setHideBlocked(true);
      window.setTimeout(() => setHideBlocked(false), 2600);
      return;
    }
    setHideBlocked(false);
    const res = on ? await addModel(id) : await removeModel(id);
    if (res.ok) onChanged({ models: res.models, model: res.model });
  };
  const makeDefault = async (id: string) => {
    if (!checked(id)) await addModel(id); // defaulting an unticked row ticks it too
    await setDefaultModel(id);
    await refresh();
  };
  const add = async () => {
    let typed = draft.trim();
    if (!typed) return;
    const res = await addModel(prefixed(typed));
    if (res.ok) {
      setDraft("");
      setAddOpen(false);
      onChanged({ models: res.models, model: res.model });
    }
  };

  const empty = rows.length === 0;
  return (
    <div className="mlist">
      {empty && (
        <div className="mlist-empty" data-testid="mlist-empty">
          <div className="text-[13px] font-medium text-ink">
            {t("models.emptyTitle", undefined, "No models yet")}
          </div>
          <div className="text-[12px] text-muted mt-0.5">
            {t("models.emptySub", undefined, "Fetch the model list from this provider.")}
          </div>
          {onRefresh && (
            <button className="btn-secondary sm mt-2.5" onClick={onRefresh} disabled={refreshing}>
              {refreshing ? "…" : t("models.fetchModels", undefined, "Fetch models")}
            </button>
          )}
        </div>
      )}
      {!empty && (
        <>
          {hideBlocked && (
            <div className="mlist-note" role="status" data-testid="mlist-hide-blocked">
              {t("models.defaultCannotHide", undefined, "Pick a new default model before hiding this one.")}
            </div>
          )}
          {/* One shared column header (使用 | 模型 | 默认) instead of repeating the
              "默认" label on every row; aria-hidden — the inputs carry real labels. */}
          <div className="mlist-head" aria-hidden="true">
            <span>{t("models.colUse", undefined, "Use")}</span>
            <span>{t("models.colModel", undefined, "Model")}</span>
            <span>{t("models.defaultLabel", undefined, "Default")}</span>
          </div>
          {rows.map((id) => {
            const isDefault = id === defaultModel;
            return (
              <div className={"mlist-row" + (checked(id) ? "" : " off")} key={id}>
                <label className="mlist-main">
                  <input
                    type="checkbox"
                    checked={checked(id)}
                    onChange={(e) => tick(id, e.target.checked)}
                    aria-label={t("models.showAria", { m: bare(id) }, `Show ${bare(id)} in the picker`)}
                  />
                  <span className="mlist-name" title={id}>
                    {labels?.[id] || bare(id)}
                  </span>
                </label>
                <label className="mlist-default-radio" title={t("models.defaultAria", undefined, "Use as the default model")}>
                  <input
                    type="radio"
                    name="mlist-default"
                    checked={isDefault}
                    onChange={() => makeDefault(id)}
                    aria-label={t("models.defaultAria", undefined, "Use as the default model")}
                  />
                </label>
              </div>
            );
          })}
        </>
      )}
      <div className="mlist-add">
        {addOpen ? (
          <div className="mlist-add-form" data-testid="mlist-add-form">
            <input
              placeholder={t("models.modelIdLabel", undefined, "Model ID")}
              value={draft}
              spellCheck={false}
              autoComplete="off"
              autoFocus
              data-testid="mlist-add-input"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void add();
                if (e.key === "Escape") setAddOpen(false);
              }}
            />
            <div className="flex gap-2 justify-end">
              <button className="btn-secondary sm" onClick={() => setAddOpen(false)}>
                {t("common.cancel", undefined, "Cancel")}
              </button>
              <button className="btn-primary sm" onClick={add} disabled={!draft.trim()}>
                {t("common.add", undefined, "Add")}
              </button>
            </div>
          </div>
        ) : (
          <button
            className="mlist-add-toggle"
            onClick={() => setAddOpen(true)}
            data-testid="mlist-add-toggle"
          >
            ＋ {t("models.manualAdd", undefined, "Add a model manually")}
          </button>
        )}
      </div>
    </div>
  );
}
