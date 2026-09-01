# Changelog

本项目所有显著变更按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 方式记录，版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

CHANGELOG 只记录用户可感知的变化和重要工程能力变化。

具体实现过程、测试数量、调试记录、依赖锁文件变化和单纯代码整理，应保留在 Pull Request、Commit 和 CI 记录中，不再写入 CHANGELOG。


## [Unreleased]

### 移除 (Removed)

- **OpenWorker Cloud 运行时依赖** (ADR-004)
  - 彻底删除 `integrations/cloud.py` (690 行): Auth0 PKCE 登录、/v1/me、managed OAuth broker、telemetry、persona gallery、GitHub installation token mint、relay routing、cloud disconnect。
  - 删除 `packages/config.py` 5 个 `cloud_*` 字段 (`cloud_base_url`, `cloud_auth_domain`, `cloud_client_id`, `cloud_audience`, `cloud_relay_ws_url`)；默认值指向 `api.openworker.com` / `opencoworker.us.auth0.com` 已移除。
  - 删除 `services/server/app.py` 12 个 cloud routes: `/v1/cloud/status`, `/v1/cloud/telemetry`, `/v1/cloud/login`, `/v1/cloud/logout`, `/auth/callback`, `/v1/connectors/*/connect-managed`, `/oauth/callback`, `/v1/cloud/gallery/*`。
  - 删除 `services/server/manager_events.py` telemetry (`emit_session_created`, `install_id`, `telemetry_enabled`)。
  - 删除 `services/server/manager_gateway.py` 对 `cloud.fresh_access_token` / `cloud.github_installation_token` / `cloud_relay_ws_url` 的 wiring；relay token 现默认空串。
  - 删除 `apps/desktop/src/components/GalleryModal.tsx`、`CloudSignIn.tsx`、所有 cloud sign-in / telemetry / managed OAuth UI 引用 (127 处)。
  - 删除 `tests/test_cloud.py`, `tests/test_cloud_server.py`, `tests/test_slack_workspaces.py`, 3 个 e2e spec (`cloud.spec.ts`, `cloud-status-pending.spec.ts`, `sidebar-account.spec.ts`)。
  - 删除 `.github/workflows/upstream-sync.yml` (自动镜像 OpenWorker 上游)；`UPSTREAM.md` 重写为"独立项目，不再同步"。
  - 删除 `pyright-report.before.json` 历史基线。

### 新增 (Added)

- **Managed Capability Ports** (`integrations/managed/`)
  - `OAuthBroker` / `NullOAuthBroker` (begin/exchange/refresh/disconnect)
  - `RelayTransport` / `NullRelayTransport` (open/recv/close)
  - `GitHubAppBroker` / `NullGitHubAppBroker` (get_installation_token/clear)
  - `ExternalIdentityProvider` / `NullIdentityProvider` (verify_assertion)
  - `ExternalIdentity` dataclass (issuer/subject/display_name)
  - `ManagedConfig` (enabled=False, base_url="", device_token="", relay_ws_url="")
  - `ManagedUnavailableError` (用户可见 "managed relay is unavailable because no managed service is configured")
- **Federation Boundary** (`integrations/federation/openworker/`)
  - 占位适配器: `OpenWorkerOAuthBroker`, `OpenWorkerRelayTransport`, `OpenWorkerGitHubAppBroker` (全部 `NotImplementedError`)
  - README 明确: 删除此目录即完全移除 OpenWorker 支持，零影响
- **架构文档**: `docs/architecture/hub-federation-boundary.md` (明确 OpenWorker 仅为可选适配器，Native Device Token 为基座)

### 变更 (Changed)

- `packages/config.py`: 仅保留 `cloud_relay_ws_url` (默认空)，删除 4 个 cloud 字段
- `integrations/connectors/relay_client.py`: `TokenProvider` 重命名为 `HubTokenProvider` 概念，默认空串
- `integrations/connectors/integration_github.py`: relay 模式返回 "managed relay unavailable" 而非调用 cloud
- 所有 connector detail 组件: 移除 cloud sign-in 检查，仅保留 manual connect

### 变更 (Changed)

- **仓库治理体系**


### 变更 (Changed)

- **Provider 路由与凭据模型**
  - Provider 从厂商名称绑定收敛为仅按 OpenAI-compatible 或 Anthropic Messages 协议和 endpoint/profile 路由。
  - OpenAI 与 Anthropic 支持显式 API 地址和对应凭据。
  - 自定义 endpoint 不再继承无关官方服务的 API Key，避免不同服务之间的凭据串用。
  - 本地模型服务、Gemini-compatible 网关等通过自定义 OpenAI-compatible endpoint 接入，不再拥有独立运行时分支。

