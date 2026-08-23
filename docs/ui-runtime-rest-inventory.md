# Delta UI Runtime REST Inventory (UI-001)

This document inventories the REST boundary used by `surfaces/gui` in the current
Delta working tree. It is an observation of the existing implementation, not a new
runtime contract or an authorization to move UI code.

## Scope and notation

- Snapshot: 2026-08-23, local commit `0db7225d` plus the current uncommitted working tree.
- Production scope: `surfaces/gui/src/**/*.{ts,tsx}`.
- Test-only direct calls are recorded separately at the end.
- Paths in the **Consumer modules** column are relative to `surfaces/gui/src`.
- The helper signature represents the request-side DTO currently exposed to callers;
  the return type represents the response DTO currently exposed by `api.ts`.
- `— (no production caller found)` means the helper is exported but has no static
  production import. The one dynamic import is called out explicitly.
- Query strings and encoded path parameters are shown symbolically.

## Transport boundary summary

- `src/api.ts` owns the HTTP base URL resolution and a module-local `fetch` wrapper.
- The wrapper attaches `X-OpenWorker-Token` when an API token is available.
- `getHealth` requires the current health contract, including `protocolVersion` and
  `capabilities`. Missing required fields are rejected; unknown capabilities and additive
  response fields are ignored with a deduplicated diagnostic rather than exposed to components.
- Contract errors use only `{code, message, details, retriable}`. The first migrated slice
  is the sidecar-token `401`; an `error` envelope field is explicitly rejected by schema
  tests. Other endpoint-local `{ok: false, error}` responses remain outside this slice.
- 133 exported REST helpers contain 133 requests, represented by 114 distinct URL
  expressions (several endpoints support multiple operations or helper payloads).
- No production module outside `src/api.ts` calls `fetch`, `globalThis.fetch`,
  `XMLHttpRequest`, `axios`, or `EventSource` directly.
- `getCloudStatus` is also called indirectly by `waitForCloudSignIn`; the modules that
  import that polling helper are included in the status row.
- Nine helpers return untyped `res.json()` values. They are marked explicitly rather
  than assigning a DTO that the current code does not declare.

## Bootstrap and workspaces

| Method and endpoint | API helper / request DTO | Response DTO | Consumer modules |
|---|---|---|---|
| GET `/v1/health` | `getHealth()` | `Health {status, default_workspace, model, protocolVersion, capabilities}` | `App` |
| GET `/v1/workspaces/recent` | `getRecentWorkspaces()` | `RecentWorkspace[]` | `App`<br>`components/FolderGate` |
| POST `/v1/workspaces/pick` | `pickFolderViaServer()` | `string \| null` | `tauri` (dynamic import) |
| POST `/v1/workspaces/open` | `openWorkspace(path, create)` | inline `{path, ok, error?, git_branch?, command_trust?}` | `components/FolderGate` |
| GET `/v1/workspaces/trusted` | `getTrustedWorkspaces()` | `WorkspaceCommandTrust[]` | `components/SettingsView` |
| POST `/v1/workspaces/trust` | `setWorkspaceTrusted(path, trusted)` | `WorkspaceCommandTrust & {ok, error?}` | `components/SettingsView`<br>`components/WorkspaceTrustPrompt` |

## Sessions, messages, artifacts, and roots

