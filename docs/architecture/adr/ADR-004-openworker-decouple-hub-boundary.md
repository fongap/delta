# ADR-004: OpenWorker Cloud 解耦 + Delta Hub 架构边界

- Status: Active
- Date: 2026-09-02
- Supersedes: `UPSTREAM.md` 旧"主动同步 OpenWorker" 流程中的运行时依赖部分
- See-also: `ADR-003-provider-protocol-model.md`

## Context

经过 PR #67 的 Provider 协议收敛（OpenAI-compatible / Anthropic Messages），Delta Desktop 仍然深度绑定 OpenWorker Cloud 平台：

1. **运行时依赖 `api.openworker.com`**: `integrations/cloud.py` 通过 `config.cloud_base_url`（默认 `https://api.openworker.com`）调用 `/v1/me`、`/v1/oauth/{provider}/start`、`/v1/oauth/{provider}/refresh`、`/v1/connections/{id}/disconnect`、`/v1/relay/{github,slack}/{disconnect,uninstall}`、`/v1/github/token`、`/v1/telemetry/events`、`/v1/personas/gallery/*`。
2. **Auth0 集成**: `config.cloud_auth_domain` (`opencoworker.us.auth0.com`) + `config.cloud_client_id` + `config.cloud_audience` (`https://api.opencoworker.app`) 通过 Auth0 Universal Login 完成 PKCE 登录。
3. **OpenWorker-managed Relay**: `config.cloud_relay_ws_url` + Slack/GitHub App 安装走 `RelayHub` → `SlackRelayAdapter` / `GitHubRelayAdapter`，token provider 当前必须传 `_relay_token() -> cloud.fresh_access_token()`，**这是 OpenWorker Cloud JWT 唯一驱动的实现**。
4. **Telemetry**: `cloud.emit_session_created` (Opt-in，仅登录且开关开才发) + `install_id` (per-install 随机 ID 持久化到 SecretStore)。
5. **GitHub App installation token**: `cloud.github_installation_token` 完全从 broker 拉，本地不存。
6. **Persona Gallery**: 走 `/v1/personas/gallery/*`，需要 sign-in 才能用。
7. **Upstream Sync Workflow**: `.github/workflows/upstream-sync.yml` 自动镜像 `andrewyng/openworker:main` 到 `upstream-openworker` 分支。`UPSTREAM.md` 第 22-40 行将其定性为 "Delta 必须与上游保持镜像同步"。这与任务书 §23 "Delta 是独立项目，不依赖 OpenWorker 上游同步" 冲突。

虽然 `cloud:auth` profile 仅在用户主动点 "Sign in" 后才有，但 **存在** 路径让 Desktop 隐式访问 `api.openworker.com`。**这正是任务书 §1 禁止的运行时依赖方向**。

## Audit Summary (阶段 0)

### 4.1 Cloud 调用链 (实际)

```text
Desktop UI (apps/desktop/src/Sidebar.tsx, Onboarding.tsx, SettingsView.tsx, AccessSection.tsx, GalleryModal.tsx)
   │  cloudLogin / cloudLogout / getCloudStatus / setCloudTelemetry / getCloudGallery / connectManaged
   ▼
HTTP /v1/cloud/{login,logout,status,telemetry,gallery,connect-managed}  (services/server/app.py:1061-1292)
   │  import integrations.cloud as cloud
   ▼
integrations/cloud.py (690 行)
   │  httpx → config.cloud_base_url = "https://api.openworker.com"
   │  Auth0 domain "opencoworker.us.auth0.com" PKCE flow
   │  client_id "g1l4Q1lhYWmyS03qPSf4KEJGrgq02Qam"
   │  audience  "https://api.opencoworker.app"
   ▼
   ├── Auth0 (身份联邦)
   ├── /v1/oauth/{provider}/{start,refresh}        managed OAuth broker
   ├── /v1/connections/{id}/disconnect              远端 metadata 同步
   ├── /v1/relay/{github,slack}/...                 managed relay 控制面
   ├── /v1/github/token                             GitHub App installation token mint
   ├── /v1/telemetry/events                         遥测
   ├── /v1/me                                       user profile
   └── /v1/personas/gallery/*                       persona gallery
```

### 4.2 Connector 调用链 (实际)

