import { useEffect, useRef, useState } from "react";
import {
  cloudLogin,
  connectManaged,
  getCloudStatus,
  getConnectors,
  getRecentChannels,
  waitForCloudSignIn,
  type CloudStatus,
  type Connector,
  type RecentChannel,
} from "../api";
import { ConnectorBadge } from "../features/connectors/ConnectorIcon";
import { ChannelPicker } from "./SubscriptionsChip";
import { SelectMenu } from "./SelectMenu";
import { useI18n } from "@delta/i18n/I18nContext";

// The Automations quickstart (UX-DECISIONS §29): ONE template system. The former onboarding
// recipe step (§24's role recipes) merged into the page's "Start from a template" grid — every
// card carries §27's connector-dot vocabulary (brand = connected, grayscale = needs connecting);
// picking a card expands the configure card below the grid: connect rows (with the lazy cloud
// sign-in pane), channel-by-name, day × time, and the §25 consent line for write recipes.
// The `ob-*` testids moved here with the machinery.

// "When" = day choice × free time (owner call 2026-07-11); the cron assembles from the two.
const DAYS: Record<string, { label: string; dow: string }> = {
  mon: { label: "automation.dayMon", dow: "1" },
  tue: { label: "automation.dayTue", dow: "2" },
  wed: { label: "automation.dayWed", dow: "3" },
  thu: { label: "automation.dayThu", dow: "4" },
  fri: { label: "automation.dayFri", dow: "5" },
  sat: { label: "automation.daySat", dow: "6" },
  sun: { label: "automation.daySun", dow: "0" },
  weekdays: { label: "automation.dayWeekdays", dow: "1-5" },
  daily: { label: "automation.dayDaily", dow: "*" },
};
// §30 connect-state spinner (the app has no other spinner — waits elsewhere are label swaps).
// Exported for Onboarding page 2's sign-in button (same states, same look).
export const Spinner = () => (
  <span className="inline-block w-3 h-3 rounded-full border-[1.5px] border-line border-t-accent animate-spin" />
);

const cronFor = (dayKey: string, hhmm: string) => {
  const [h, m] = hhmm.split(":");
  return `${Number(m) || 0} ${Number(h) || 9} * * ${DAYS[dayKey]?.dow ?? "*"}`;
};

type T = (key: string, vars?: Record<string, string | number>) => string;

interface QuickTemplate {
  key: string;
  tk: string; // i18n key prefix, e.g. "automation.template.github"
  title: string;
  blurb: string;
  cadence: string; // i18n key for the card's footer label (automation.cadence.*)
  conns: { name: string; why: string; whyKey?: string }[]; // [] = no connections needed
  needsRepo?: boolean;
  needsChannel?: boolean;
  consent?: boolean; // write recipes carry the §25 consent line; reads carry disclosure
  deliver?: boolean; // Morning brief's deliver-to choice
  day: string;
  time: string;
  instructions: (t: T, ctx: { repo: string; channel: string; deliver: "app" | "slack" }) => string;
}

