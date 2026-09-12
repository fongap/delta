import { useEffect, useState } from "react";
import {
  deletePersona,
  getPersonas,
  getSessions,
  updatePersona,
  type Persona,
} from "../api";
import type { SessionInfo } from "../types";
import { Icon } from "./Icon";
import { useI18n } from "@delta/i18n/I18nContext";
import { fullPersonaName } from "../personaScope";

// Personas: Delta is the single registered persona. This panel manages its enabled / surfaced
// / default lifecycle; with only Delta there is little to toggle, but the surface stays (R6.0
// did not redo the UI). Third-party persona install was removed with the persona platform.
const CARD = "rounded-xl2 border border-line bg-panel";
const CHECK = "flex items-center gap-1.5 text-[12.5px] text-muted select-none shrink-0";
const BTN_BORDERED =
  "text-[12.5px] px-2.5 py-1.5 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0 disabled:opacity-40 disabled:hover:border-line";

export function PersonasTab({ onOpenPersona }: { onOpenPersona?: (id: string) => void }) {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  // Disabling archives the persona's conversations (server-side), so when there are any we
  // arm an inline confirm (same two-step idiom as delete) instead of flipping immediately.
  const [confirmOff, setConfirmOff] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const { t } = useI18n();

  const reload = () => getPersonas().then(setPersonas).catch(() => {});
  const reloadSessions = () => getSessions().then(setSessions).catch(() => {});
  useEffect(() => {
    reload();
    reloadSessions();
  }, []);

  // Real conversations the disable would archive (unarchived; run sessions are server-hidden).
  const liveCount = (id: string) =>
    sessions.filter((s) => s.agent === id && !s.archived).length;

  const toggle = async (
    id: string,
    body: { enabled?: boolean; surfaced?: boolean; default?: boolean },
  ) => {
    const r = await updatePersona(id, body);
    if (r.personas) setPersonas(r.personas);
    else reload();
    if (body.enabled === false) reloadSessions(); // counts just changed
  };

  const requestDisable = (p: Persona) => {
    if (liveCount(p.id) > 0) setConfirmOff(p.id);
    else toggle(p.id, { enabled: false });
  };

  const remove = async (id: string) => {
    setConfirmDel(null);
    const r = await deletePersona(id);
    if (!r.ok) {
      setMsg(r.error || t("personas.errorDelete", undefined, "delete failed"));
      return;
    }
    if (r.personas) setPersonas(r.personas);
    else reload();
  };

  return (
    <div>
      <p className="text-[12.5px] text-muted mb-3 leading-relaxed">
        {t(
          "personas.intro",
          undefined,
          "Enable a Delta agent, then choose whether it appears in the new-session picker. The starred persona is the default for new sessions.",
        )}
      </p>

      <div className={CARD + " divide-y divide-line mb-6"}>
        {personas.map((p) => (
          <div key={p.id} className="px-4 py-3">
            <div className="flex items-center gap-4">
            <div className="min-w-0 flex-1">
              <div className="text-[13.5px] font-medium flex items-center gap-1.5">
                <span className="truncate">{fullPersonaName(p.name, p.id)}</span>
                {p.default && (
                  <span
                    className="text-accent"
                    title={t("personas.defaultStarTitle", undefined, "Default for new sessions")}
                  >
                    ★
                  </span>
                )}
                {p.builtin && (
                  <span className="text-[11px] text-faint font-normal">
                    {t("personas.builtin", undefined, " · built-in")}
                  </span>
                )}
              </div>
              <div className="text-[12px] text-muted truncate">{p.tagline}</div>
            </div>
            <label className={CHECK}>
              <input
                type="checkbox"
                checked={p.enabled}
                onChange={(e) =>
                  e.target.checked ? toggle(p.id, { enabled: true }) : requestDisable(p)
                }
              />
              {t("common.enabled", undefined, "Enabled")}
            </label>
            <label className={CHECK + (p.enabled ? "" : " opacity-40")}>
              <input
                type="checkbox"
                checked={p.surfaced}
                disabled={!p.enabled}
                onChange={(e) => toggle(p.id, { surfaced: e.target.checked })}
              />
              {t("personas.inPicker", undefined, "In picker")}
            </label>
            <button
              className={BTN_BORDERED}
              disabled={p.default || !p.enabled}
              onClick={() => toggle(p.id, { default: true })}
            >
              {t("personas.setDefault", undefined, "Set default")}
            </button>
            {onOpenPersona && (
              <button
                className="text-faint hover:text-ink shrink-0 p-1"
                title={t("personas.configureWithName", { name: fullPersonaName(p.name, p.id) }, "Configure {name}")}
                aria-label={t("personas.configureWithName", { name: fullPersonaName(p.name, p.id) }, "Configure {name}")}
                data-testid={`persona-configure-${p.id}`}
                onClick={() => onOpenPersona(p.id)}
              >
                <Icon name="sliders" size={15} />
              </button>
            )}
            {!p.builtin &&
              (confirmDel === p.id ? (
                <span className="flex items-center gap-1.5 shrink-0">
                  <button
                    className="text-[12px] px-2 py-1.5 rounded-lg bg-danger text-onAccent"
                    data-testid={`persona-delete-confirm-${p.id}`}
                    onClick={() => remove(p.id)}
                  >
                    {t("common.delete", undefined, "Delete")}
                  </button>
                  <button className={BTN_BORDERED} onClick={() => setConfirmDel(null)}>
                    {t("common.keep", undefined, "Keep")}
                  </button>
                </span>
              ) : (
                <button
                  className="text-faint hover:text-danger shrink-0 p-1"
                  title={t("personas.deleteTitle", undefined, "Delete this persona")}
                  aria-label={t("personas.deleteWithName", { name: fullPersonaName(p.name, p.id) }, "Delete {name}")}
                  data-testid={`persona-delete-${p.id}`}
                  onClick={() => setConfirmDel(p.id)}
                >
                  <Icon name="trash" size={14} />
                </button>
              ))}
            </div>
            {confirmOff === p.id && (
              <div
                className="mt-2 flex items-center gap-2.5 text-[12px] text-muted"
                data-testid={`persona-disable-warning-${p.id}`}
              >
                <span className="min-w-0">
                  {t(
                    "personas.disableWarning",
                    {
                      n: liveCount(p.id),
                      s: liveCount(p.id) === 1 ? "" : "s",
                    },
                    "Disabling archives its {n} conversation{s} — they stay available under “Show archived”.",
                  )}
                </span>
                <button
                  className="text-[12px] px-2.5 py-1.5 rounded-lg bg-accent text-onAccent shrink-0"
                  data-testid={`persona-disable-confirm-${p.id}`}
                  onClick={() => {
                    setConfirmOff(null);
                    toggle(p.id, { enabled: false });
                  }}
                >
                  {t("personas.disable", undefined, "Disable")}
                </button>
                <button className={BTN_BORDERED} onClick={() => setConfirmOff(null)}>
                  {t("personas.keepEnabled", undefined, "Keep enabled")}
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      {msg && <div className="text-[12.5px] text-muted mt-2.5">{msg}</div>}
    </div>
  );
}