- **STT 网络与依赖栈**
  - 升级 `ureq`、`sha2`、`cpal` 等 Rust 依赖并完成 API 兼容适配。
  - 保留模型下载取消、超时保护、代理回退和 SHA-256 校验能力。
  - Rust 最低工具链要求随相关依赖升级同步调整。

- **依赖治理**
  - 调整 Python、npm、Rust 和 GitHub Actions 的 Dependabot 策略。
  - 生产依赖继续独立评估，开发依赖可按规则分组。
  - Major 升级和组合升级保持人工审核，避免自动升级掩盖兼容性问题。

- **仓库维护规则**
  - `CONTRIBUTING.md`、`UPSTREAM.md`、架构、治理和运维文档与当前仓库结构重新对齐。
  - 收紧历史路径检查，旧路径仅允许存在于必要历史记录。
  - 品牌资源检查脚本和开发环境说明按当前目录职责重新整理。


### 移除 (Removed)

- **不支持的 Provider 实现**
  - 移除原生 Gemini、AWS Bedrock、Google Vertex 与 Ollama 独立 Provider/协议，以及对应 SDK 依赖、配置和模型目录。
  - Pyright 改为零错误直接门禁，不再使用 errorCount baseline。


### 修复 (Fixed)

- **非 ASCII Artifact 路径**
  - 修复 Markdown Artifact 链接经过 URI 编码后，中文等非 ASCII 文件名无法正确匹配、打开的问题。
  - 非法转义或文件名中的普通 `%` 保持兼容处理。

- **Python 依赖冲突**
  - 调整 Provider SDK 与 `websockets` 等依赖版本约束，解决不同 SDK 对依赖版本要求不一致造成的 CI 安装失败。

- **STT 兼容性**
  - 修复 `cpal` 新版本下麦克风采集接口变化导致的编译问题。
  - 修复 `sha2` 新版本摘要结果不再支持旧格式化方式的问题。


## [0.3.0] - 2026-08-31

### 新增 (Added)

- **工具按需注入**
  - 模型调用不再默认携带全部 Tool Schema。
  - 根据 persona、当前消息和本轮工具使用情况选择必要工具，降低请求体和上下文开销。
  - 核心人在回路、计划和 Skill 能力始终保留。
  - 当工具选择不足时可自动回退到完整工具集，避免工具裁剪导致任务中断。

- **请求可观测性**
  - 增加模型请求日志，记录 Provider、Model、消息规模、Tool 数量、请求体积、上下文估算、TTFT、总耗时和调用结果等关键指标。
  - 观测记录失败不会影响正常模型调用。

- **Endpoint 能力画像**
  - OpenAI-compatible endpoint 不再默认假设支持全部标准参数。
  - 支持记录不同 endpoint 对 `stream_options`、reasoning、并行 Tool Call 和上下文窗口等能力。
  - 对已经确认不支持的参数，后续请求可主动跳过，减少重复失败和无意义重试。

- **工具 Schema 上下文预算**
  - 上下文预算开始同时考虑 Messages 与 Tool Schema。
  - 接近上下文上限时优先缩减工具集合，再根据需要触发消息压缩。


### 变更 (Changed)

- **模型调用负载**
  - 模型调用从“每轮完整 Tool Schema”调整为按任务需要加载，降低无关上下文占用。
  - Explorer 等需要完整工具能力的执行路径仍可保持完整注入。

- **兼容端点参数处理**
  - 参数兼容策略从单纯失败后重试，调整为“显式配置 + 已学习能力 + 失败兜底”的组合方式。


## [0.2.2] - 2026-08-27

### 新增 (Added)

- **Execution Gateway**
  - 建立统一的工具执行安全入口。
  - 对本地写入、外部副作用、高风险操作实施分级审批。
  - 高风险外部操作不再能够通过普通 session 授权或 blanket permission 绕过审批。
  - 无人值守任务在无法获得必要授权时按 fail-closed 处理。

- **资源范围守卫**
  - 带磁盘副作用的工具调用会在执行入口重新验证可信根目录和可写范围。
  - 越界写入不会因上游分类或已有授权而自动放行。

- **Run Event Ledger**
  - 后台进程启动、终止等生命周期事件进入运行记录。
  - 运行记录增加更明确的执行隔离信息，便于后续审计和复盘。

- **Source Layer v1**
  - 文件来源成为可持久化、可追踪的来源记录。
  - 通过内容指纹识别来源变化、缺失和重复捕获。
  - 为后续引用、证据链和来源新鲜度检查建立基础。

