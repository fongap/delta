import { useI18n } from "@delta/i18n/I18nContext";

// Empty-state for a fresh session: just the greeting and the composer below it.
// No example cards, SaaS suggestions, or tool-specific CTA — the user's own
// input is the only entry point.

export function SessionIntro() {
  const { t } = useI18n();
  return (
    <div className="intro">
      <h1 className="greeting">{t("sessionIntro.greeting")}</h1>
    </div>
  );
}