```text
Connector (integrations/connectors/*.py)
   ├── Local / Manual  ←── 保持原样: PAT paste / Slack Socket Mode bot token / 浏览器 OAuth flow
   │                      (descriptors.py 标记 managed=False, managed_paused=False)
   │
   └── Managed (relay + OAuth)
        ├── Managed OAuth (cloud.begin_managed_connect → /v1/oauth/{provider}/start)
        │     仅在 profile.get("managed") == True 时生效
        │     manual paste profile 永远不被 cloud 触碰
        │
        ├── Managed Relay (mode == "relay")
        │     ├── SlackRelayAdapter   ← integrations/connectors/relay_client.py
        │     ├── GitHubRelayAdapter  ← integrations/connectors/github_relay.py
        │     └── 共用 RelayHub
        │           └── token_provider: Callable[[], str] 当前唯一实现 = cloud.fresh_access_token (OpenWorker JWT)
        │
        └── GitHub App installation
              ├── token mint  ← cloud.github_installation_token → /v1/github/token
              └── routing rows ← /v1/relay/github/disconnect
```

### OpenWorker 引用文件分布 (按命中数)

| 命中数 | 路径 | 性质 |
|---|---|---|
| 43 | `integrations/cloud.py` | OpenWorker Cloud client 核心 |
| 39 | `tests/test_memory_api.py` | `/v1/memory` 路径 (false positive,无关) |
| 21 | `tests/test_cloud.py` | OpenWorker 单元测试 |
| 14 | `services/server/app.py` | cloud/telemetry/managed/relay 路由 |
| 14 | `tests/test_cloud_server.py` | 路由级测试 |
| 12 | `packages/config.py` | `cloud_base_url` / `cloud_auth_domain` / `cloud_client_id` / `cloud_audience` / `cloud_relay_ws_url` 字段 |
| 10 | `tests/test_cloud_server.py` (telemetry) | telemetry 路由测试 |
| 8 | `apps/desktop/src/api.ts` | TS API 客户端调用 `/v1/cloud/*` |
| 5 | `services/server/manager_events.py` | `emit_session_created` 触发点 |
| 5 | `services/server/manager_gateway.py` | relay URL + `_relay_token` wiring |
| 5 | `apps/desktop/tests/e2e/fixtures.ts` | e2e cloud state 模拟 |
| 4 | `apps/desktop/src/components/SettingsView.tsx` | telemetry toggle UI |
| 2 | `apps/desktop/src/components/Sidebar.tsx` | cloud sign-in row |

### Connector 当前状态（阶段 0 探查）

- Gmail / Google Calendar / Google Drive / Outlook / HubSpot / Notion / Attio:
  - 均有 `managed` 模式（cloud begin_managed_connect）— 任务书 §9.1 要求 manual 保留，OpenWorker managed 删除，future managed 抽象成 capability port。
  - 均有 `manual` 模式（`gmail_accounts.connect_account` 等直接 `secrets.put`），与 cloud 完全解耦。
- Slack:
  - `mode == "socket"` = `SlackAdapter` + bot/app token（manual）。
  - `mode == "relay"` = `SlackRelayAdapter`，**当前 token_provider 强制绑 cloud JWT**（任务书 §9.2 要求拆）。
- GitHub:
  - `mode == "pat"` = `GitHubPatAdapter`（manual，完全本地）。
  - `mode == "relay"` = `GitHubRelayAdapter`，installation_token 走 `cloud.github_installation_token`（任务书 §9.3 要求拆）。
  - 仍保留 `GitHubAppBroker` interface（实际尚未独立成 capability port 类）。

## Decision

### D-1. 拆 Cloud runtime dependency