- **敏感数据统一脱敏**
  - Audit 与 Run Ledger 共用统一的递归脱敏策略。
  - API Key、Token、Authorization、Cookie、签名参数和敏感正文等信息统一处理。


### 变更 (Changed)

- **Provider 设置体验**
  - 服务商设置按照路由标识、协议、API 地址、API Key、连接测试的顺序重新组织。
  - API 地址从隐藏的高级配置调整为主要配置项。
  - 模型列表、默认模型、显示状态、手动添加模型和危险操作重新整理。
  - 默认模型不能被直接隐藏或删除，避免产生无效配置。

- **RuntimePort 架构**
  - Server 业务层逐步退出对具体 `TurnEngine` 实现的直接依赖。
  - Session 和 Automation 等路径统一通过 Runtime Port 驱动运行时能力。

- **SessionManager 拆分**
  - 原大型 SessionManager 按 Workspace、Session、Event、Connector、Provider、Automation、Artifact 等职责拆分。
  - 对外接口保持兼容。

- **性能边界**
  - 大文件读取避免重复全文件扫描。
  - PDF 预览改为按视口虚拟化。
  - 用户停止生成时主动关闭上游 Provider Stream，减少无效后台下载。

- **自动更新入口**
  - 在正式 updater feed 尚未具备完整发布链路时隐藏无效的自动更新入口。


### 修复 (Fixed)

- **流式响应截断**
  - OpenAI-compatible 流式响应如果在没有合法完成标记的情况下结束，将明确判定为截断。
  - 不再把只收到少量字符的残缺响应误认为完整回答。
  - 用户主动停止生成不会被误判为服务端截断。

- **模型发送按钮**
  - 修复发送按钮与回车发送条件不一致导致按钮无法点击的问题。

- **Excel 日期预览**
  - XLSX 预览能够识别 Excel 日期、时间和日期时间格式。
  - 日期型单元格不再显示原始 Excel 序列号。

- **Provider 默认模型一致性**
  - 删除当前默认模型时改为明确拒绝，避免默认模型指向不存在的配置。

- **安全授权边界**
  - 修正外部消息发送等 L3 操作的 standing rule 和审批行为，避免 session 级授权错误扩散到高风险操作。


### 安全 (Security)

- **Sidecar Token 隔离**
  - Sidecar 根 Token 从 WebView 渲染进程退出。
  - Tauri 本地反向代理负责 REST Header 和 WebSocket 子协议注入。
  - Web 内容不再直接持有 Sidecar Token。

- **HTML Artifact 隔离**
  - HTML Artifact 预览使用更严格的 sandbox。
  - 关闭暴露给页面脚本的全局 Tauri Bridge，降低不受信 Artifact 影响桌面权限的风险。

- **ACL 降级检测**
  - SecretStore 和 Workspace Trust 写入后验证实际文件权限。
  - ACL 保护失败时产生显式降级标记和警告，而不是静默继续。

- **CI 供应链硬化**
  - 第三方 GitHub Actions 固定到不可变 Commit SHA。
  - 增加 Python、npm 和 Rust 依赖安全公告检查。
  - Ruff 和 Pyright 建立静态分析回归门，防止新增技术债。


## [0.2.1] - 2026-08-24

### 变更 (Changed)

- **GitHub Actions 发布链路**
  - Windows Portable Release 收敛为从 `main` 手动触发。
  - 发布前统一检查 Python、npm、Tauri、Portable Launcher 和 CHANGELOG 版本一致性。
  - Release 先创建 Draft，上传资产并重新下载验证后再正式发布。
  - 重复执行可继续未发布 Draft；版本、Tag 或 Commit 冲突时安全终止。

- **发布版本统一**
  - Python、Desktop、Tauri 和 Portable Launcher 的版本号进入统一发布检查流程。


### 修复 (Fixed)

- **异步事件循环阻塞**
  - OAuth Token 刷新、Telegram 等同步网络操作移出主异步事件循环，避免网络异常导致整个 Server 暂停响应。

- **Automation 重叠执行**
  - 修正定时任务 overlap guard 在并发边界下可能同时派生重复任务的问题。


### 移除 (Removed)

- **旧 MSVC Portable 构建脚本**
  - 删除包含本机绝对路径且已经被当前 Portable 构建流程取代的旧辅助脚本。


## [0.2.0] - 2026-08-24

### 新增 (Added)

