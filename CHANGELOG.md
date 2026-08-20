# Changelog

本项目所有显著变更以 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式记录。版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增 (Added)

#### 2026-08-20 08:14

- **原生窗口标题栏跟随应用主题**
  - 新增 `set_native_theme` Tauri 命令（封装 `WebviewWindow::set_theme`）：Webview 的 `data-theme` 只影响网页内容，此前在 Delta 深色模式下原生标题栏仍随系统主题；现由前端 `theme.ts` 的 `apply()` 在初始化、手动切换及 auto 跟随系统时一并下推深浅，使 Windows DWMWA 标题栏、macOS 窗口外观与界面一致。浏览器构建下该命令无 shell 可调，自然回退为空操作。

#### 2026-08-19 14:07

- **Delta Windows 便携版（DeltaPortable）打包**
  - 新增 `packaging/build_portable.ps1`：按现有 `build_windows.ps1` 流程构建服务器 sidecar 与 Tauri 应用（`tauri build --no-bundle`），将已构建的根启动器嵌入为 `Delta.exe`，组装可整体移动的 `App/Data/Other/AppInfo` 目录结构，并产出可重新分发的 ZIP + SHA-256。
  - 新增 `packaging/build_portable.ps1` 二进制名解析：构建的可执行名取自 `Cargo.toml`（`[package] name`，如 `openworker-desktop`），而非 `tauri.conf.json` 的 `productName`（Delta），并在 `App\Delta\` 中按 productName 改名落地。
  - 新增 `packaging/scan_portable_paths.ps1` 作为发布门禁：扫描打包树中所有文本与二进制字符串，检出构建机的绝对路径/源码路径泄漏（如 `C:\...`、repo 根目录），命中即构建失败，确保便携版完全可重定位。
  - 便携版经多位置实测验证可整体重定位：`C:\DeltaPortable\`、`D:\Portable Apps\DeltaPortable\`、中文+空格+特殊字符路径 `G:\AI工具\深层 目录 & 测试(1)\子目录-嵌套_更多\Delta 工作助手(改名&测试)\` 下均正常启动；状态/密钥/日志/数据库全部落在 `<ROOT>\Data\`，未触碰 `%APPDATA%`，与开发/安装模式数据隔离。

### 变更 (Changed)

#### 2026-08-20 10:20

- **默认受信任工作区由 `~/OpenWorker` 改为 `~/Delta`**
  - 会话 scratch 目录默认根路径 `DEFAULT_SCRATCH_BASE` 由 `~/OpenWorker` 改为 `~/Delta`（`coworker/server/manager.py`），同步更新 docstring、设置测试断言与 e2e 夹具路径；按用户要求不做既有数据迁移。
  - 随后更新 `helpers.ts` 回退值与 `e2e/fixtures.ts` 中的 workspace/PRIMARY_ROOT 路径。

- **内部服务器进程 `openworker-server` 更名为 `delta-server`**
  - 服务器 sidecar 进程、二进制、入口点与配套脚本统一更名为 `delta-server`：`pyproject.toml [project.scripts]` 入口 `delta-server`、PyInstaller spec（`packaging/openworker-server.spec` → `packaging/delta-server.spec`，保留 git 历史）、`build_windows.ps1` / `build_dmg.sh` / `build_portable.ps1` 的进程终止与产物路径、`lib.rs` 的 sidecar 定位与日志文件名、`run.py` 的 `prog`、README / setup_dev_env live 命令。
  - 云端连通标识与数据兼容标识按要求保留不变：`X-OpenWorker-Token` 请求头、`openworker` WebSocket 子协议、`coworker:*` 事件名、`com.openworker.desktop` identifier、`coworker` 状态目录、`openworker` / `openworker-connectors` CLI 入口、`coworker` Python 包名 —— OpenWorker Cloud 连接不受影响。
  - 5 处 `e2e-live` 跳过提示同步为 “start delta-server”。

#### 2026-08-20 06:47

- **Voice Input 本地模型切换为多语言 Whisper Base**
  - 默认语音模型由仅英文的 `ggml-base.en.bin` 换为多语言 `ggml-base.bin`（147,951,465 字节，SHA-256 已更新），转写时不再强制 `language=en`，改为自动检测语种，中文等非英语语音可直接转写。
  - 同步更新模型名展示（"Whisper Base (local)"）与 GUI 提示文案/测试夹具中的字节数。

#### 2026-08-20 06:17

- **Composer 报批开关同行布局 + Settings 路径按钮配色**
  - Composer ModeMenu 的「发送到收件箱」开关（unattended Toggle）从原独占一行的标签-描述-开关垂直布局改为：开关与「发送到收件箱」标签同行，说明文字单独另起一行；降低紧凑度符合报批模式的同行交互预期。
  - Settings · 文件卡「选择文件夹」浏览按钮配色由边框式（`BTN_BORDERED`）改为 accent 强调式 (`bg-accentSoft text-accent border-accent`)，使其与同行「保存」主键按钮视觉一致，hover 不透明度反馈。

#### 2026-08-20 05:24

- **Composer 模式菜单文案本地化**
  - 三种权限模式（讨论 / 审批 / 自动）的标签与描述由硬编码英文回退改为 i18n key（`access.mode.discuss` / `interactive` / `auto` 及其 `Desc` 后缀），zh 值分别为「讨论模式—仅讨论，不执行」「审批模式—执行前需获得批准」「自动模式—无需批准，自动执行全部操作」。

#### 2026-08-20 05:20

- **Access 展示文案本地化（"Access" → "访问权限"）**
  - 右侧栏 Access 区块标题由硬编码英文回退文案改为 i18n key `access.sectionTitle`，zh 值为「访问权限」（此前缺 key 时回退英文 "Access"），与 `connectors.access` "访问权限" 一致。

#### 2026-08-20 05:08

- **侧边栏底部账户行改为四图标操作**
  - 侧边栏底部由单一账户行改为四个统一图标：收件箱（Inbox）、活动（Activity）、登录（Sign-in）、设置（Settings），各带一对一 hover 提示。
  - 登录图标承载账户菜单：已登录 → 点击打开账户菜单（邮箱身份、Connectors 入口、退出登录）；未登录 → 点击直接触发 Delta Cloud 登录。
  - 收件箱图标保留待办计数徽章；Inbox、Activity、Settings 不再出现在账户菜单内，均为底部直达图标。Automations 仍为侧栏一级导航行。
  - 同步迁移约 35 个 e2e 用例至新 testid 与结构（`sidebar-footer-inbox` / `sidebar-footer-activity` / `sidebar-footer-settings` / `nav-automations`；移除 `account-sign-in`、`inbox-chip` 旧标识）。

#### 2026-08-20 03:54

- **全局搜索入口移至顶部工具栏**
  - 搜索入口从侧边栏（自动化与设置之间）移到顶部工具栏右侧、与 Delta 品牌同行的位置；默认仅显示放大镜图标，点击展开为输入框并自动聚焦，点击外部/Esc 自动收起，宽/窄屏下位置一致。
  - 新增 `TopbarSearch` 组件，展开后输入并回车打开命令面板（`SearchModal`）；侧边栏搜索按钮及其独立 `SearchModal` 实例已移除，侧栏折叠时搜索依然可达。

#### 2026-08-19 22:08

- **侧边栏 / 输入区 / 文件 / 受信任工作区 / 设置 / 更新模块 UI 汉化**
  - 将 Sidebar、Composer、Files、Trusted workspaces、Settings（Voice、Sidebar、Composer、Files、Trusted workspaces、Update、PDF、Compaction 等卡片）及 Update 模块的硬编码英文文案统一收敛到集中式 i18n 字典（`en.ts` / `zh.ts`），组件内改为 `t()` 调用，缺失键回退英文。
  - 新增 `access.folderCount`、`settings.workspace.allowanceCount` 等带运行时插值（`{n}`）的键，随界面语言切换动态翻译。

#### 2026-08-19 20:16

- **品牌色调与标识统一**
  - 品牌色 `--brand` 由钴蓝 `#2563eb` 改为 logo 背景色 `#286f78`（浅/深双主题一致），logo/品牌标识统一为 teal。
  - 托盘图标由 44×44 黑色 monochrome 模板图改为 32×32 彩色品牌 logo（同一 `assets/logo` 下采样），去掉 `icon_as_template(true)`，托盘与桌面图标一致。
  - 去除全部 4 处 BETA 徽章（titlebar、启动页、onboarding、sidebar）及 `.beta-tag` 样式。