- 删除 `integrations/cloud.py` 中所有 `httpx` 请求路径（sign-in, telemetry, gallery, managed OAuth, managed token refresh, GitHub installation token, relay routing）。
- 删除 `packages/config.py` 的 5 个 `cloud_*` 字段以及 `Config` 默认值。
- 删除 `services/server/app.py` 中所有 `/v1/cloud/*`、`/v1/connectors/*/connect-managed`、`/v1/connectors/*/oauth/callback`、`/oauth/callback` 路由。
- 删除 `services/server/manager_events.py` 中的 `_emit_session_created` 与 telemetry 副作用。
- 删除 `services/server/manager_gateway.py` 中对 `cloud.fresh_access_token` / `cloud.github_installation_token` / `cloud_relay_ws_url` 的 wiring。
- 删除 `apps/desktop/src/api.ts` 中 `cloudLogin` / `cloudLogout` / `getCloudStatus` / `setCloudTelemetry` / `connectManaged` / `getCloudGallery*` 与 `CLOUD_CHANGED` 事件。
- 删除 `apps/desktop/src/components/Sidebar.tsx` 的 cloud sign-in row。
- 删除 `apps/desktop/src/components/Onboarding.tsx` 的 cloud sign-in step。
- 删除 `apps/desktop/src/components/SettingsView.tsx` 的 telemetry toggle。
- 删除 `apps/desktop/src/components/AccessSection.tsx` 的 cloud status。
- 删除 `apps/desktop/src/components/GalleryModal.tsx` 整文件。
- 删除 `apps/desktop/src/components/ManageTabs.tsx` 中所有 `cloudLogin` / `getCloudStatus` / `setCloudTelemetry` 调用。
- 删除 `apps/desktop/src/components/AutomationQuickstart.tsx` 中所有 cloud sign-in 提示。
- 删除 `apps/desktop/src/components/SubscriptionsChip.tsx` 中 cloud 引用。
- 删除 `apps/desktop/src/components/PersonasTab.tsx` 中 cloud gallery 引用。
- 删除 `apps/desktop/src/components/SkillsTab.tsx` 中 cloud 引用。
- 删除 `tests/test_cloud.py`、`tests/test_cloud_server.py`、`tests/test_memory_api.py`（cloud 字段 + memory 路径中 `cloud` false positive）。
- 删除 `apps/desktop/tests/e2e/cloud.spec.ts`、`apps/desktop/tests/e2e/cloud-status-pending.spec.ts`、`apps/desktop/tests/e2e/sidebar-account.spec.ts`（cloud 相关 step）。
- 精简 `apps/desktop/tests/e2e/fixtures.ts` 中 `CLOUD_STATE` 注入。

### D-2. 拆 Telemetry 整层

- 删除 `TELEMETRY_PROFILE`、`install_id`、`telemetry_enabled`、`set_telemetry_enabled`、`emit_session_created`。
- 任务书 §8 明确 "不得改名成 Delta Telemetry 继续保留"。未来如需 diagnostics，独立重新设计。

### D-3. 拆 Relay token 平台专属绑定

- `integrations/connectors/relay_client.py` 现有 `RelayTransport` Protocol、`RelayHub` 已是**通用能力**，不删除。
- 唯一平台专属绑定是 `TokenProvider = Callable[[], str]` 当前必须返回 OpenWorker Cloud JWT。
- 阶段 6 将其改名为 `HubTokenProvider`，调用方传入任何来源的 hub token（未来 Delta Hub 派 / 现在保持 None = 不连）。

### D-4. 拆 Connector managed OAuth platform 绑定

- 通用能力：`managed_profile_from_callback` 的字段约定（access_token / refresh_token / connection_id / provider / account）—— 通用，可保留。
- 平台专属：`begin_managed_connect` / `refresh_managed_token` / `cloud_disconnect` 走 `httpx` 到 OpenWorker —— 删除。
- 阶段 6 引入 `OAuthBroker` Protocol（begin / exchange / refresh / disconnect），manual 当前已经走浏览器 OAuth，managed 走 broker；broker 实例默认 `NullBroker` 返回 "managed relay is unavailable because no managed service is configured"。

### D-5. 拆 GitHub App installation token mint 平台专属绑定

- 删除 `cloud.github_installation_token` 与 `/v1/github/token` 调用。
- 引入 `GitHubAppBroker` Protocol: `async get_installation_token(installation_id) -> str`。
- 默认实现 `NullGitHubAppBroker` 返回空串。
- manual 路径：保留 GitHub PAT 完全工作。

### D-6. 建立 Managed Capability Ports (阶段 6)

- 新建 `integrations/managed/` 目录:
  - `identity.py` — `ExternalIdentity(issuer, subject, display_name)` + `ExternalIdentityProvider` Protocol。
  - `oauth.py` — `OAuthBroker` Protocol。
  - `relay.py` — `RelayTransport` / `RelayHub` 已有，迁移到本目录并去掉 cloud 注释。
  - `github_app.py` — `GitHubAppBroker` Protocol。
  - `models.py` — `ExternalIdentity` / `ManagedError` / `ManagedUnavailable`。
  - `errors.py` — `ManagedUnavailableError("managed relay is unavailable because no managed service is configured")`。
  - `__init__.py` — `null_broker()` / `null_relay_hub()` / `null_github_app_broker()` 工厂。
