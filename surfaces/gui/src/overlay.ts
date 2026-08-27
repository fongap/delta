// Pure platform → overlay decision, extracted so the contract is unit-testable.
// The macOS desktop shell keeps the overlay (traffic-light inset) layout; Windows
// and Linux keep their native title bar — the overlay compensations misalign under
// a native bar (alignment bug, caught on Windows 2026-07-21). `sim` is the dev-only
// ?overlay=1 browser preview, inert in the real desktop app (isTauri short-circuits).
export function shouldShowOverlay(desktop: boolean, os: string, sim: boolean): boolean {
  return (desktop && os === "macos") || sim;
}
