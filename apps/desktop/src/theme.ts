// Appearance: Light / Dark / Auto-as-system. The preference lives in localStorage only —
// it's per-device (like macOS appearance itself) and must apply before the sidecar is even
// reachable. index.html sets data-theme inline pre-paint with the same key, so the first
// frame is already the right color; this module keeps it current from then on.
import { useEffect, useState } from "react";
import { isTauri, setNativeTheme, followSystemTheme } from "./tauri";

export type ThemePref = "light" | "dark" | "auto";

// Canonical key. The legacy "openwork-theme" / "openwork:theme-pref" pair was migrated to
// the Delta-branded keys below; readThemePref still honors a stored legacy value (writing it
// into the canonical key once) so existing users keep their preference.
const KEY = "delta-theme";
const LEGACY_KEY = "openwork-theme";
const PREF_EVENT = "delta:theme-pref";
const media = window.matchMedia?.("(prefers-color-scheme: dark)");

/** Read the stored preference, migrating a legacy "openwork-theme" value if present. */
function readThemePref(): ThemePref {
  let v = null;
  try {
    v = localStorage.getItem(KEY);
  } catch {
    return "auto";
  }
  if (v === "light" || v === "dark") return v;
  if (v === null) {
    // No canonical value yet — fall back to the legacy key, and migrate it forward.
    let legacy = null;
    try {
      legacy = localStorage.getItem(LEGACY_KEY);
    } catch {
      legacy = null;
    }
    if (legacy === "light" || legacy === "dark") {
      try {
        localStorage.setItem(KEY, legacy);
        localStorage.removeItem(LEGACY_KEY);
      } catch {
        /* private mode etc. — value still applies for this session */
      }
      return legacy;
    }
  }
  return "auto";
}

export function getThemePref(): ThemePref {
  return readThemePref();
}

function apply(pref: ThemePref) {
  const dark = pref === "dark" || (pref === "auto" && !!media?.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  // Follow the webview theme in the native chrome too (Windows/Linux title bar, macOS
  // window appearance). Manual light/dark pins the window theme; auto UN-pins it so the
  // OS is tracked again — re-pinning to a snapshot would freeze the "follow" forever.
  // Fire-and-forget: the browser build has no shell to talk to.
  if (isTauri()) {
    if (pref === "auto") void followSystemTheme();
    else void setNativeTheme(dark);
  }
}

export function setThemePref(pref: ThemePref) {
  try {
    if (pref === "auto") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    /* private mode etc. — still applies for this session */
  }
  apply(pref);
  window.dispatchEvent(new CustomEvent(PREF_EVENT));
}

/** Call once at startup: applies the stored pref and follows the OS appearance (macOS and
 * Windows live theme changes; WebView2 & WKWebView both fire matchMedia change) while in auto. */
export function initTheme() {
  apply(getThemePref());
  media?.addEventListener("change", () => {
    if (getThemePref() === "auto") apply("auto");
  });
}

/** The settings control's hook — stays in sync if the pref changes elsewhere. */
export function useThemePref(): [ThemePref, (p: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(getThemePref);
  useEffect(() => {
    const sync = () => setPref(getThemePref());
    window.addEventListener(PREF_EVENT, sync);
    return () => window.removeEventListener(PREF_EVENT, sync);
  }, []);
  return [pref, setThemePref];
}
