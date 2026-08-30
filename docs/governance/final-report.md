# Delta Repository Governance — Historical Final Report

> Superseded by the responsibility-boundary split documented in
> `docs/architecture/repository-layout.md`. This report records the earlier
> `src/delta` migration and is retained only as an audit artifact.

**Status:** Complete (code + CI + docs landed; two human-side GitHub actions remain — see §7)
**Branch of record:** `refactor/repository-normalization`
**Date:** 2026-08-28

This report consolidates the multi-part repository governance effort. Every item below
was implemented, verified, and committed. Where a step can only be performed by a human
with GitHub admin access, it is called out explicitly in §7 rather than silently skipped.

---

## 1. Executive summary

Delta's repository was normalized from an ad-hoc, multi-paradigm layout into a single
canonical structure, its connector frontend unified under one feature module, its Python
runtime package cleaned of test-only code and a private package name, its brand
consolidated to one source of truth, and its largest modules split into cohesive
low-risk units — all guarded by a new CI gate that cannot drift.

The work was delivered across two merged PRs and one open branch of record:

| PR / ref | Merge | Contents |
| --- | --- | --- |
| #5 (`delta-layout-v2`) | `54973ad` | Layout v2: canonical top-level layout, runtime rename, brand alignment, stt crate |
| #6 (`refactor/repository-normalization`) | `2659c1e` | Layout normalization, connector unification, legacy key migration, CI gate, docs |
| branch `refactor/repository-normalization` (3 commits) | **pending** | Large-module refactor (`integration_helpers`, `engine_format`, `integration_github`, `integration_auth`) |

---

## 2. What was delivered

### 2.1 Canonical repository layout (`docs/architecture/repository-layout.md`)

- Top-level directories frozen: `apps/`, `services/`, `core/`, `tests/`,
  `resources/`, `packaging/`, `scripts/`, `docs/`, `.github/`.
- Desktop is **feature-based**: `apps/desktop/src/features/<feature>/` with shared
  code under `src/shared/`; tests are colocated (`*.test.*`).
- A **forbidden-directories list** (`surfaces/`, `crates/`, `coworker/`, `assets/`,
  plus catch-alls like `common/`, `core/`, `utils/`, `legacy/`) documents what must
  never reappear.
- A new `scripts/layout-check`-style CI job (`layout-check`) enforces the structure
  so the layout stays canonical without relying on memory.

### 2.2 Runtime package cleanup

- Private package name `coworker` renamed to **`delta`** (`core/`).
- **Test-only code moved out of the runtime package**: fake Slack server moved from
  `core/testing/` to `tests/fakes/`; `tests/conftest.py` updated. Test-only
  support now lives exclusively under `tests/`, per the layout rule "never inside
  the package."

### 2.3 Connector frontend unification

- All connector UI moved from `src/components/connectors/` into the feature module
  **`src/features/connectors/`** (registry, icon, visuals, 14 component files).
- Every cross-module relative import re-pointed (path-depth corrected); `tsc`
  passes cleanly.
- Provider logo module renamed `logos.ts` → `providerLogos.ts` to resolve a name
  collision.

### 2.4 Brand single source of truth

- `resources/brand/` is the canonical home for the Delta brand
  (`delta-logo-512x512.png`), documented in `resources/brand/MANIFEST.md`.
- `scripts/generate_icons.py` regenerates/verifies all derived icons from the SSOT
  and enforces a staleness check (with a 5 s tolerance to avoid false positives on
  near-simultaneous mtimes).

### 2.5 Legacy OpenWork key migration

- Legacy OpenWork runtime theme keys migrated; `layout-check` now allows the three
  legitimate migration sites (`theme.ts`, `index.html`, `lib.rs`) while still
  flagging any other legacy key reappearance.

### 2.6 CI gate (`ci-required`)

- `.github/workflows/ci.yml` gained a `ci-required` job: `if: always()` +
  `toJson(needs)` — a **single, stable required-check name** that survives matrix
  job renames. This is the check to pin as the unique Required Check (see §7).
- Full CI matrix: Python tests, frontend unit + e2e, Rust (Tauri + launcher),
  advisories (npm/pip/cargo-deny), Ruff/Pyright/format/clippy, and `layout-check`.

### 2.7 Large-module refactor (branch of record, 3 commits)

Splitting `integration_tools.py` (≈4 900 lines) into cohesive units, plus two
stateless formatters out of `engine.py`:

