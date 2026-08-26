import { describe, expect, it } from "vitest";
import { shouldShowOverlay } from "./overlay";

// The platform → overlay mapping must be deterministic: macOS desktop gets the overlay
// (traffic-light insets), Windows/Linux desktop never do, and the dev `?overlay=1` flag
// is the only way the overlay applies outside the desktop shell. Pinning the matrix so
// a stale platform-detection global (the 2026-07 overlay e2e regression) can't silently
// flip Windows into the mac layout or strip the mac overlay.
describe("shouldShowOverlay", () => {
  it("applies the overlay only for the macOS desktop shell", () => {
    expect(shouldShowOverlay(true, "macos", false)).toBe(true);
  });

  it("never applies the overlay on Windows or Linux desktop", () => {
    expect(shouldShowOverlay(true, "windows", false)).toBe(false);
    expect(shouldShowOverlay(true, "linux", false)).toBe(false);
  });

  it("never applies the overlay in the browser build (not desktop, no sim flag)", () => {
    expect(shouldShowOverlay(false, "macos", false)).toBe(false);
    expect(shouldShowOverlay(false, "windows", false)).toBe(false);
    expect(shouldShowOverlay(false, "linux", false)).toBe(false);
  });

  it("honors the dev-only ?overlay=1 browser preview regardless of platform", () => {
    expect(shouldShowOverlay(false, "macos", true)).toBe(true);
    expect(shouldShowOverlay(false, "windows", true)).toBe(true);
    expect(shouldShowOverlay(false, "linux", true)).toBe(true);
  });
});