- **“新建会话”更名为“新任务”**
  - 新建动作可见文案统一为“New task / 新任务”：侧边栏新建按钮、顶栏新建按钮（aria-label/title）、标题栏回退文案；下拉菜单“Start a session as / 以以下身份开始会话”→“Start as / 选择身份开始”；同步 `nav.newChat` 与 Slack 说明图。
  - 内部 session/conversation 术语、后端与数据库标识保持不变。

#### 2026-08-19 03:08

- **OpenWorker 品牌全面替换为 Delta**
  - 用户可见品牌统一为 Delta：窗口标题、托盘菜单与提示、Sidebar、设置页、onboarding、连接器展示文案、自动化/计划任务状态文案、loopback 登录页与后端服务端提示（en/zh 双语言字典同步）；"OpenWorker BETA" → "Delta BETA"，"OpenWorker Cloud" → "Delta Cloud"。
  - Persona 显示名统一走 `fullPersonaName`/`shortPersonaName`（管理页、Sidebar 会话过滤弹层等）：内置 coworker 显示为 "Coworker"（去除品牌残影），"Ops Coworker"/"Code Coworker" 保持全称。
  - 应用图标全面替换：以 `assets/logo` 为唯一来源重新生成 `src-tauri/icons/*`（含托盘 monochrome 模板图标）。
  - 打包与应用元数据：Release 工件与安装器稳定名（Windows setup/msi、macOS dmg/app.tar.gz）改用 Delta；`Info.plist`、Cargo.toml、`build_dmg.sh`、`make_update_manifest.py` 同步。
  - 自动更新：updater `endpoints` 置空——Delta 尚无自有更新源，不再指向 download.openworker.com。
  - 内部标识按 §11/§12 保留：`com.openworker.desktop` identifier、`X-OpenWorker-Token`、状态目录等未改，保持数据兼容与上游同步（`openworker-server` 进程名已于 2026-08-20 更名为 `delta-server`，见下方「变更」条目）。

