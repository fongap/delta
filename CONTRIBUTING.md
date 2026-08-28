# Contributing to Delta

Short and practical. See `docs/architecture/repository-layout.md` for the
repository structure.

## Branch naming

Use a prefixed branch off `main`:

`feat/*`, `fix/*`, `refactor/*`, `ci/*`, `chore/*`, `docs/*`, `test/*`,
`release/*`, `upstream/*`.

## PR flow

1. Branch off `main` (see naming above).
2. Open a PR against `main`.
3. CI runs; the `ci-required` checks must pass.
4. Merge via squash into `main` once green.

## Commit convention

- Format: `<type>: <description>` (e.g. `fix: honor idle timeout in scheduler`).
- `WIP` commits are fine on a working branch.
- `main` gets clean, squashed commits — no WIP, no noisy history.

## CI requirement

- The `ci-required` checks must pass before merge.
- `layout-check` guards repository structure — do not add top-level directories
  or violate the layout (see `docs/architecture/repository-layout.md`). There
  is no top-level "layout v3".

## Upstream rule

- Upstream sync must go through a PR — **never auto-merge to `main`**.

## Release rule

- Releases are cut only from `main`.
- Tags follow `v*` (e.g. `v1.2.0`).