| Method and endpoint | API helper / request DTO | Response DTO | Consumer modules |
|---|---|---|---|
| POST `/v1/sessions/{sessionId}/revert` | `revertSession(sessionId, index)` | `{ok, error?, text?}` | `App` |
| PATCH `/v1/sessions/{sessionId}` | `setReasoningEffort(sessionId, effort)` | `{ok, error?, reasoning_effort?}` | `App` |
| GET `/v1/sessions?workspace=...` | `getSessions(workspace?)` | `SessionInfo[]` | `App`<br>`components/InboxConfigure`<br>`components/PersonasTab` |
| GET `/v1/sessions/{sessionId}/messages` | `getSessionMessages(sessionId)` | `ConversationMessage[]` | `App` |
| PATCH `/v1/sessions/{sessionId}` | `renameSession(sessionId, title)` | `{ok, error?}` | `App` |
| PATCH `/v1/sessions/{sessionId}` | `setSessionFlags(sessionId, flags)` | `{ok, error?}` | `App` |
| DELETE `/v1/sessions/{sessionId}` | `deleteSession(sessionId)` | `{ok, error?}` | `App` |
| GET `/v1/sessions/{sessionId}/artifacts` | `getArtifacts(sessionId)` | `ArtifactInfo[]` | `App`<br>`components/RightRail` |
| GET `/v1/sessions/{sessionId}/artifacts/read?path=...` | `readArtifact(sessionId, path)` | `ArtifactContent` | `components/RightRail` |
| POST `/v1/sessions/{sessionId}/artifacts/reveal` | `revealArtifact(sessionId, path, mode)` | `{ok, error?}` | `components/RightRail` |
| GET `/v1/sessions/{sessionId}/roots` | `getRoots(sessionId)` | `RootInfo[]` | `useRoots` |
| POST `/v1/sessions/{sessionId}/roots` | `addRoot(sessionId, path, writable)` | `{ok, error?, roots?: RootInfo[]}` | `useRoots` |
| DELETE `/v1/sessions/{sessionId}/roots?path=...` | `removeRoot(sessionId, path)` | `{ok, error?, roots?: RootInfo[]}` | `useRoots` |

## MCP servers

| Method and endpoint | API helper / request DTO | Response DTO | Consumer modules |
|---|---|---|---|
| GET `/v1/mcp` | `getMcpServers()` | `McpServer[]` | `components/ManageTabs` |
| POST `/v1/mcp` | `addMcpServer(name, config)` | **undeclared** (`res.json()`) | `components/ManageTabs` |
| PATCH `/v1/mcp/{name}` | `patchMcpServer(name, changes)` | **undeclared** (`res.json()`) | `components/ManageTabs` |
| DELETE `/v1/mcp/{name}` | `deleteMcpServer(name)` | **undeclared** (`res.json()`) | `components/ManageTabs` |
| GET `/v1/mcp/{name}/tools` | `getMcpTools(name)` | `{ok, error?, tools: {name, description}[]}` | `components/ManageTabs` |
| POST `/v1/mcp/reload` | `reloadMcp()` | **undeclared** (`res.json()`) | `components/ManageTabs` |
| POST `/v1/mcp/{name}/connect` | `connectMcp(name)` | `{ok, started?}` | `components/ManageTabs` |
| POST `/v1/mcp/{name}/signout` | `signoutMcp(name)` | `{ok}` | `components/ManageTabs` |

## Cloud, connectors, audit, browser, and UI settings

