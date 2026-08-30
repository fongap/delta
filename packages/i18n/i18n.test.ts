// i18n infrastructure tests: key parity between locales, locale normalization,
// and token interpolation. English is the shape of truth; every other locale must
// mirror its key set exactly (§17 localization checkpoint).
import { describe, expect, it } from "vitest";
import { en } from "./en";
import { zh } from "./zh";
import { dictionaries, DEFAULT_LOCALE, normalizeLocale } from "./dictionaries";
import { interpolate } from "./I18nContext";

describe("i18n dictionary integrity", () => {
  it("zh mirrors every en key (no missing, no extras)", () => {
    const enKeys = Object.keys(en) as string[];
    const zhKeys = Object.keys(zh);
    expect(zhKeys.filter((k) => !enKeys.includes(k))).toEqual([]);
    expect(enKeys.filter((k) => !zhKeys.includes(k))).toEqual([]);
  });

  it("every registered locale is non-empty and keyed identically to en", () => {
    const enKeys = Object.keys(en).sort();
    for (const locale of Object.keys(dictionaries) as (keyof typeof dictionaries)[]) {
      const dict = dictionaries[locale];
      expect(dict).toBeTruthy();
      expect(Object.keys(dict).sort()).toEqual(enKeys);
    }
  });

  it("every zh value is non-empty (no accidental blank strings)", () => {
    for (const [key, value] of Object.entries(zh)) {
      expect(value.trim(), key).not.toBe("");
    }
  });
});

describe("normalizeLocale", () => {
  it("maps exact and shorthand values to a supported Locale", () => {
    expect(normalizeLocale("zh-CN")).toBe("zh-CN");
    expect(normalizeLocale("zh")).toBe("zh-CN");
    expect(normalizeLocale("en-US")).toBe("en-US");
    expect(normalizeLocale("en")).toBe("en-US");
  });

  it("falls back to the default for unknown, null, or undefined input", () => {
    expect(normalizeLocale("fr-FR")).toBe(DEFAULT_LOCALE);
    expect(normalizeLocale("")).toBe(DEFAULT_LOCALE);
    expect(normalizeLocale(null)).toBe(DEFAULT_LOCALE);
    expect(normalizeLocale(undefined)).toBe(DEFAULT_LOCALE);
  });
});

describe("interpolation", () => {
  it("replaces {tokens} with provided values", () => {
    expect(interpolate("Hello {name}", { name: "Delta" })).toBe("Hello Delta");
  });

  it("leaves unknown tokens untouched", () => {
    expect(interpolate("A {missing} token", { name: "x" })).toBe("A {missing} token");
  });

  it("returns template unchanged when no vars are given", () => {
    expect(interpolate("static")).toBe("static");
  });
});
