// i18n types — shared between the locale dictionaries and the I18nProvider.
export type Locale = "zh-CN" | "en-US";

/** Flat dot-notation key. Structural typing only; actual key sets live in the dictionaries. */
export type TKey = string;

/** Interpolation values for `{name}`-style tokens in a translation string. */
export type TVars = Record<string, string | number>;
