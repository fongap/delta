// Locale → dictionary registry. `en` is the shape of truth and the fallback source;
// any locale that lacks a key resolves to it (§ fallback strategy in I18nContext).
import type { Locale } from "./types";
import { en, type TranslationKey } from "./en";
import { zh } from "./zh";

/** The runtime dictionary shape: every key is a concrete string in each locale. */
export type Dict = Record<TranslationKey, string>;

export const dictionaries: Record<Locale, Dict> = {
  "en-US": en,
  "zh-CN": zh,
};

export const DEFAULT_LOCALE: Locale = "en-US";

/** Normalize a runtime string (settings/prefs value) to a supported Locale, or the default. */
export function normalizeLocale(value: string | null | undefined): Locale {
  if (value === "zh-CN" || value === "zh") return "zh-CN";
  if (value === "en-US" || value === "en") return "en-US";
  return DEFAULT_LOCALE;
}
