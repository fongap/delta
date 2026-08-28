import { useEffect, useState } from "react";
import {
  cloudLogin,
  connectManaged,
  getCloudStatus,
  getConnectors,
  setOnboarded,
  type CloudStatus,
  type Connector,
} from "../api";
import { ConnectorBadge } from "../features/connectors/ConnectorIcon";
import { CustomCreateForm, ProviderCards, ProviderForm, useProviderSetup } from "../providers/ProviderSetup";
import { Spinner } from "./AutomationQuickstart";
import { useI18n } from "../i18n/I18nContext";

// First-run onboarding (UX-DECISIONS §24 → §29 → §39): model → your tools → go.
// §39 (owner design, 2026-07-18): step 1 is a PROVIDER GALLERY — 13 real brand
// marks, two per row, each card wearing its own state — and step 2 is a
// two-state tools page whose post-sign-in body is a mini connector gallery with
// live one-click connects. Both steps share one frame rule: the header and
// footer never move; only the middle region swaps, at a fixed height.
// The gallery/form themselves live in providers/ProviderSetup.tsx, shared with
// Settings ▸ Models (UX-021) so the two surfaces can't drift.
// Replayable from Settings ▸ General ▸ "Run setup again".

// Step 2's benefit rows (§41): managed connectors with LIVE prod OAuth apps only,
// each framed by the job it does (detail copy stays ONE line even with a Connect
// pill — wrap made rows jump between states). gmail + google_calendar ship as one
// combined grayed "Coming soon" row — both ride the same Google app, gated on
// Google verification/CASA; give them rows when it lands.
const TOOL_ROWS = [
  { name: "outlook", benefitKey: "onboarding.toolRows.outlook.benefit", detailKey: "onboarding.toolRows.outlook.detail" },
  { name: "slack", benefitKey: "onboarding.toolRows.slack.benefit", detailKey: "onboarding.toolRows.slack.detail" },
  { name: "github", benefitKey: "onboarding.toolRows.github.benefit", detailKey: "onboarding.toolRows.github.detail" },
  { name: "notion", benefitKey: "onboarding.toolRows.notion.benefit", detailKey: "onboarding.toolRows.notion.detail" },
  { name: "hubspot", benefitKey: "onboarding.toolRows.hubspot.benefit", detailKey: "onboarding.toolRows.hubspot.detail" },
  { name: "attio", benefitKey: "onboarding.toolRows.attio.benefit", detailKey: "onboarding.toolRows.attio.detail" },
];
const TOOLS_SOON = ["gmail", "google_calendar"];