- 默认所有 broker 为 `Null*`：调用方拿不到结果就跳过（与任务书 §9.2 "managed relay 应显式返回 unavailable" 一致）。

### D-7. 配置体系从零重建 (阶段 7)

- `packages/config.py` 5 个 `cloud_*` 字段 → 单一 `managed` 嵌套 dataclass:
  ```python
  @dataclass
  class ManagedConfig:
      enabled: bool = False
      base_url: str = ""
      device_token: str = ""   # 未来 Delta Hub Native Device Token baseline
      relay_ws_url: str = ""   # 可选 managed relay endpoint (空 = 不连)
  ```
- 默认 `enabled=False, base_url="", device_token="", relay_ws_url=""` — 新安装零网络调用。
- 不硬编码任何 `openworker.com` / 维护者私有域。
- `Config.cloud_*` 字段以"无值"缺省：未来 Managed Capability Ports 自取自校验。

### D-8. Federation 边界预留 (阶段 8)

- `UPSTREAM.md` 改为 "已停止 active sync；`upstream-openworker` 分支删除或标记 archived；不与 OpenWorker 维持镜像关系"。
- 删除 `.github/workflows/upstream-sync.yml` 整文件。
- 未来 Federation Adapter 位置在 `integrations/federation/openworker/`（仅接口，不实现）：
  - `__init__.py` — 写明 "Optional Federation Adapter — Delta does NOT depend on this at runtime; remove the whole directory to drop OpenWorker support without affecting Delta Hub or Delta Desktop。"
  - `oauth.py` — `OpenWorkerOAuthBroker(OAuthBroker)` 占位 raise `NotImplementedError`。
  - `relay.py` — `OpenWorkerRelayTransport(RelayTransport)` 占位 raise。
  - `github_app.py` — `OpenWorkerGitHubAppBroker(GitHubAppBroker)` 占位 raise。
  - `README.md` — 描述何时启用 / 如何删除。

### D-9. 文档同步

- `UPSTREAM.md` 改写为 "Delta is an independent project derived from OpenWorker (MIT attribution preserved). No automated upstream sync; no runtime dependency on OpenWorker Cloud. OpenWorker may optionally surface as a Federation Adapter in the future."
- 新建 `docs/architecture/hub-federation-boundary.md` — 描述 Delta Hub Native Device Token baseline + 未来 optional OpenWorker Federation Adapter 位置 + §14.1 推荐结构。
- `CHANGELOG.md` 写一条 "OpenWorker Cloud runtime dependency removed; Cloud sign-in / Telemetry / Managed Relay / Managed OAuth / GitHub App Token Mint / Persona Gallery all removed; Local / Manual paths preserved; Managed Capability Ports introduced as the future Delta Hub baseline." 归属 `[Unreleased]` / `Removed`。
- 历史 `pyright-report.before.json` 涉及 `bedrock_provider.py` / `vertex_provider.py` 字串 —— 旧 pyright baseline 文件，PR #67 已生成 `pyright-report.json` 现版本。**阶段 9 二次扫描中应一并删除 `pyright-report.*.json` 历史文件**。

## Consequences

### 正面

- Delta Desktop **完全脱离 OpenWorker Cloud 运行时依赖**（任务书 §18 离线验证可过）。
- Provider 协议 + Connector manual 路径 + Local OAuth/Socket Mode/PAT 全部保留。
- 为未来 Delta Hub 建立 `OAuthBroker` / `RelayTransport` / `GitHubAppBroker` / `ExternalIdentityProvider` capability port 边界（任务书 §10）。
- 未来可选 OpenWorker Federation Adapter 不影响任何现有代码（任务书 §14）。
- 删除 OpenWorker active sync workflow，Delta 明确为独立项目（任务书 §23）。

### 负面