#### 2026-08-19 01:22

- **连接器 About/Access 展示文案迁入 i18n**
  - catalog ABOUT/ACCESS 逐条汉化：新增 49 个 `connectors.<name>.about` / `connectors.<name>.access` key（en/zh 各 49）。
  - ACCESS 列表以 `\n` 连接存于单 key，渲染层 `accessLines` 拆行——任一 bullet 变动时整块回退后端英文，避免按索引 key 的错位风险。
  - 后端 `catalog_copy.py` 机器数据源未动，前端缺 key 时仍显示后端英文原文（§8 回退语义）。

#### 2026-08-19 00:56

- **OpenWorker 硬编码文案收拢与汉化（CP4 Provider/Connector + CP5 状态字符串）**
  - Provider/Connector 展示文案迁移至现有 i18n 字典：新增 46 个 `providers.*.blurb` / `connectors.*.blurb` key，四处渲染点（ProviderSetup、AvailableDetail、AccessSection、ConnectorsList）通过 `t(key, vars, fallback)` 回退解析——后端仍下发英文原文，前端缺 key 时展示原文。
  - Provider 测试失败消息（H2）本地化：新增 8 个 `providers.*` 错误 key，`localizeVerifyMsg` 仅映射已知机器诊断字符串的展示；后端机器可读的 reason/code 保持原样，未知消息原样透传。
  - 自动化任务运行状态/触发器字符串（H6）渲染层本地化：新增 7 个 `scheduled.status.*` / `scheduled.trigger.*` key，`runStatus`/`runTrigger` 映射已知值、未知值透传；`TaskRun.status`（running/ok/error/skipped）与 `AutomationRun.trigger`（schedule/manual/catchup）等机器标识在线缆与内部比较中保持原样（§2.1/§11）。

#### 2026-08-17 21:56