const TEMPLATES: QuickTemplate[] = [
  {
    key: "github",
    tk: "automation.template.github",
    title: "GitHub digest",
    blurb: "Merged PRs and commits, posted to your team's Slack.",
    cadence: "automation.cadence.weekly",
    conns: [
      { name: "slack", why: "Where the digest posts", whyKey: "automation.template.github.whySlack" },
      { name: "github", why: "What the digest summarizes", whyKey: "automation.template.github.whyGithub" },
    ],
    needsRepo: true,
    needsChannel: true,
    consent: true,
    day: "mon",
    time: "09:00",
    instructions: (t, { repo, channel }) =>
      t("automation.template.github.instructions", {
        repo: repo || t("automation.template.github.connectedRepo"),
        channel,
      }),
  },
  {
    key: "pipeline",
    tk: "automation.template.pipeline",
    title: "Pipeline digest",
    blurb: "Deals that moved — and deals going quiet — posted to Slack.",
    cadence: "automation.cadence.weekly",
    conns: [
      { name: "slack", why: "Where the digest posts", whyKey: "automation.template.pipeline.whySlack" },
      { name: "hubspot", why: "Pipeline and deal activity", whyKey: "automation.template.pipeline.whyHubspot" },
    ],
    needsChannel: true,
    consent: true,
    day: "mon",
    time: "09:00",
    instructions: (t, { channel }) => t("automation.template.pipeline.instructions", { channel }),
  },
  {
    key: "brief",
    tk: "automation.template.brief",
    title: "Morning brief",
    blurb: "Calendar and unread email, summarized before your day starts.",
    cadence: "automation.cadence.daily",
    conns: [
      { name: "google_calendar", why: "Today's meetings and gaps", whyKey: "automation.template.brief.whyCalendar" },
      { name: "gmail", why: "What arrived overnight", whyKey: "automation.template.brief.whyGmail" },
    ],
    deliver: true,
    day: "daily",
    time: "08:00",
    instructions: (t, { deliver }) =>
      t("automation.template.brief.instructions") +
      (deliver === "app" ? t("automation.template.brief.deliverApp") : t("automation.template.brief.deliverSlack")),
  },
  {
    key: "news",
    tk: "automation.template.news",
    title: "Morning news briefing",
    blurb: "A 5-bullet tech & world news digest, saved as markdown.",
    cadence: "automation.cadence.daily",
    conns: [],
    day: "daily",
    time: "08:00",
    instructions: (t) => t("automation.template.news.instructions"),
  },
  {
    key: "inboxdigest",
    tk: "automation.template.inboxdigest",
    title: "Inbox digest",
    blurb: "One short digest of your unread email.",
    cadence: "automation.cadence.weekdays",
    conns: [{ name: "gmail", why: "Your unread email", whyKey: "automation.template.inboxdigest.whyGmail" }],
    day: "weekdays",
    time: "09:00",
    instructions: (t) => t("automation.template.inboxdigest.instructions"),
  },
  {
    key: "cleanup",
    tk: "automation.template.cleanup",
    title: "Folder cleanup",
    blurb: "Sort recent Downloads into tidy folders by type.",
    cadence: "automation.cadence.weekly",
    conns: [],
    day: "fri",
    time: "17:30",
    instructions: (t) => t("automation.template.cleanup.instructions"),
  },
];