| Method and endpoint | API helper / request DTO | Response DTO | Consumer modules |
|---|---|---|---|
| POST `/v1/cloud/telemetry` | `setCloudTelemetry(enabled)` | `{ok, telemetry_enabled?}` | — (no production caller found) |
| GET `/v1/cloud/status` | `getCloudStatus()` | `CloudStatus` | `components/AccessSection`<br>`components/AutomationQuickstart`<br>`components/connectors/CloudSignIn` (via `waitForCloudSignIn`)<br>`components/connectors/ConnectorsSection`<br>`components/GalleryModal`<br>`components/Onboarding`<br>`components/Sidebar` |
| POST `/v1/cloud/login` | `cloudLogin()` | `{ok}` | `components/AutomationQuickstart`<br>`components/connectors/CloudSignIn`<br>`components/GalleryModal`<br>`components/Onboarding`<br>`components/Sidebar` |
| POST `/v1/cloud/logout` | `cloudLogout()` | `{ok}` | `components/Sidebar` |
| POST `/v1/connectors/{name}/connect-managed` | `connectManaged(name, options?)` | `{ok, error?}` | `components/AutomationQuickstart`<br>`components/connectors/AccountsDetail`<br>`components/connectors/AddConnectionModal`<br>`components/connectors/CalendarDetail`<br>`components/connectors/GmailDetail`<br>`components/ManageTabs`<br>`components/Onboarding` |
| POST `/v1/connectors/{name}/mcp-connect` | `connectMcpBacked(name)` | `{ok, error?}` | `components/connectors/AddConnectionModal`<br>`components/ManageTabs` |
| GET `/v1/connectors` | `getConnectors()` | `Connector[]` | `components/AccessSection`<br>`components/AutomationQuickstart`<br>`components/connectors/AddConnectionModal`<br>`components/connectors/ConnectorsSection`<br>`components/InboxConfigure`<br>`components/InboxView`<br>`components/IntegrationsView`<br>`components/Onboarding`<br>`components/PersonaView`<br>`components/SessionIntro`<br>`components/SubscriptionsChip` |
| POST `/v1/connectors/{name}/connect` | `connectConnector(name, fields)` | `{ok, account?, error?}` | `components/connectors/AddConnectionModal`<br>`components/ManageTabs` |
| POST `/v1/connectors/{name}/disconnect` | `disconnectConnector(name)` | `{ok}` | `components/connectors/ConnectorsSection` |
| PATCH `/v1/connectors/{name}/tools` | `updateConnectorTools(name, enabled)` | `{ok, error?, tools?}` | `components/connectors/ToolsDisclosure`<br>`components/ManageTabs` |
| GET `/v1/audit?...` | `getAudit(params)` | `AuditEvent[]` | `components/AuditView` |
| GET `/v1/browser/state` | `getBrowserState()` | `BrowserState` | — (no production caller found) |
| POST `/v1/browser/screenshot` | `takeBrowserScreenshot()` | `BrowserState & {ok?, error?}` | — (no production caller found) |
| POST `/v1/browser/close` | `closeBrowser()` | `{ok?, error?}` | — (no production caller found) |
| POST `/v1/settings/pdf` | `setPdfSettings(patch)` | `Partial<PdfSettings> & {ok, error?}` | `components/SettingsView` |
| POST `/v1/settings/compaction` | `setCompactionSettings(patch)` | `{ok, error?}` | `components/SettingsView` |
| POST `/v1/attachments/inspect-pdf` | `inspectPdf(dataUrl)` | `{ok, pages?, bytes?, error?}` | `components/Composer` |
| POST `/v1/settings/context-bar` | `setContextBar(shown)` | `{ok, context_bar?, error?}` | `components/SettingsView` |
| POST `/v1/settings/sessions-peek` | `setSessionsPeek(n)` | `{ok, sessions_peek?, error?}` | `components/SettingsView` |
| POST `/v1/settings/scratch-base` | `setScratchBase(path)` | `{ok, error?, scratch_base?}` | `components/SettingsView` |
| POST `/v1/settings/surfaces` | `setSurfaces(flags)` | `{ok, surfaces: SurfaceVisibility}` | — (no production caller found) |
| POST `/v1/settings/nav-layout` | `setNavLayout(layout)` | `{ok, nav_layout?, error?}` | `components/Sidebar` |

## Personas and session connections

