# GitHub-side governance steps (manual — needs your access)

These cannot be executed from this environment: `gh` CLI is not installed and no
`GITHUB_TOKEN` is set. Run them yourself, or grant access and I'll execute them.

Install the CLI first if you want me to run them:

```bash
# Windows (winget) — then authenticate
winget install GitHub.cli
gh auth login
```

---

## 1. Push the work branch + open the PR

```bash
git push -u origin refactor/repository-normalization
# then create the PR (from this branch into main)
```

## 2. Require CI before locking — IMPORTANT ORDER

The `ci-required` job must run and pass **once** on the PR before you set it as
the required check, otherwise GitHub refuses to add a check that has no history.

1. Push the branch / open the PR so `ci-required` runs.
2. Wait for it to be green.
3. Then add it as the unique required check (below).

## 3. Protect `main` (Ruleset — preferred) or Branch Protection

Use a **Ruleset** targeting `main` (the repo uses Ruleset governance):

- **Name**: `protect-main`
- **Target**: `main`
- **Enforcement**: Active
- **Rules**:
  - Require a pull request before merging — required approvals: **0**
  - Block force pushes — **on**
  - Restrict deletions — **on**
  - Require conversation resolution — **on**
  - Require linear history — **on** (matches squash-only)
- Require status checks — add exactly one: **`ci-required`**
    - *Unique* check on purpose: a directory rename that changes a matrix check
      name can never strand a Required Check.
  - Require branches to be up to date before merging — **on**. This is the
    Ruleset equivalent of `strict_required_status_checks_policy = true`.
- **Required pull request → merge method**: Squash merge (or allow only squash)

Equivalent Branch Protection settings exist if you prefer that UI; the Ruleset
form is the one this repo is set up for.

## 4. Tag ruleset

- **Name**: `protect-release-tags`
- **Target**: tags matching `v*`
- **Enforcement**: Active
- **Rules**: block deletion and non-fast-forward updates; do not grant a bypass
  unless a specifically designated release actor needs one. Tag creation should
  remain limited to the release workflow/release maintainer.

## 5. Actions permissions audit

In **Settings → Actions → General**:
- **Workflow permissions**: Read repository contents and packages (**read**
  default) — `contents: read`. The only write is the release job, which requests
  granular `contents: write` itself.
- Review the two workflows that need extra permission:
  - `.github/workflows/release.yml` — releases (granular write, main-only).
  - `.github/workflows/upstream-sync.yml` — pushes to `upstream-openworker`
    mirror branch (long-lived upstream mirror, allowed).

## 6. Confirm after locking

After the ruleset is live and `ci-required` has a green run, the gate is real.
If you later rename a job, `ci-required`'s `needs:` must be updated but the
Required Check name stays `ci-required` — no drift.
