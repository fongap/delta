# Upstream

Delta is an independent project derived from OpenWorker.

## Upstream

- Repository: `andrewyng/openworker`
- Branch: `main`
- Mirror branch: `upstream-openworker`

## Synchronization

`upstream-openworker` is automatically synchronized with
`andrewyng/openworker:main` by GitHub Actions.

The mirror branch must remain an exact copy of upstream and must not contain
Delta-specific commits.

## Integration Policy

Upstream changes are not automatically merged into `main`.

Changes are reviewed and selectively integrated into Delta based on their
relevance, compatibility, and impact.

Priority is generally given to:

- Agent/runtime fixes
- Security and approval fixes
- MCP and tool protocol improvements
- Connector fixes
- General stability improvements

Delta-specific UI, localization, provider configuration, packaging, branding,
and product architecture remain independently maintained.

## Attribution

Delta originated from OpenWorker and continues to acknowledge the OpenWorker
project and its contributors under the applicable license.