| Method and endpoint | API helper / request DTO | Response DTO | Consumer modules |
|---|---|---|---|
| GET `/v1/personas` | `getPersonas()` | `Persona[]` | `App`<br>`components/GalleryModal`<br>`components/InboxView`<br>`components/PersonasTab`<br>`components/Sidebar` |
| POST `/v1/personas/{id}` | `updatePersona(id, body)` | `{ok, personas?: Persona[], error?}` | `components/PersonasTab` |
| DELETE `/v1/personas/{id}` | `deletePersona(id)` | `{ok, personas?: Persona[], error?}` | `components/PersonasTab` |
| GET `/v1/cloud/gallery` | `getCloudGallery()` | `{ok, personas: GalleryPersona[], error?}` | `components/GalleryModal` |
| GET `/v1/cloud/gallery/{slug}` | `getCloudGalleryDetail(slug)` | `GalleryDetail` | `components/GalleryModal` |
| POST `/v1/personas/install` | `installPersona(body)` | `{ok, consent?: PersonaConsent[], personas?: Persona[], error?}` | `components/GalleryModal`<br>`components/PersonasTab` |
| GET `/v1/personas/{id}` | `getPersonaDetail(id)` | `PersonaDetail` | `components/PersonaView` |
| POST `/v1/personas/{id}/connections` | `setPersonaConnection(id, connector, enabled)` | `{ok, default_connections?: PersonaDefaultConnection[], error?}` | `components/PersonaView` |
| POST `/v1/personas/{id}/enable` | `setPersonaEnabled(id, enabled)` | `{ok, personas?: Persona[], error?}` | `components/PersonaView` |
| GET `/v1/sessions/{sessionId}/connections?persona=...` | `getSessionConnections(sessionId, persona?)` | `SessionConnections` | `components/AccessSection`<br>`components/SessionIntro` |
| POST `/v1/sessions/{sessionId}/connections` | `setSessionConnection(sessionId, connector, enabled, clear)` | `{ok, error?}` | `components/AccessSection` |

## Skills

| Method and endpoint | API helper / request DTO | Response DTO | Consumer modules |
|---|---|---|---|
| GET `/v1/skills?workspace=...` | `listSkills(workspace?)` | `SkillRow[]` | `components/SkillsTab` |
| POST `/v1/skills` | `createSkill(body)` | `{ok, error?}` | `components/SkillsTab` |
| PATCH `/v1/skills/{name}` | `updateSkill(name, patch)` | `{ok, error?}` | `components/SkillsTab` |
| POST `/v1/skills/{name}/reveal` | `revealSkill(name)` | `{ok, error?}` | `components/SkillsTab` |
| DELETE `/v1/skills/{name}?workspace=...` | `deleteSkill(name, workspace?)` | `{ok, error?}` | `components/SkillsTab` |
| POST `/v1/skills/{name}/move` | `moveSkill(name, scope, workspace?)` | `{ok, error?}` | — (no production caller found) |
| POST `/v1/skills/upload` | `stageSkillUpload(dataB64, filename)` | `SkillUploadPreview` | `components/SkillsTab` |
| POST `/v1/skills/upload/confirm` | `confirmSkillUpload(token, scope, workspace?)` | `{ok, error?}` | `components/SkillsTab` |
| GET `/v1/sessions/{sessionId}/skills?workspace=...` | `sessionSkills(sessionId, workspace?)` | `SessionSkillRow[]` | `components/Composer` |
| POST `/v1/sessions/{sessionId}/skills` | `setSessionSkill(sessionId, skill, enabled, opts)` | `{skills?: SessionSkillRow[], ok?, error?}` | — (no production caller found) |

## Inbox, routing, subscriptions, and unattended mode

