// Delta is the only product persona. No persona is "project-scoped" (that was the retired
// code-family behavior: an explicit directory picked by the user, sessions grouped by project);
// Delta runs on a transparent per-conversation scratch dir, with real folders added as roots
// when needed — no folder gate.
export function isProjectScoped(_p?: { workspace?: string; family?: string }): boolean {
  return false;
}

// Persona naming: the product is "Delta"; there is exactly one persona. These helpers keep the
// API shape (name + id) so the display layer doesn't special-case; with only Delta they always
// render "Delta".
export function shortPersonaName(name?: string, id?: string): string {
  return id === "delta" ? "Delta" : (name || id || "").trim() || "Delta";
}

export function fullPersonaName(name?: string, id?: string): string {
  return id === "delta" ? "Delta" : (name || id || "").trim() || "Delta";
}