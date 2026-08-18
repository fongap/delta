// Lightweight i18n provider — the single source of truth for all user-visible natural
// language in the GUI. Follows the theme pattern (theme.ts): the preference is persisted
// via the backend settings (`prefs.json` → `/v1/settings`), read on mount, and switched at
// runtime through a small state + effects channel so unrelated surfaces re-render.
//
// §17: no second store, no external framework — a hook + context the rest of the app consumes.
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { dictionaries, DEFAULT_LOCALE } from "./dictionaries";
import type { Locale } from "./types";
import type { TranslationKey } from "./en";

export type { TranslationKey };

export interface I18nState {
  locale: Locale;
  setLocale: (l: Locale) => void;
  /** Resolve a key in the active locale. `fallback` is the backend-shipped English
   * presentation string (provider blurb, field label, model label, …); it is used
   * verbatim when the key isn't in the dictionary yet, so a newly added provider or
   * model still renders (in English) instead of showing a raw key. Keys keep their
   * stable internal identity; only the visible copy localizes. */
  t: (key: string, vars?: Record<string, string | number>, fallback?: string) => string;
}

const I18nContext = createContext<I18nState | null>(null);

/** Interpolate `{name}` tokens in `template` with `vars`. Missing keys return undefined→template. */
export function interpolate(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = vars[name];
    return value === undefined || value === null ? match : String(value);
  });
}

export function I18nProvider({
  locale,
  onLocaleChange,
  children,
}: {
  locale: Locale;
  /** Persist+propagate a user-chosen language (backend settings). Default: in-memory only. */
  onLocaleChange?: (l: Locale) => void;
  children: ReactNode;
}) {
  const setLocale = useCallback(
    (l: Locale) => {
      onLocaleChange?.(l);
    },
    [onLocaleChange],
  );

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>, fallback?: string) => {
      const dict = dictionaries[locale] ?? dictionaries[DEFAULT_LOCALE];
      const template = dict[key as TranslationKey];
      if (template === undefined) {
        // Missing-key dev warning; falls back to the provided English string and then the key.
        if (import.meta.env.DEV) console.warn(`[i18n] missing key: ${key} (${locale})`);
        if (fallback !== undefined) return interpolate(fallback, vars);
        return key;
      }
      return interpolate(template, vars);
    },
    [locale],
  );

  const value = useMemo<I18nState>(() => ({ locale, setLocale, t }), [locale, setLocale, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

/** Read the active locale + translation function. Must be used under <I18nProvider>. */
export function useI18n(): I18nState {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within <I18nProvider>");
  return ctx;
}
