import { useState } from "react";
import {
  disconnectGmailAccount,
  setGmailDefaultAccount,
  setGmailFilters,
  type GmailAccount,
} from "../../../api";
import { ConnectorBadge } from "../ConnectorIcon";
import type { DetailProps } from "./ConnectorsSection";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { FOOT, GRP, GRP_H, PILL_ACCENT, ROW, TAG_ACCENT, TAG_WARN, XBTN } from "./ui";
import { useI18n } from "@delta/i18n/I18nContext";

// The Gmail detail page (UX-DECISIONS §21): connected mailboxes (multi-account,
// Default badge, per-account disconnect) + "Never show agents" privacy filters.
// Adding an account launches manual connect (managed OAuth removed, ADR-004).

const LABEL = "text-[12.5px] text-muted w-24 shrink-0";

export function GmailDetail({ c, onChanged }: DetailProps) {
  const { t } = useI18n();
  const [_showManual, setShowManual] = useState(false);
  const accounts = (c.accounts ?? []) as GmailAccount[]; // email-keyed (pre-generic-layer shape)

  return (
    <div data-testid="gmail-detail">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title={t("connectors.brand.gmail")} />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">{t("connectors.brand.gmail")}</h2>
          <div className="text-[12.5px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span className="w-2 h-2 rounded-full bg-ok" />
                <span data-testid="gmail-status">
                  {t("connectors.accountCount", { n: accounts.length })}
                </span>
              </>
            ) : (
              <span>{t("connectors.notConnected")}</span>
            )}
          </div>
        </div>
<button
          className={PILL_ACCENT + (c.managed_paused ? " opacity-50" : "")}
          data-testid="add-account-btn"
          onClick={() => setShowManual((v) => !v)}
          disabled={c.managed_paused}
        >
          {c.managed_paused
            ? t("connectors.addAccountComingSoon")
            : t("connectors.addAccount")}
        </button>
      </div>

      {!c.connected && (
        <div className={GRP}>
          <div className={ROW + " text-[12.5px] text-muted"}>
            {t("connectors.gmailNotConnectedBlurb")}
          </div>
        </div>
      )}

      {accounts.length > 0 && (
        <>
          <div className={GRP_H + " !mt-0"}>{t("connectors.accounts")}</div>
          <div className={GRP} data-testid="gmail-accounts">
            {accounts.map((a) => (
              <AccountRow key={a.email} a={a} onChanged={onChanged} />
            ))}
          </div>
        </>
      )}

      <FiltersGroup c={c} onChanged={onChanged} />

      <ToolsDisclosure c={c} onChanged={onChanged} />
      <div className={FOOT + " mt-2"}>{t("connectors.gmailFootnote")}</div>
    </div>
  );
}

function AccountRow({ a, onChanged }: { a: GmailAccount; onChanged: () => void }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  return (
    <div className={ROW} data-testid={`gmail-account-${a.email}`}>
      <span className="min-w-0 flex-1 flex items-center gap-2">
        <span className="text-[13px] font-medium truncate">{a.email}</span>
        {a.default && <span className={TAG_ACCENT}>{t("common.default")}</span>}
        {a.needs_reauth && <span className={TAG_WARN}>{t("connectors.signInAgain")}</span>}
      </span>
      {!a.default && (
        <button
          className="text-[12px] text-muted hover:text-ink shrink-0"
          data-testid={`gmail-make-default-${a.email}`}
          onClick={async () => {
            await setGmailDefaultAccount(a.email);
            onChanged();
          }}
        >
          {t("connectors.makeDefault")}
        </button>
      )}
      <button
        className={XBTN}
        title={t("connectors.disconnectMailbox")}
        data-testid={`gmail-disconnect-${a.email}`}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          await disconnectGmailAccount(a.email);
          setBusy(false);
          onChanged();
        }}
      >
        ×
      </button>
    </div>
  );
}

function FiltersGroup({ c, onChanged }: Pick<DetailProps, "c" | "onChanged">) {
  const { t } = useI18n();
  const filters = c.filters ?? { senders: [], labels: [] };
  return (
    <>
      <div className={GRP_H}>{t("connectors.neverShowAgents")}</div>
      <div className={GRP} data-testid="gmail-filters">
        <ChipListRow
          label={t("connectors.senders")}
          testid="gmail-filter-senders"
          placeholder={t("connectors.sendersPlaceholder")}
          values={filters.senders}
          onSave={async (senders) => {
            await setGmailFilters({ senders });
            onChanged();
          }}
        />
        <ChipListRow
          label={t("connectors.labels")}
          testid="gmail-filter-labels"
          placeholder={t("connectors.labelsPlaceholder")}
          values={filters.labels}
          onSave={async (labels) => {
            await setGmailFilters({ labels });
            onChanged();
          }}
        />
      </div>
      <div className={FOOT}>{t("connectors.filterFootnote")}</div>
    </>
  );
}

function ChipListRow({
  label,
  testid,
  placeholder,
  values,
  onSave,
}: {
  label: string;
  testid: string;
  placeholder: string;
  values: string[];
  onSave: (next: string[]) => Promise<void>;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState("");
  const add = async () => {
    const v = draft.trim();
    if (!v) return;
    setDraft("");
    await onSave([...values, v]);
  };
  return (
    <div className={ROW} data-testid={testid}>
      <span className={LABEL}>{label}</span>
      <span className="min-w-0 flex-1 flex flex-wrap items-center gap-1.5">
        {values.map((v) => (
          <span
            key={v}
            className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-paper border border-line text-[12.5px]"
          >
            {v}
            <button
              className={XBTN}
              title={t("common.remove")}
              onClick={() => onSave(values.filter((x) => x !== v))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          className="flex-1 min-w-[140px] bg-transparent text-[12.5px] outline-none placeholder:text-faint"
          placeholder={placeholder}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") add();
          }}
          onBlur={() => draft.trim() && add()}
        />
      </span>
    </div>
  );
}