| Method and endpoint | API helper / request DTO | Response DTO | Consumer modules |
|---|---|---|---|
| GET `/v1/inbox?session_id=...&state=...` | `getInbox(sessionId?, state?)` | `InboxItem[]` | `App`<br>`components/InboxView` |
| POST `/v1/inbox/{id}/resolve` | `resolveInboxItem(id, resolution)` | `{ok}` | `App`<br>`components/InboxView` |
| GET `/v1/subscriptions` | `getSubscriptions()` | `Subscription[]` | `components/AccessSection`<br>`components/connectors/GithubDetail`<br>`components/connectors/SlackDetail`<br>`components/InboxConfigure`<br>`components/ManageTabs` |
| GET `/v1/inbox/routing` | `getInboxRouting()` | `InboxBinding[]` | `components/InboxConfigure`<br>`components/InboxView` |
| POST `/v1/inbox/routing/binding` | `setInboxBinding(name, channel, target)` | `{ok, bindings?: InboxBinding[], error?}` | `components/InboxConfigure` |
| GET `/v1/unrouted` | `getUnrouted()` | `UnroutedItem[]` | `components/InboxConfigure`<br>`components/InboxView` |
| GET `/v1/channels/recent` | `getRecentChannels()` | `RecentChannel[]` | `components/AccessSection`<br>`components/AutomationQuickstart`<br>`components/InboxConfigure`<br>`components/InboxView`<br>`components/SubscriptionsChip` |
| POST `/v1/subscriptions` | `subscribeChannel(sessionId, channel)` | `{ok, channel?, error?}` | `components/AccessSection`<br>`components/InboxConfigure`<br>`components/SubscriptionsChip` |
| POST `/v1/subscriptions/remove` | `unsubscribeChannel(sessionId, channel)` | `{ok, removed?}` | `components/AccessSection`<br>`components/connectors/GithubDetail`<br>`components/connectors/SlackDetail`<br>`components/InboxConfigure`<br>`components/ManageTabs`<br>`components/SubscriptionsChip` |
| GET `/v1/sessions/{sessionId}/unattended` | `getUnattended(sessionId)` | `boolean` | `App` |
| POST `/v1/sessions/{sessionId}/unattended` | `setUnattended(sessionId, unattended)` | `{ok, unattended}` | `App` |

## Model settings, memory, and providers

| Method and endpoint | API helper / request DTO | Response DTO | Consumer modules |
|---|---|---|---|
| GET `/v1/settings` | `getSettings()` | `ModelSettings` | `App`<br>`components/Composer`<br>`components/ManageTabs`<br>`components/ModelChecklist`<br>`components/SettingsView`<br>`components/Sidebar` |
| POST `/v1/settings/model-key` | `setModelKey(apiKey)` | `{ok, error?, has_key?, source?}` | — (no production caller found) |
| POST `/v1/settings/default-model` | `setDefaultModel(model)` | `{ok, error?, model?}` | `components/ModelChecklist`<br>`providers/ProviderSetup` |
| POST `/v1/settings/models/add` | `addModel(model)` | `ModelSettings & {ok, error?}` | `components/ModelChecklist` |
| POST `/v1/settings/models/remove` | `removeModel(model)` | `ModelSettings & {ok}` | `components/ModelChecklist` |
| POST `/v1/settings/onboarded` | `setOnboarded(value)` | `{ok, onboarded}` | `components/Onboarding`<br>`components/SettingsView` |
| POST `/v1/settings/language` | `setLanguage(language)` | `Partial<ModelSettings> & {ok, language?}` | `App` |
| GET `/v1/memory` | `getMemory()` | `MemoryEntry[]` | `components/MemorySection` |
| PATCH `/v1/memory/{id}` | `updateMemory(id, content)` | `{ok, error?}` | `App`<br>`components/MemorySection` |
| DELETE `/v1/memory/{id}` | `deleteMemory(id)` | `{ok, error?}` | `App`<br>`components/MemorySection` |
| DELETE `/v1/memory` | `deleteAllMemory()` | `{ok, deleted}` | `components/MemorySection` |
| GET `/v1/memory/settings` | `getMemorySettings()` | `MemorySettings` | `components/MemorySection` |
| PUT `/v1/memory/settings` | `setMemorySettings(patch)` | `MemorySettings` | `components/MemorySection` |
| GET `/v1/providers` | `getProviders()` | `ProviderInfo[]` | `providers/ProviderSetup` |
| GET `/v1/protocols` | `getProtocols()` | `ProviderProtocol[]` | `providers/ProviderSetup` |
| POST `/v1/providers` | `setProvider(name, fields)` | `{ok, error?, provider?, recommended_model?}` | `providers/ProviderSetup` |
| DELETE `/v1/providers/{name}` | `removeProvider(name)` | `{ok, error?}` | `providers/ProviderSetup` |
| POST `/v1/providers` | `createCustomProvider(alias, protocol, fields)` | `{ok, error?, provider?, protocol?, recommended_model?}` | `providers/ProviderSetup` |
| DELETE `/v1/providers/{alias}` | `removeCustomProvider(alias)` | `{ok, error?}` | — (no production caller found) |
| POST `/v1/providers/fetch` | `fetchModels(name, fields)` | `{ok, error?, alias?, models?, added?}` | `providers/ProviderSetup` |
| POST `/v1/providers/verify` | `verifyProvider(name, fields)` | `{ok, error?}` | `providers/ProviderSetup` |

