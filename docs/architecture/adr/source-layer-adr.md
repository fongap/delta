# ADR: Source Layer — Sources as First-Class Citizens (ARCH-001)

- Status: Proposed
- Date: 2026-08-25
- Scope: source/reference modeling across server, runtime, and UI

## Context

Delta is a **general-purpose, locally-first office agent**: its users connect everyday
tools (files, spreadsheets, mail, calendar, chat, web) and ask for work products —
reports, summaries, analyses, drafts. Delta's answers are only as trustworthy as their
inputs, but sources are currently a display-only sidecar:

- `integrations/connectors/base.py` defines `MessageSource` (connector/kind/channel/sender/
  ts/text) purely for rendering connector messages; it is stripped before any provider call.
- The contract exposes `source` only as an optional dict on `MessageDTO`.
- Files referenced during a run (Excel, PDF, CSV) have no durable identity: no hash,
  no version, no retrieval timestamp. When the underlying file changes, Delta cannot
  tell that a previous report's evidence has drifted.

Products like Codex/Claude Code treat attachments as ephemeral turn input. Delta's
positioning (long-lived office evidence: documents, spreadsheets, mail threads, web
pages) requires the opposite: answers must remain auditable against the sources they
cited.

## Decision

Introduce a **SourceRef** as a first-class, persisted record owned by the application
layer (not the runtime):

```text
SourceRef
├ id                  stable id, citable from runs/artifacts
├ origin              file | url | connector | db | manual
├ location            workspace-relative path / URI / connector coordinate
├ fingerprint         sha256 of content bytes at capture time
├ captured_at         when Delta observed this version
├ freshness           checked_at + status: current | changed | missing
├ cited_ranges        optional per-run citations (pages, rows, message ids)
└ permissions         which sessions/personas may cite it
```

Properties:

1. **Versioning by fingerprint, not by copying.** A SourceRef pins one content hash;
   re-reading the same path produces a new fingerprint and flips older refs to
   `changed`. No silent duplication of user files.
2. **Freshness is a background check, not a blocking gate.** Runs record what they saw;
   freshness checks update status asynchronously and surface drift in UI/audit.
3. **Runtime-agnostic.** The runtime receives resolved, readable inputs; it never owns
   source identity. Any future Runtime Adapter can resolve SourceRefs identically.
4. **Citations link runs → sources.** An artifact can answer "which data produced
   this?" with ids and hashes, not prose.

## Non-goals

- No full document store in v1: large binaries stay in the filesystem; the ledger
  references them by id + sha256.
- No automatic re-runs on drift detection; drift is surfaced, not acted upon.

## Consequences

- New table (`sources`) plus a freshness checker task.
- Contract gains a `SourceDTO` (id, origin, display name, fingerprint prefix,
  freshness) — UI may render provenance without filesystem access.
- Audit entries gain optional `source_ids`, tying the two ledgers together.