| Commit | New module | Extracted |
| --- | --- | --- |
| `1091dfd` | `integration_helpers.py` | 8 generic helpers (`_meta`, `_schema`, `_attach`, `_request`, `_TextExtractor`, `_html_to_text`, `_now_ms`, `_clamp`) |
| `1091dfd` | `engine_format.py` | 4 stateless formatters (`_assistant_message`, `_tool_result_message`, `_tool_error_message`, `_preview`) |
| `121f36d` | `integration_github.py` | 7 GitHub helpers (headers, base, auth, git args, run-git, call) |
| `1292fde` | `integration_auth.py` | 7 pure header/base builders (google, graph, basic, atlassian, bearer, gitlab, qbo) |

**Design constraint honored:** only helpers that do *not* cross the center test seam
were extracted. `_linear_gql` (which calls `_request`) was deliberately **kept** in
`integration_tools.py` because `tests/test_connectors.py` patches
`integration_tools._request` as a catch-all — moving it would strand those patch
points. The extraction is behavior-preserving: modules re-import the helpers by the
same names.

---

## 3. Verification

| Check | Result |
| --- | --- |
| `tests/test_connectors.py` | **57 passed** |
| `tests/test_github_installs.py` + `tests/test_engine.py` | **33 passed** |
| Full Python suite | **1290 passed, 3 skipped** |
| Pre-existing env failures | 7× `test_bedrock_provider.py` — `ModuleNotFoundError: No module named 'botocore'` (missing optional dep in the venv; **unrelated** to this work, confirmed pre-existing) |
| Frontend `tsc` | passes (post-unification) |
| CI `layout-check` | passes (legacy-key exceptions scoped to 3 files) |

---

## 4. Test-seam engineering note

The extraction used a safe, verifiable pattern:

1. Move **stateless** helpers verbatim to a new module.
2. Re-import them in the origin module **by the same names**, so global-symbol
   resolution inside closures is unchanged.
3. Run tests; the module-level `monkeypatch.setattr` seams reveal any location
   that must stay put.
4. If a helper sits on a center catch-all seam, keep it in place (as with
   `_linear_gql`) rather than break the patch points.

This avoided the trap of a cross-module re-import creating a *second* binding that
silently diverges from the patched one.

---

## 5. Git history (canonical branch path)

```
main
├─ 54973ad  Merge PR #5  (delta-layout-v2)
├─ 2659c1e  Merge PR #6  (refactor/repository-normalization)  ← docs + layout
└─ (pending) 3 commits from refactor/repository-normalization:
    ├─ 1091dfd  split generic helpers → integration_helpers + engine_format
    ├─ 121f36d  extract GitHub helpers → integration_github
    └─ 1292fde  extract pure auth builders → integration_auth
```

The three refactor commits are pushed to `origin/refactor/repository-normalization`
and are **not yet merged into `main`** — that is the single remaining code-side step
(see §7).

---

## 6. Commits of record

| Commit | Summary |
| --- | --- |
| `5d93fb7` | establish Delta repository layout |
| `ab1b4e8` | rename coworker package to delta |
| `647c03b` | align stt crate and brand assets to canonical layout |
| `28f3dcc` | normalize layout, unify connectors, migrate legacy keys |
| `d191954` | docs(governance): manual GitHub-side ruleset steps |
| `1091dfd` | split generic helpers out of integration_tools and engine |
| `121f36d` | extract GitHub connector helpers into integration_github |
| `1292fde` | extract pure auth header builders into integration_auth |

---

## 7. Remaining human-side steps (GitHub admin access required)

These cannot run from this environment (`gh` CLI absent, no token). They are fully
documented in `docs/governance/github-side-steps.md`; the order matters:

1. **Merge the branch of record** — open/merge a PR for the 3 refactor commits into
   `main` (squash merge to keep linear history).
2. **Require CI before locking** — let `ci-required` run green on the PR *first*,
   then pin it as the unique Required Check (a check with no history cannot be added).
3. **Protect `main`** via Ruleset `protect-main` (PR-required, linear history,
   block force-push, restrict deletions, single Required Check `ci-required`).
4. **Protect tags** via Ruleset `protect-tags`.
5. **Actions permissions audit** — confirm `contents: read` default; only
   `release.yml` (release) and `sync-upstream.yml` (upstream mirror) hold granular
   write, scoped and main-only.

---

## 8. Acceptance criteria

All code-side deliverables are complete, committed, and verified:

- ✅ Top-level layout frozen and enforced
- ✅ Runtime cleaned of test-only code; private name renamed
- ✅ Connector frontend unified; provider name collision resolved
- ✅ Brand SSOT with regenerate/verify script
- ✅ Legacy OpenWork keys migrated
- ✅ CI `ci-required` gate in place (single non-drift check)
- ✅ Large modules split into cohesive, seam-safe units
- ✅ Full test suite green (pre-existing env failures documented)
- ⏳ Human-side: merge refactor PR, then apply the ruleset + check pinning (§7)