export function Onboarding({ onDone }: { onDone: (next?: "work" | "gallery" | "automations") => void }) {
  const [step, setStep] = useState(0);
  const { t } = useI18n();

  // -- step 1: model (provider gallery ⇄ key form, shared machinery) ---------------
  const ps = useProviderSetup();
  const [skipConfirm, setSkipConfirm] = useState(false);

  const anyReady =
    ps.customProviders.some((p) => p.configured && p.needs_key) || ps.keylessOk.size > 0;
  // In the form with typed-but-untested input, Next verifies+saves first (tester
  // catch 2026-07-12: a manual Test-then-Continue two-step reads as a puzzle).
  const nextFromForm = !!ps.sel && ps.dirty && ps.secretFilled;
  const canNext = anyReady || nextFromForm;

  const advance = async () => {
    if (nextFromForm && !ps.credentialed) {
      ps.cancelBackTimer();
      if (!(await ps.runTestAndSave())) return;
    }
    setStep(1);
  };

  // -- step 2: connect your everyday tools (§39 two-state page) -------------------
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [cloud, setCloud] = useState<CloudStatus | null>(null);
  const [signinPhase, setSigninPhase] = useState<"opening" | "waiting" | null>(null);
  // One in-flight connect at a time; clicking another card quietly resets the first.
  const [pendingTool, setPendingTool] = useState<string | null>(null);

  // Poll while on the tools page: sign-in AND vendor consents land out-of-band in
  // the system browser. Tighten while either is actually in flight.
  useEffect(() => {
    if (step !== 1) return;
    const load = () => {
      getConnectors().then(setConnectors).catch(() => {});
      getCloudStatus().then(setCloud).catch(() => {});
    };
    load();
    const fast = signinPhase === "waiting" || pendingTool !== null;
    const timer = setInterval(load, fast ? 750 : 3000);
    return () => clearInterval(timer);
  }, [step, signinPhase, pendingTool]);

  // The poll flips the card to ✓ when the consent lands.
  useEffect(() => {
    if (pendingTool && connectors.find((c) => c.name === pendingTool)?.connected)
      setPendingTool(null);
  }, [connectors, pendingTool]);

  const startTool = async (name: string) => {
    setPendingTool(name); // replaces any previous pending connect
    const res = await connectManaged(
      name,
      name === "hubspot" ? { access: "read" } : undefined, // least privilege in onboarding
    ).catch(() => ({ ok: false }));
    if (!res.ok) setPendingTool((cur) => (cur === name ? null : cur)); // silent reset — no error walls here
  };

  const finish = async (next?: "work" | "gallery" | "automations") => {
    await setOnboarded(true).catch(() => {});
    onDone(next);
  };

  // -- shared bits ----------------------------------------------------------------
  const dots = (
    <div className="flex justify-center gap-2 mb-6">
      {[0, 1, 2].map((i) => (
        <span key={i} className={"w-1.5 h-1.5 rounded-full " + (i <= step ? "bg-accent" : "bg-line")} />
      ))}
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 bg-ink/30 grid place-items-center" data-testid="onboarding">
      {/* FIXED height across all three steps (owner call 2026-07-12, reaffirmed §39: the
          modal must never resize — the gallery⇄form swap happens inside this box). */}
      <div className="w-[600px] max-w-[92vw] h-[560px] max-h-[88vh] rounded-2xl border border-line bg-panel shadow-2xl p-8 flex flex-col">
        {dots}

        {step === 0 && (
          <section data-testid="ob-step-model" className="flex-1 min-h-0 flex flex-col">
            {/* Persistent header — stays put while the region below swaps (§39). */}
            <h1 className="text-[19px] font-semibold">{t("onboarding.welcomeTitle")}</h1>
            <p className="text-[13px] text-muted mt-0.5 mb-4">
              {t("onboarding.pickProvider")}
            </p>

            {!ps.sel && !ps.creating ? (
              /* ---- the provider GALLERY: inline custom-provider create card + custom-only cards ---- */
              <div className="flex-1 min-h-0 overflow-y-auto pr-1" data-testid="ob-provider-gallery">
                <div className="rounded-xl border border-line bg-panel p-4" data-testid="ob-custom-card">
                  <div className="text-[15px] font-semibold">{t("providers.customProviderCard")}</div>
                  <p className="text-[12px] text-muted mt-0.5 mb-3 leading-relaxed">
                    {t("providers.customProviderCardSub")}
                  </p>
                  <CustomCreateForm ps={ps} tp="ob" inline />
                </div>
                {ps.orderedCustom.length > 0 && (
                  <div className="mt-4">
                    <ProviderCards ps={ps} tp="ob" customOnly hideAdd />
                  </div>
                )}
              </div>
            ) : (
              /* ---- one provider's key form, or the new-custom-provider form, same box ---- */
              <div className="flex-1 min-h-0 overflow-y-auto pr-1">
                <ProviderForm ps={ps} tp="ob" />
              </div>
            )}

            {/* Persistent footer (§39). */}
            <div className="flex items-center gap-3 pt-5">
              {!skipConfirm ? (
                <button className="text-[12.5px] text-faint hover:text-muted" onClick={() => setSkipConfirm(true)}>
                  {t("onboarding.skipSetup")}
                </button>
              ) : (
                <span className="text-[12.5px] text-muted">
                  {t("onboarding.nothingWorks")}{" "}
                  <button className="text-accent" onClick={() => finish()}>
                    {t("onboarding.skipAnyway")}
                  </button>
                </span>
              )}
              <button
                className="ml-auto px-6 py-2 rounded-full bg-ink text-panel text-[13px] disabled:opacity-40"
                disabled={!canNext || ps.verify.state === "testing"}
                onClick={advance}
                data-testid="ob-continue"
              >
                {ps.verify.state === "testing" ? t("onboarding.checking") : t("onboarding.next")}
              </button>
            </div>
            <p className="text-[11px] text-faint mt-3">
              {t("onboarding.modelsHint")}
            </p>
          </section>
        )}

        {step === 1 && (
          /* §41 (owner design, 2026-07-19, supersedes §39's card gallery): BENEFIT ROWS are
             the connect surface — one row set, two states, ZERO layout shift. Pre-sign-in the
             rows make the case and a pinned band asks for sign-in; after sign-in the band's
             slot keeps its place but flips to a green congrats, and every row grows a quiet
             Connect pill. The gated Google pair is ONE combined grayed row. */
          <section data-testid="ob-step-tools" className="flex-1 min-h-0 flex flex-col">
            <h1 className="text-[19px] font-semibold">{t("onboarding.toolsTitle")}</h1>
            <p className="text-[13px] text-muted mt-0.5 mb-3">
              {t("onboarding.toolsSub")}
            </p>

            <div className="flex-1 min-h-0 overflow-y-auto pr-1" data-testid="ob-tool-gallery">
              {TOOL_ROWS.map(({ name, benefitKey, detailKey }) => {
                const c = connectors.find((x) => x.name === name);
                if (!c) return null;
                return (
                  <div
                    key={name}
                    className="flex items-center gap-3 py-2 border-b border-paper last:border-0"
                    data-testid={`ob-tool-${name}`}
                  >
                    <ConnectorBadge connector={c} size={34} title={c.title} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13.5px] font-semibold leading-tight">{t(benefitKey)}</span>
                      <span className="block text-[12px] text-muted truncate">{t(detailKey)}</span>
                    </span>
                    {cloud?.signed_in &&
                      (c.connected ? (
                        <span className="text-[12px] text-ok font-medium shrink-0">{t("onboarding.toolConnected")}</span>
                      ) : pendingTool === name ? (
                        <span className="text-[12px] text-muted shrink-0">{t("onboarding.checkBrowser")}</span>
                      ) : (
                        <button
                          className="shrink-0 rounded-full border border-line px-4 py-1.5 text-[12.5px] font-medium hover:border-lineStrong"
                          onClick={() => startTool(name)}
                        >
                          {t("connectors.connect")}
                        </button>
                      ))}
                  </div>
                );
              })}
              {/* The gated Google pair: one combined grayed row, both states (§41). */}
              <div className="flex items-center gap-3 py-2" data-testid="ob-tool-google-soon">
                <span className="flex gap-1.5 opacity-40 grayscale">
                  {TOOLS_SOON.map((n) => {
                    const c = connectors.find((x) => x.name === n);
                    return c ? <ConnectorBadge key={n} connector={c} size={28} title={c.title} /> : null;
                  })}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13.5px] font-semibold leading-tight text-faint">
                    {t("onboarding.googleSoon")}
                  </span>
                  <span className="block text-[12px] text-faint truncate">
                    {t("onboarding.googleSoonDetail")}
                  </span>
                </span>
                {cloud?.signed_in && <span className="text-[11.5px] text-faint shrink-0">{t("onboarding.comingSoon")}</span>}
              </div>
            </div>

            {/* The band is PINNED outside the scroll area and its slot never moves: the ask
                pre-sign-in, a green congrats after — zero layout shift at the moment the user
                returns from the browser (§41). */}
            {!cloud?.signed_in ? (
              <div className="mt-3.5 rounded-xl border border-line bg-paper px-4 py-3 flex items-center gap-3.5 shrink-0">
                <span className="flex-1 text-[12.5px] text-muted leading-snug">
                  <span className="block text-[13px] font-semibold text-ink mb-0.5">
                    {t("onboarding.signinTitle")}
                  </span>
                  {t("onboarding.signinBody")}
                </span>
                {signinPhase ? (
                  <span className="inline-flex items-center gap-2 text-[12.5px] text-muted shrink-0">
                    <Spinner />
                    {signinPhase === "opening" ? (
                      t("onboarding.openingBrowser")
                    ) : (
                      <>
                        {t("onboarding.waiting")}{" "}
                        <button
                          className="underline hover:text-ink"
                          onClick={() => setSigninPhase(null)}
                          data-testid="ob-signin-cancel"
                        >
                          {t("common.cancel")}
                        </button>
                      </>
                    )}
                  </span>
                ) : (
                  <button
                    className="shrink-0 px-5 py-2 rounded-full bg-ink text-panel text-[13px]"
                    onClick={async () => {
                      setSigninPhase("opening");
                      await cloudLogin().catch(() => {});
                      setSigninPhase("waiting");
                    }}
                    data-testid="ob-cloud-signin"
                  >
                    {t("common.signIn")}
                  </button>
                )}
              </div>
            ) : (
              <div
                className="mt-3.5 rounded-xl border border-line bg-okSoft px-4 py-3 shrink-0"
                data-testid="ob-tools-signedin"
              >
                <span className="block text-[13px] font-semibold text-ok mb-0.5">
                  {t("onboarding.signedIn")}
                  {cloud.account ? t("onboarding.signedInAs", { account: cloud.account }) : ""}
                </span>
                <span className="block text-[12.5px] text-muted">
                  {t("onboarding.signedInBody")}
                </span>
              </div>
            )}

            {/* One footer button, one slot: quiet skip pre-sign-in, black Next after. */}
            <div className="flex items-center mt-3.5">
              {cloud?.signed_in ? (
                <button
                  className="ml-auto px-6 py-2 rounded-full bg-ink text-panel text-[13px] shrink-0"
                  onClick={() => setStep(2)}
                  data-testid="ob-continue-tools"
                >
                  {t("onboarding.next")}
                </button>
              ) : (
                <button
                  className="ml-auto px-5 py-2 rounded-full border border-line text-[13px] text-muted hover:text-ink hover:border-lineStrong shrink-0"
                  onClick={() => setStep(2)}
                  data-testid="ob-tools-skip"
                >
                  {t("onboarding.continueWithoutSignin")}
                </button>
              )}
            </div>
            <p className="text-[11px] text-faint mt-3">
              {t("onboarding.moreTools")}
            </p>
          </section>
        )}

        {step === 2 && (
          <section data-testid="ob-step-done" className="flex-1 min-h-0 flex flex-col overflow-y-auto">
            <div className="text-center">
              <div className="w-12 h-12 rounded-full bg-okSoft text-ok grid place-items-center mx-auto mb-3 text-[22px]">
                ✓
              </div>
              <h1 className="text-[19px] font-semibold mb-1">{t("onboarding.doneTitle")}</h1>
              <p className="text-[13px] text-muted mb-5">{t("onboarding.doneSub")}</p>
            </div>

            <button
              className="w-full flex items-start gap-3 rounded-xl2 border border-line hover:border-accent bg-panel px-4 py-3.5"
              onClick={() => finish("automations")}
              data-testid="ob-cta-automation"
            >
              <span className="w-9 h-9 rounded-lg bg-accentSoft text-accent grid place-items-center text-[15px] shrink-0">
                ◷
              </span>
              <span className="flex-1 min-w-0 text-left">
                <b className="block text-[13.5px]">{t("onboarding.ctaAutomation")}</b>
                <span className="text-[12px] text-muted">
                  {t("onboarding.ctaAutomationSub")}
                </span>
              </span>
              <span className="text-faint self-center">›</span>
            </button>
            <button
              className="w-full flex items-start gap-3 rounded-xl2 border border-line hover:border-accent bg-panel px-4 py-3.5 mt-2.5"
              onClick={() => finish("work")}
              data-testid="ob-start"
            >
              <span className="w-9 h-9 rounded-lg bg-accentSoft text-accent grid place-items-center text-[15px] shrink-0">
                ✦
              </span>
              <span className="flex-1 min-w-0 text-left">
                <b className="block text-[13.5px]">{t("onboarding.ctaWork")}</b>
                <span className="text-[12px] text-muted">
                  {t("onboarding.ctaWorkSub")}
                </span>
              </span>
              <span className="text-faint self-center">›</span>
            </button>

            {/* The Specialist-deltas gallery card and the per-session-scope line stay HIDDEN
                (owner call 2026-07-12); the finish("gallery") plumbing remains for their return. */}

            <p className="text-[11px] text-faint text-center mt-auto pt-5">
              {t("onboarding.replay")}
            </p>
          </section>
        )}
      </div>
    </div>
  );
}