- **Windows Portable 发布基线**
  - 建立以 `Delta-Windows-Portable.zip` 和 SHA-256 文件为核心的 Windows Portable 发布方式。
  - Portable 数据保存在应用目录下，可整体移动，不依赖固定安装路径。
  - ZIP 统一使用单个 `Delta/` 顶层目录。
  - 构建过程增加绝对路径泄漏扫描和实际启动 Smoke Test。

- **UI Runtime Contract v1**
  - 建立 Desktop UI 与 Server 之间的核心 DTO 和事件契约。
  - Session 与应用级 WebSocket 事件统一使用版本化包络。
  - Health、HTTP Error 和核心 Runtime Event 建立明确兼容边界。
  - WebSocket 异常断开支持自动重连，并通过 sequence 抑制重复事件。

- **会话级思考深度**
  - 每个 Session 可以独立设置默认、低、高、最大等 reasoning effort。
  - 设置持久化，并在下一次模型调用中实际生效。

- **自定义模型服务商**
  - 模型设置支持用户自行添加 Provider。
  - 可以选择兼容协议、配置 API 地址与 API Key，并拉取可用模型。
  - 自定义 Provider 配置可以持久化并参与正常模型路由。

- **消息编辑与撤回**
  - 已发送的人类消息可以编辑。
  - 编辑时回退该消息之后的对话历史，并允许重新发送。

- **多语言本地语音输入**
  - 本地 Whisper 模型由英语专用版本切换为多语言版本。
  - 支持自动识别中文等非英语语音。


### 变更 (Changed)

- **无预设模型**
  - Delta 不再内置默认 Provider 或默认 Model。
  - 首次使用由用户自行配置模型服务商和默认模型。

- **Provider 设置**
  - 自定义 Provider 成为模型配置的主要入口。
  - Provider Alias、协议、API 地址和模型列表的职责进一步明确。

- **默认工作区**
  - 默认受信任工作区从旧 OpenWorker 路径调整为 Delta 自有路径。
  - 不对既有用户数据进行自动迁移。

- **内部 Server 命名**
  - Desktop Sidecar 进程和发布产物逐步统一为 `delta-server`。
  - 为兼容既有 Cloud/Runtime 协议，部分 OpenWorker/Coworker 协议标识暂时保留。

- **Windows Portable Launcher**
  - 根 `Delta.exe` 收敛为 Bootstrapper。
  - 负责环境和数据目录初始化后启动 Desktop，GUI 与 Sidecar 生命周期继续由 Desktop 管理。

- **主题与界面**
  - 原生窗口标题栏跟随应用明暗主题。
  - 调整侧边栏、搜索入口、Composer、模型设置、消息元数据和空状态等桌面交互。
  - 完善中文界面与 i18n 文案。


### 修复 (Fixed)

- **Portable PowerShell 构建**
  - 修复 PowerShell 5.1 将 PyInstaller/Tauri 输出到 stderr 的正常日志误判为构建失败的问题。

- **Portable 路径兼容**
  - 修复 Windows `AppData` 等路径可能被搜索排除规则误判的问题。
  - Portable 在空格、中文和特殊字符路径下保持可运行。

- **聊天布局**
  - 修复长文本、URL、代码块和表格导致主聊天区域被撑宽的问题。
  - 代码块和宽表格仅在自身区域横向滚动。

- **Reasoning 状态持久化**
  - 修复会话重载或 WebSocket 重连后 reasoning effort 回到默认值的问题。

- **附件处理**
  - Picker、拖放和粘贴统一附件验证规则。
  - 对类型不支持、大小超限、数量超限、重复和读取失败给出明确反馈。

- **Provider 错误展示**
  - 可重试 Provider 错误改为更清晰的本地化摘要。
  - 原始基础设施错误默认折叠但仍可查看和复制。

- **模型与设置交互**
  - 修复自定义 Provider 表单加载、深色模式下 Select 可读性、Provider 状态展示和相关文案问题。

- **Desktop 原生交互**
  - 修复窗口拖拽区抢占按钮事件导致侧栏、搜索等操作无法点击的问题。
  - 修复原生标题栏首次显示时可能短暂出现错误主题的问题。

- **可访问性**
  - 调整浅色与深色主题中的次要文本颜色，使核心界面对比度达到 WCAG AA 要求。


### 移除 (Removed)

- **旧发布链路**
  - 移除不再使用的 MSI、NSIS、macOS DMG 和旧 updater 发布脚本及相关资产。
  - 当前发布链路收敛到 Windows Portable。

- **旧设计和构建资产**
  - 清理未使用的 UI 原型、重复 Logo、旧构建日志、缓存和中间产物。