## Messaging route and automations

| Method and endpoint | API helper / request DTO | Response DTO | Consumer modules |
|---|---|---|---|
| GET `/v1/messaging/dm-route` | `getDmRoute()` | `string \| null` | `components/InboxConfigure` |
| POST `/v1/messaging/dm-route` | `setDmRoute(sessionId)` | `{ok, dm_session}` | `components/InboxConfigure` |
| GET `/v1/automations` | `getAutomations()` | `Automation[]` | `components/ScheduledView`<br>`components/Sidebar` |
| POST `/v1/automations/{id}/seen` | `markAutomationSeen(id)` | `{ok}` | `components/ScheduledView` |
| POST `/v1/automations` | `createAutomation(payload)` | `{ok, error?, task?: Automation}` | `components/ScheduledView` |
| GET `/v1/automations/{id}` | `getAutomation(id)` | `{task: Automation, runs: AutomationRun[]}` | `components/ScheduledView` |
| PATCH `/v1/automations/{id}` | `updateAutomation(id, changes)` | **undeclared** (`res.json()`) | `components/ScheduledView` |
| DELETE `/v1/automations/{id}` | `deleteAutomation(id)` | **undeclared** (`res.json()`) | `components/ScheduledView` |
| POST `/v1/automations/{id}/run` | `runAutomation(id)` | `PreparedRun` | `App` |
| POST `/v1/automations/{id}/runs/{runId}/finalize` | `finalizeAutomationRun(id, runId)` | **undeclared** (`res.json()`) | `App` |

## Connector account and authorization details