- `cloud_*` 字段、cloud sign-in UI、telemetry、Persona Gallery、Slack/GitHub managed relay 全部移除（任务书 §7、§8、§9.2、§9.3 一致要求）。
- 大量 UI / API / 测试 / 路由需同步删除（涉及 ~30+ 文件）。
- 部分 connector 暂时回到 manual-only；未来 Delta Hub 上线后可重新打开 managed 路径。

### 风险

- **Slack GitHub managed 多工作区能力暂时丢失**：manual Socket Mode 仍能单工作区工作。任务书 §9.2 明确接受该 tradeoff。
- **CI / e2e fixture 中 `CLOUD_STATE` 需清理**：影响 `cloud.spec.ts` / `cloud-status-pending.spec.ts` / `sidebar-account.spec.ts` / 部分 e2e step。e2e 测试是允许失败的，但 desktop-e2e 是 required gate（PR #67 ci-required 包含）—— 阶段 10 必须解决。
- **Provider 治理延续**: PR #67 已完成；本 ADR 不触碰 provider 子系统。

## File Inventory (待改动文件)

| 类别 | 文件 |
|---|---|
| 删除 | `integrations/cloud.py`, `apps/desktop/src/components/GalleryModal.tsx` |
| 删除 | `tests/test_cloud.py`, `tests/test_cloud_server.py` |
| 删除 | `apps/desktop/tests/e2e/cloud.spec.ts`, `apps/desktop/tests/e2e/cloud-status-pending.spec.ts`, `apps/desktop/tests/e2e/sidebar-account.spec.ts` |
| 删除 | `.github/workflows/upstream-sync.yml` |
| 删除（清理） | `pyright-report.before.json` |
| 大量修改 | `services/server/app.py`（routes）, `services/server/manager_events.py`, `services/server/manager_gateway.py` |
| 大量修改 | `packages/config.py`（cloud_* 字段） |
| 大量修改 | `apps/desktop/src/api.ts`, `apps/desktop/src/components/{Sidebar,Onboarding,SettingsView,AccessSection,ManageTabs,AutomationQuickstart,SubscriptionsChip,PersonasTab,SkillsTab}.tsx` |
| 修改 | `integrations/connectors/adapters.py`（拆 relay token 绑定） |
| 修改 | `integrations/connectors/relay_client.py`（迁移至 managed/） |
| 修改 | `integrations/connectors/github_relay.py`（token 改 broker） |
| 修改 | `integrations/connectors/github_installs.py`（installation token 改 broker） |
| 修改 | `integrations/connectors/{gmail_accounts,gcal_accounts,outlook_accounts,hubspot_portals,notion_accounts,attio_accounts,slack_accounts,slack_addr,descriptors,setup}.py`（managed 模式拆 broker） |
| 新建 | `integrations/managed/{__init__,identity,oauth,relay,github_app,models,errors}.py` |
| 新建 | `integrations/federation/openworker/{__init__,oauth,relay,github_app,README}.md`（占位 / 不实现） |
| 新建 | `docs/architecture/hub-federation-boundary.md` |
| 改写 | `UPSTREAM.md` |
| 改写 | `CHANGELOG.md`（[Unreleased]） |

## Verification (阶段 10 实施时执行)

- `uv run --locked pytest tests -q` 期望：除 e2e 不跑外，1331+ 通过
- `uv run --locked pyright core providers integrations packages services/server apps/tui` 期望：0 errors
- `uv run --locked ruff check .` 期望：All checks passed
- `cd apps/desktop && npm test` + `npx tsc --noEmit` 期望：全部通过
- `cd apps/desktop && npm run e2e` 期望：删 cloud.spec.ts / cloud-status-pending.spec.ts 后剩余 e2e 全绿
- Rust CI（apps/desktop/src-tauri, packaging/portable/launcher, services/stt）期望：全绿
- 离线验证（任务书 §19）：阻断 `api.openworker.com` / `opencoworker.us.auth0.com` / 任意 `cloud_*` endpoint 后，Desktop 启动 + 跑通一个 session 完整
- 二次扫描（任务书 §22）：`rg "api\.openworker\.com|opencoworker|cloud_auth_domain|cloud_client_id|cloud_audience|cloud_relay_ws_url|cloud:auth|OpenWorker Cloud"` 运行时代码 = 0 命中（仅允许在 `UPSTREAM.md` / `CHANGELOG.md` / `LICENSE` / `docs/architecture/hub-federation-boundary.md` / `integrations/federation/openworker/*` 占位）