- **全局 UI/UX 重构（Delta 混合设计系统）** — 仅涉及 `surfaces/gui` 的表现层与交互层：
  - 设计 Token 统一：界面主色由钴蓝 `#2563eb` 迁移至灰蓝 `#4A6572`（深色主题 `#8FA6B5`），确立「灰蓝 · 安静的力量」品牌气质。
  - 新增配套 Token：`--accent-hover`、`--on-accent`、`--focus`（键盘焦点环）、`--brand`（保留 Delta 商标蓝 `#2563eb` 用于 Logo/品牌标识）。既有 Token 语义按规范映射，未做批量改名。
  - 收编 17 个组件中的硬编码颜色为设计 Token：`bg-accent text-white` → `text-onAccent`；`bg-green-*/text-green-*`、`bg-red-*/text-red-*` → `ok/ok-soft`、`danger/danger-soft` 等。
  - 修复 `AutomationQuickstart.tsx` 中未定义的 `line2` 工具类 → `line`。

### 修复 (Fixed)

#### 2026-08-20 06:29

- **Voice Input 系统信息在中文 Windows 乱码**
  - `voice_input_compatibility` 读取 Windows 版本号（`cmd /C ver`）时，对 `from_utf8_lossy` 直接解码 OEM 字节（中文 Windows 下为 GBK/CP936），导致「版本」→ `·本§` 乱码；改为 `encoding_rs::GBK.decode` 广播解码，版本号为纯 ASCII 字段不受影响，`device_summary` 在中/英文 Windows 下均显示正常。
  - 新增 `encoding_rs` 依赖。

#### 2026-08-20 03:54

- **顶栏右侧面板切换按钮点击无响应**
  - 右侧操作区（`.main-topbar-actions`）的窗口拖拽 `onPointerDown` 抢先触发原生窗口拖动，使产物/侧栏面板切换按钮的 `onMouseDown` 拦截失效；改用 `onPointerDown` 停止冒泡，与折叠导航簇的既有模式一致。

#### 2026-08-19 22:13

- **补全 `skills.install` 缺失 i18n key**
  - SkillsTab 上传确认按钮（"Install skill"）此前引用不存在的 `skills.install` 键，运行时触发 `[i18n] missing key` 警告并回退英文；已在 en.ts/zh.ts 补充（"Install skill" / "安装技能"），随界面语言正常翻译。

#### 2026-08-19 03:40

- **Provider 卡片状态渲染与 e2e/vitest 断言同步**
  - `providers.usedAgo` i18n 值去除多余的 ` · ` 前缀，修复「✓ Connected · · used 2h ago」双分隔符渲染（en/zh 字典同步；`ProviderSetup` 的 JSX 已自带分隔符）。
  - 同步 6 个 e2e 用例与 1 个 vitest 用例的断言到实际渲染文案：`automations-manage`（last Running 大小写）、`automations-quickstart`（Today's 弯引号）、`onboarding`（工具 benefit 文案）、`slack-health`（can't 弯引号）、`unattended` + `Composer.voice`（Send 按钮 label 为 "Send message"）。
  - 均为文案/定位同步，未改任何产品功能或测试语义。

### 修复 (Fixed) / 可访问性

#### 2026-08-17 21:56

- **WCAG AA 对比度**（Phase 7-9 强制作弊审计）：
  - 次要文本 `--faint` 两处不达标已修正：浅色 `#9aa1aa`(2.61) → `#717173`(≥4.5)；深色 `#62686f`(2.96) → `#8a8c8e`(≥4.5)，在 paper/panel 双背景均达标。
  - 其余核心文本、图形、键盘焦点环均达 AA（主文本 ≥15:1，accent/on-accent ≥6:1，焦点环/图形 ≥3:1）。
- **E2E 稳定性**：`nav-collapse ⌘B` 用例在键盘监听挂载前按键被丢弃导致的偶发失败，已加 boot 完成守卫修复（测试代码，非生产逻辑）。

### 说明

- 有意固定的品牌/模拟色（Slack 面板、蜡烛图模拟器红绿灯、persona 品牌章）保持不变。
- 本次为表现层重构，未改动任何 Rust 后端、IPC、DB、Agent 生命周期、Provider、工具执行、审批、权限、密钥、记忆逻辑等业务状态。
