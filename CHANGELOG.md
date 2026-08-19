# Changelog

本项目所有显著变更以 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式记录。版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增 (Added)

#### 2026-08-19 14:07

- **Delta Windows 便携版（DeltaPortable）打包**
  - 新增 `packaging/build_portable.ps1`：按现有 `build_windows.ps1` 流程构建服务器 sidecar 与 Tauri 应用（`tauri build --no-bundle`），将已构建的根启动器嵌入为 `Delta.exe`，组装可整体移动的 `App/Data/Other/AppInfo` 目录结构，并产出可重新分发的 ZIP + SHA-256。
  - 新增 `packaging/scan_portable_paths.ps1` 作为发布门禁：扫描打包树中所有文本与二进制字符串，检出构建机的绝对路径/源码路径泄漏（如 `C:\...`、repo 根目录），命中即构建失败，确保便携版完全可重定位。
  - 便携版行为与 `packaging/portable/launcher` 一致：运行时由 `Delta.exe` 自身位置解析根目录，`COWORKER_STATE_DIR` 指向 `Data\`，密钥/数据/日志全部随文件夹移动，不写注册表、不依赖 `%APPDATA%`。

### 变更 (Changed)

#### 2026-08-19 03:08

- **OpenWorker 品牌全面替换为 Delta**
  - 用户可见品牌统一为 Delta：窗口标题、托盘菜单与提示、Sidebar、设置页、onboarding、连接器展示文案、自动化/计划任务状态文案、loopback 登录页与后端服务端提示（en/zh 双语言字典同步）；"OpenWorker BETA" → "Delta BETA"，"OpenWorker Cloud" → "Delta Cloud"。
  - Persona 显示名统一走 `fullPersonaName`/`shortPersonaName`（管理页、Sidebar 会话过滤弹层等）：内置 coworker 显示为 "Coworker"（去除品牌残影），"Ops Coworker"/"Code Coworker" 保持全称。
  - 应用图标全面替换：以 `assets/logo` 为唯一来源重新生成 `src-tauri/icons/*`（含托盘 monochrome 模板图标）。
  - 打包与应用元数据：Release 工件与安装器稳定名（Windows setup/msi、macOS dmg/app.tar.gz）改用 Delta；`Info.plist`、Cargo.toml、`build_dmg.sh`、`make_update_manifest.py` 同步。
  - 自动更新：updater `endpoints` 置空——Delta 尚无自有更新源，不再指向 download.openworker.com。
  - 内部标识按 §11/§12 保留：`com.openworker.desktop` identifier、`X-OpenWorker-Token`、`openworker-server` 进程名、状态目录等未改，保持数据兼容与上游同步。

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