| Method and endpoint | API helper / request DTO | Response DTO | Consumer modules |
|---|---|---|---|
| POST `/v1/connectors/{name}/allow` | `allowUser(name, userId, teamId?, displayName?)` | **undeclared** (`res.json()`) | `components/connectors/SlackDetail`<br>`components/ManageTabs` |
| GET `/v1/connectors/slack/workspaces/{teamId}/directory?q=...` | `getSlackDirectory(teamId, q)` | `{ok, error?, members?: SlackMember[]}` | `components/connectors/SlackDetail` |
| GET `/v1/connectors/slack/workspaces/{teamId}/channels?q=...` | `getSlackChannels(teamId, q)` | `{ok, error?, channels?: SlackChannelEntry[]}` | `components/SubscriptionsChip` |
| POST `/v1/connectors/{name}/unauthorized/{itemId}` | `resolveUnauthorized(name, itemId, action)` | `{ok, error?}` | `components/connectors/GithubDetail`<br>`components/connectors/SlackDetail`<br>`components/ManageTabs` |
| POST `/v1/connectors/{name}/disallow` | `disallowUser(name, userId, teamId?)` | **undeclared** (`res.json()`) | `components/connectors/GithubDetail`<br>`components/connectors/SlackDetail`<br>`components/ManageTabs` |
| POST `/v1/connectors/slack/approval-owners/add` | `addSlackApprovalOwner(userId, displayName?)` | `{ok, error?}` | `components/connectors/SlackDetail` |
| POST `/v1/connectors/slack/approval-owners/remove` | `removeSlackApprovalOwner(userId)` | `{ok, error?}` | `components/connectors/SlackDetail` |
| POST `/v1/connectors/slack/workspaces/{teamId}/disconnect` | `disconnectSlackWorkspace(teamId)` | `{ok, error?, remaining_workspaces?}` | `components/connectors/SlackDetail` |
| POST `/v1/connectors/gmail/accounts/{email}/disconnect` | `disconnectGmailAccount(email)` | `{ok, error?, remaining_accounts?}` | `components/connectors/GmailDetail` |
| POST `/v1/connectors/gmail/accounts/{email}/default` | `setGmailDefaultAccount(email)` | `{ok, error?}` | `components/connectors/GmailDetail` |
| POST `/v1/connectors/google_calendar/accounts/{email}/disconnect` | `disconnectGcalAccount(email)` | `{ok, error?, remaining_accounts?}` | `components/connectors/CalendarDetail` |
| POST `/v1/connectors/google_calendar/accounts/{email}/default` | `setGcalDefaultAccount(email)` | `{ok, error?}` | `components/connectors/CalendarDetail` |
| POST `/v1/connectors/{connector}/accounts/{accountId}/disconnect` | `disconnectAccount(connector, accountId)` | `{ok, error?, remaining_accounts?}` | `components/connectors/AccountsDetail` |
| POST `/v1/connectors/{connector}/accounts/{accountId}/default` | `setDefaultAccount(connector, accountId)` | `{ok, error?}` | `components/connectors/AccountsDetail` |
| PATCH `/v1/connectors/gmail/filters` | `setGmailFilters(filters)` | `{ok, filters?: GmailFilters, error?}` | `components/connectors/GmailDetail` |
| GET `/v1/connectors/github/status` | `getGithubStatus()` | `GithubStatus` | `components/connectors/GithubDetail` |
| POST `/v1/connectors/github/installations/{installationId}/disconnect` | `disconnectGithubInstallation(installationId)` | `{ok, error?, remaining_installs?}` | `components/connectors/GithubDetail` |
| POST `/v1/connectors/hubspot/portals/{hubId}/disconnect` | `disconnectHubSpotPortal(hubId)` | `{ok, error?, remaining_portals?}` | `components/connectors/HubSpotDetail` |
| POST `/v1/connectors/hubspot/portals/{hubId}/default` | `setHubSpotDefaultPortal(hubId)` | `{ok, error?}` | `components/connectors/HubSpotDetail` |
| PATCH `/v1/connectors/hubspot/hidden-fields` | `setHubSpotHiddenFields(fields)` | `{ok, hidden_fields?, error?}` | `components/connectors/HubSpotDetail` |
| GET `/v1/connectors/slack/status` | `getSlackStatus()` | `SlackStatus` | `components/connectors/ConnectorsSection` |

## Direct `fetch` matrix

| Scope | File and location | Endpoint | Classification |
|---|---|---|---|
| Production transport | `src/api.ts:23-30` | all local REST endpoints | Expected centralized wrapper; adds the launch token. |
| Production callers outside transport | none | none | No direct `fetch`, `globalThis.fetch`, XHR, axios, or `EventSource` found. |
| E2E test | `e2e/automations-quickstart.spec.ts:69` | `POST /v1/cloud/login` | Test-only browser-context call; not shipped production behavior. |
| Live-E2E helper | `e2e-live/helpers.ts:29` | dynamic `${BACKEND}${path}` | Test harness transport; not imported by production UI. |

## DTO gaps observed by this inventory

The following response values have no declared TypeScript return DTO in the current
API module: `addMcpServer`, `patchMcpServer`, `deleteMcpServer`, `reloadMcp`,
`updateAutomation`, `deleteAutomation`, `finalizeAutomationRun`, `allowUser`, and
`disallowUser`. This inventory records the gap only; defining or migrating those DTOs
belongs to later contract work and is outside UI-001.