export function AutomationQuickstart({
  busy,
  onCreate,
}: {
  busy: boolean;
  onCreate: (payload: {
    title: string;
    instructions: string;
    cron?: string;
    permissions?: { tool: string; target: string; access: "read" | "write" }[];
  }) => void;
}) {
  const { t } = useI18n();
  const [pickedKey, setPickedKey] = useState<string | null>(null);
  const picked = TEMPLATES.find((x) => x.key === pickedKey) || null;

  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [cloud, setCloud] = useState<CloudStatus | null>(null);
  const [pendingConn, setPendingConn] = useState<string | null>(null);
  // §30 connect states: "opening" while the broker POST is in flight (the browser hasn't
  // appeared yet), "waiting" once it has — the handoff strip explains the out-of-band finish.
  const [connFlow, setConnFlow] = useState<{ name: string; phase: "opening" | "waiting" } | null>(
    null,
  );
  const [signinPhase, setSigninPhase] = useState<"opening" | "waiting" | null>(null);
  const [recent, setRecent] = useState<RecentChannel[]>([]);
  const [repo, setRepo] = useState("");
  const [channel, setChannel] = useState("");
  const [day, setDay] = useState("mon");
  const [time, setTime] = useState("09:00");
  const [deliver, setDeliver] = useState<"app" | "slack">("app");
  const [consent, setConsent] = useState(true);

  const refresh = () => {
    getConnectors().then(setConnectors).catch(() => {});
    getCloudStatus().then(setCloud).catch(() => {});
  };
  // Connector state drives the card dots, so load once up front; poll only while a template
  // is being configured (connects and the cloud sign-in land out-of-band).
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    refresh();
  }, []);
  useEffect(() => {
    if (!picked) return;
    refresh();
    getRecentChannels().then(setRecent).catch(() => {});
    pollRef.current = setInterval(refresh, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickedKey]);

  const connState = (name: string) => connectors.find((c) => c.name === name);
  const allConnected = !picked || picked.conns.every((c) => connState(c.name)?.connected);
  // §25 consent line shows the HUMAN name (owner catch 2026-07-14: it echoed the raw
  // slack:T…/C… target). Names come from a picker pick (remembered per address) or the
  // recent list; a hand-typed raw address stays raw — we never guess.
  const [picked_names, setPickedNames] = useState<Record<string, { name: string; workspace?: string }>>({});
  const pickedInfo = picked_names[channel];
  const channelName = pickedInfo?.name || recent.find((c) => c.channel === channel)?.name;
  const channelLabel = channelName ? `#${channelName}` : channel;
  const channelWorkspace = pickedInfo?.workspace;

  // The poll flipping a row to ✓ is what ends its waiting state.
  useEffect(() => {
    if (connFlow && connState(connFlow.name)?.connected) setConnFlow(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectors]);

  // §30: the configure card scrolls into view on pick — it expands below the fold on
  // three-row grids and otherwise appears "nowhere".
  const cfgRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (pickedKey) cfgRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [pickedKey]);

  const pick = (tmpl: QuickTemplate) => {
    setPickedKey(tmpl.key);
    setDay(tmpl.day);
    setTime(tmpl.time);
    setConsent(true);
    setConnFlow(null);
  };

  const startConnect = async (name: string) => {
    if (!cloud?.signed_in) {
      setPendingConn(name); // the pane appears; sign-in completes it
      return;
    }
    // §30: the broker round-trip takes seconds — narrate it on the row itself.
    setConnFlow({ name, phase: "opening" });
    // GitHub is authorize-first at the BROKER: one connect links an existing
    // installation or lands on the install page — no flow choice here anymore.
    await connectManaged(name).catch(() => {});
    // The POST resolves once the system browser is off; the poll ends the waiting state.
    setConnFlow((f) => (f?.name === name ? { name, phase: "waiting" } : f));
    refresh();
  };

  const signinPollRef = useRef<(() => void) | null>(null);
  const cancelSignin = () => {
    signinPollRef.current?.();
    signinPollRef.current = null;
    setSigninPhase(null);
  };
  useEffect(() => cancelSignin, []); // never leave the poll running after unmount

  const signInThenConnect = async () => {
    setSigninPhase("opening");
    await cloudLogin().catch(() => {});
    setSigninPhase("waiting");
    // Poll until the browser flow lands, then finish the pending connect (bounded).
    signinPollRef.current = waitForCloudSignIn(async (s) => {
      signinPollRef.current = null;
      setSigninPhase(null);
      if (!s?.signed_in) return;
      setCloud(s);
      if (pendingConn) {
        const name = pendingConn;
        setConnFlow({ name, phase: "opening" });
        await connectManaged(name).catch(() => {});
        setConnFlow((f) => (f?.name === name ? { name, phase: "waiting" } : f));
        setPendingConn(null);
        refresh();
      }
    });
  };

  const create = () => {
    if (!picked) return;
    onCreate({
      title: t(picked.tk + ".title"),
      instructions: picked.instructions(t, { repo, channel, deliver }),
      cron: cronFor(day, time),
      permissions:
        picked.consent && consent && channel
          ? [{ tool: "send_message", target: channel, access: "write" }]
          : [],
    });
  };

  const gateHint = !allConnected
    ? t("automation.connectToContinue", {
        names: picked?.conns
          .filter((c) => !connState(c.name)?.connected)
          .map((c) => connState(c.name)?.title || c.name)
          .join(" and ") || "",
      })
    : picked?.needsChannel && !channel
      ? t("automation.pickChannelFirst")
      : "";

  const label = "block text-[12px] text-muted mt-3 mb-1";
  const input =
    "w-full px-3 py-2 rounded-lg border border-line bg-panel text-[13.5px] outline-none focus:border-accent";

  return (
    <div className="mb-4">
      <div className="text-[11px] uppercase tracking-[0.05em] text-faint mb-2.5">
        {t("automation.startFromTemplate")}
      </div>
      {/* Equal-height cards (owner ask 2026-07-12): 1fr rows + h-full — <button> grid items
          don't stretch like divs. */}
      <div className="grid grid-cols-3 auto-rows-fr gap-3">
        {TEMPLATES.map((tmpl) => (
          <button
            key={tmpl.key}
            data-testid={`qs-template-${tmpl.key}`}
            className={
              "h-full text-left rounded-xl2 border bg-panel p-4 flex flex-col gap-1.5 " +
              (pickedKey === tmpl.key
                ? "border-accent ring-2 ring-accentSoft"
                : "border-line hover:border-lineStrong")
            }
            onClick={() => pick(tmpl)}
          >
            <span className="text-[13.5px] font-semibold">{t(tmpl.tk + ".title")}</span>
            <span className="text-[12px] text-muted leading-relaxed flex-1">{t(tmpl.tk + ".blurb")}</span>
            <span className="flex items-center gap-1.5 mt-1">
              {tmpl.conns.map((c) => {
                const cs = connState(c.name);
                const on = !!cs?.connected;
                return (
                  <span
                    key={c.name}
                    title={`${cs?.title || c.name} — ${on ? t("common.connected") : t("automation.notConnectedYet")}`}
                    style={on ? undefined : { filter: "grayscale(1)", opacity: 0.55 }}
                  >
                    {cs ? (
                      <ConnectorBadge connector={cs} size={16} title={cs.title} />
                    ) : (
                      <span className="inline-block w-4 h-4 rounded-full border border-line" />
                    )}
                  </span>
                );
              })}
              <span className="text-[11px] text-faint ml-0.5">
                {tmpl.conns.length === 0
                  ? t("automation.noConnections", { cadence: t(tmpl.cadence) })
                  : t(tmpl.cadence)}
              </span>
            </span>
          </button>
        ))}
      </div>

      {picked && (
        <div
          ref={cfgRef}
          className="mt-3 rounded-xl2 border border-line bg-panel p-4"
          data-testid="qs-configure"
        >
          {/* §30: the card names its template — without this it starts abruptly after the grid. */}
          <div className="flex items-baseline gap-2 pb-2.5 mb-1 border-b border-line">
            <span className="text-[11px] uppercase tracking-[0.05em] text-accent font-semibold">
              {t("automation.setUp")}
            </span>
            <span className="text-[14px] font-semibold">{t(picked.tk + ".title")}</span>
            <span className="ml-auto text-[12px] text-faint max-sm:hidden">
              {picked.conns.length ? t("automation.connDeliverySchedule") : t("automation.deliverySchedule")} ·{" "}
              {t(picked.cadence)}
            </span>
          </div>
          {picked.conns.map(({ name, why, whyKey }) => {
            const c = connState(name);
            const flow = connFlow?.name === name ? connFlow : null;
            return (
              <div key={name} className="border-b border-line last:border-b-0">
                <div className="flex items-center gap-3 py-2.5">
                  {c && <ConnectorBadge connector={c} size={26} title={c.title} />}
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13.5px] font-medium">{c?.title || name}</span>
                    <span className="block text-[11.5px] text-faint">{whyKey ? t(whyKey) : why}</span>
                  </span>
                  {c?.connected ? (
                    <span className="text-[12.5px] text-ok">✓ {t("common.connected")}</span>
                  ) : flow ? (
                    <span className="inline-flex items-center gap-2 text-[12px] text-muted">
                      <Spinner />
                      {flow.phase === "opening"
                        ? t("automation.openingBrowser")
                        : t("automation.waitingFor", { name: c?.title || name })}
                    </span>
                  ) : (
                    <button
                      className="px-3.5 py-1 rounded-full border border-line text-[12.5px] hover:bg-paper"
                      onClick={() => startConnect(name)}
                      data-testid={`ob-connect-${name}`}
                    >
                      {t("connectors.connect")}
                    </button>
                  )}
                </div>
                {/* §30 handoff strip: the flow finishes out-of-band in the browser — say so,
                    and let Cancel clear the LOCAL state (the browser tab is the user's). */}
                {flow?.phase === "waiting" && (
                  <div
                    className="flex items-start gap-2 bg-accentSoft/50 rounded-lg px-3 py-2 mb-2.5 text-[12px] text-muted"
                    data-testid="ob-connect-wait"
                  >
                    <span>↗</span>
                    <span className="flex-1 min-w-0">
                      <b className="text-ink font-medium">
                        {t("automation.finishConnecting", { name: c?.title || name })}
                      </b>{" "}
                      {t("automation.approveThere")}
                    </span>
                    <button
                      className="text-faint underline hover:text-muted shrink-0"
                      onClick={() => setConnFlow(null)}
                      data-testid="ob-connect-cancel"
                    >
                      {t("common.cancel")}
                    </button>
                  </div>
                )}
              </div>
            );
          })}

          {pendingConn && !cloud?.signed_in && (
            <div
              className="bg-accentSoft/50 rounded-xl px-4 py-3 mt-3 text-[12.5px] text-muted"
              data-testid="ob-cloudpane"
            >
              <span className="block text-[13px] text-ink font-medium">
                {t("automation.oneSignIn")}
              </span>
              {t("automation.brokeredCloud")}
              <div className="flex items-center gap-3 mt-2">
                {signinPhase ? (
                  <>
                    <span className="inline-flex items-center gap-2 text-[12px]">
                      <Spinner />
                      {signinPhase === "opening"
                        ? t("automation.openingBrowser")
                        : t("automation.waitingSignIn")}
                    </span>
                    {signinPhase === "waiting" && (
                      <span className="text-[11.5px] text-faint">
                        {t("automation.finishSignIn")}{" "}
                        <button
                          className="underline hover:text-muted"
                          onClick={cancelSignin}
                          data-testid="ob-signin-cancel"
                        >
                          {t("common.cancel")}
                        </button>
                      </span>
                    )}
                  </>
                ) : (
                  <button
                    className="px-3.5 py-1 rounded-full border border-line text-[12.5px] text-accent hover:bg-panel"
                    onClick={signInThenConnect}
                    data-testid="ob-cloud-signin"
                  >
                    {t("nav.signInCloud")}
                  </button>
                )}
              </div>
            </div>
          )}

          {allConnected && (
            <div className={picked.conns.length ? "bg-paper rounded-xl px-4 py-3.5 mt-3" : ""} data-testid="ob-recipe">
              {picked.needsRepo && (
                <>
                  <label className={label}>{t("automation.repository")}</label>
                  <input
                    className={input}
                    placeholder={t("automation.ownerRepoPlaceholder")}
                    value={repo}
                    onChange={(e) => setRepo(e.target.value)}
                    data-testid="ob-repo"
                  />
                </>
              )}
              {picked.needsChannel && (
                <>
                  <label className={label}>{t("automation.postToChannel")}</label>
                  <div data-testid="ob-channel">
                    <ChannelPicker
                      value={channel}
                      onChange={setChannel}
                      recent={recent}
                      onPickName={(address, name, workspace) =>
                        setPickedNames((m) => ({ ...m, [address]: { name, workspace } }))
                      }
                    />
                  </div>
                  <p className="text-[11px] text-warnInk mt-1">
                    {t("automation.botMember")}
                  </p>
                </>
              )}
              <label className={label}>{t("automation.when")}</label>
              <div className="flex gap-2">
                <div className="flex-1 min-w-0">
                  <SelectMenu
                    ariaLabel={t("automation.day")}
                    value={day}
                    options={Object.entries(DAYS).map(([k, v]) => ({ value: k, label: t(v.label) }))}
                    onChange={setDay}
                  />
                </div>
                <input
                  className="w-28 px-3 py-2 rounded-lg border border-line bg-panel text-[13.5px] outline-none focus:border-accent"
                  type="time"
                  aria-label={t("automation.time")}
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                />
              </div>
              {picked.deliver && (
                <>
                  <label className={label}>{t("automation.deliverTo")}</label>
                  <SelectMenu
                    ariaLabel={t("automation.deliverTo")}
                    value={deliver}
                    options={[
                      { value: "app", label: t("automation.deliverApp") },
                      { value: "slack", label: t("automation.deliverSlackDm") },
                    ]}
                    onChange={(v) => setDeliver(v as "app" | "slack")}
                  />
                </>
              )}
              {picked.consent ? (
                <label className="flex items-start gap-2.5 mt-3.5 text-[12.5px] text-muted select-none">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    data-testid="ob-consent"
                  />
                  <span>
                    {t("automation.consent", {
                      channel: channelLabel || t("automation.theChannel"),
                    })}
                    {channelWorkspace ? ` (${channelWorkspace})` : ""}
                  </span>
                </label>
              ) : picked.conns.length > 0 ? (
                <p className="text-[12.5px] text-muted mt-3">{t("automation.readsOnly")}</p>
              ) : null}
            </div>
          )}

          <div className="flex items-center gap-3 mt-4">
            <button
              className="text-[12.5px] text-faint hover:text-muted"
              onClick={() => setPickedKey(null)}
            >
              {t("common.cancel")}
            </button>
            {/* A silently-disabled primary reads as a bug — always name the missing piece. */}
            {gateHint && (
              <span className="ml-auto text-[11.5px] text-faint" data-testid="ob-create-hint">
                {gateHint}
              </span>
            )}
            <button
              className={
                (gateHint ? "" : "ml-auto ") +
                "px-5 py-2 rounded-full bg-ink text-panel text-[13px] disabled:opacity-40"
              }
              disabled={busy || !allConnected || (picked.needsChannel && !channel)}
              onClick={create}
              data-testid="ob-create"
            >
              {busy ? t("automation.creating") : t("automation.createAutomation")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
