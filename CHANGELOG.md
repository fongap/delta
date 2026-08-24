# Changelog

本项目所有显著变更以 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式记录。版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 移除 (Removed)

#### 2026-08-24 20:03

- **废弃 MSVC 便携构建辅助脚本**
  - 移除 `packaging/_build_portable_msvc.cmd`：该脚本硬编码了本机旧仓库路径（`D:\900 AIWork\910 GitHub\delta\packaging`）与个人 Python 目录，仓库内零引用，且已被 `packaging/build_portable.ps1` 完全取代。

### 修复 (Fixed)

#### 2026-08-24 20:29

- **事件循环阻塞修复（稳定/修复）**
  - 修复 managed relay 重连路径上的一次性阻塞：token 刷新（OAuth refresh）是同步 httpx 请求（最长 15s）且在异步 `open()` 内直接调用，若云不可达会让整个服务端（所有 WS 会话、广播、调度）在每次重连周期冻结长达 15s。现将 token 获取改为 `asyncio.to_thread` 下放，与 `github_installation_token` 的既有做法一致。
  - 修复 Telegram 出站同样的事件循环阻塞：`TelegramAdapter.send` 直接调用同步 `_send_telegram`（`httpx`，30s 超时），慢/不可达的 api.telegram.org 会冻结整个服务端。现改为 `asyncio.to_thread` 下放（Slack 适配器此前已做同样处理，Telegram 被遗漏）。
- **本地状态 JSON 存储的原子写与容错加载**
  - 新增 `coworker/_jsonstate.py`（`load_json_state` / `save_json_state`）：临时文件 + `os.replace` 原子写入；加载对损坏/截断文件不再抛 `JSONDecodeError`，而是回退到空状态并将损坏文件保留为 `.corrupt-<ts>` 以便恢复。
  - 应用到全部本地状态存储（`inbox`、`mentions`、`unrouted`、`unattended`、`connections`（persona/session 两层）、`inbox_routing`、`selfwake`），避免一次崩溃留下截断 JSON 导致下次启动 sidecar 直接失败、桌面应用打不开。
- **会话持久化的性能与稳定性**
  - `ConversationStore` 增加每会话的 JSONL 行数缓存：追加型 `.jsonl` 只在需要时全量读一遍，之后的每次 save（含每个 turn 检查点）不再 O(历史长度) 重读整文件，长会话下广播/检查点延迟不再随对话增长。
- **MCP 连接取消的孤儿泄漏**
  - `MCPManager.ensure` 在等待连接建立时若调用方被取消，先前会在连接建立后因未注册而让 `_serve` 永久驻留在 `shutdown.wait()`，遗留一个无人管理的活动连接；现改为取消时同时取消 `_serve` 任务，让 `AsyncExitStack` 正常拆掉底层传输。
- **无界增长的小泄漏**
  - `InboxStore` 每 resolve 一项后移除其 `asyncio.Event` 等待器（原先只 set 不删，逐条泄漏）；会话删除时清理 `_autotitle_attempts` 计数（原先只增不减）。

#### 2026-08-24 19:44

- **侧边栏行菜单与 e2e 测试同步**
  - 修复侧边栏每行 ⋮ 菜单的真实产品缺陷：菜单高度硬编码 `MENU_H=150`，但自“推理深度”子区(标签 + 4 档)加入后菜单实际高约 300px。靠近窗口底部的行翻转菜单时，`top = r.top - 150` 把 Delete 等底部项渲染到视口外，导致无法点击。现将高度常量修正为 `300` 并在向上翻转时对齐菜单底部到锚点行上沿，避免末项落到屏幕外。
  - 同步更新 9 个 e2e 规格以匹配当前 UI：侧边栏 footer 的 Inbox 现在唯一存在于 footer 图标（非导航行）、账户菜单二次点击的幂等处理、Slack 连接器“Sign-in needed”状态通过强制状态测试、加工作区弹窗的已登录流程、转录消息元信息的当前结构、以及“Send approvals to Inbox”切换的 i18n 文案。

#### 2026-08-24 15:22

- **自动化调度器重复执行**
  - 修复调度循环的竞态：一个 run 尚未完成保存（next_run 仍是旧值）时，后续 tick 读到过期的到期快照并在 overlap 守卫释放后再次 spawn，导致同一任务在同一次触发窗口内被执行两次（生产中表现为自动化重复发消息/重复执行）。
  - 现在 `_tick` 在派生前同步预占 overlap 守卫（检查与添加之间无 await），过期快照会被正确跳过；手动触发路径 `run_task` 的跳过语义保持不变。已用复现脚本（修复前约 2/15 失败率，修复后 200 次迭代全部通过）及完整测试套件验证。

## [0.2.0] - 2026-08-24

### 变更 (Changed)

#### 2026-08-24 06:38

- **便携包名去除版本号**：产物统一为 `Delta-Windows-Portable.zip` + `.sha256`，构建脚本与 Release 工作流同步。

#### 2026-08-24 02:54

- **0.2.0 发布基线与 Windows 便携版唯一发布链路**
  - Python、npm、Tauri 与便携启动器版本统一为 `0.2.0`；已验证的 Python 运行时、测试和消息/Bedrock 可选依赖改为精确版本，`aisuite` 保留上游不可变 commit 以维持当前 Agent/runtime 修复集。
  - 默认本地发布入口与 GitHub Actions 收敛为 Windows portable-only：最终仅生成 `releases/Delta-Windows-Portable.zip` 及其 `.sha256`；旧 MSI、NSIS、DMG 与 updater 发布脚本、素材已移除。
  - 便携构建的中间目录移至 `packaging/build`/`packaging/dist`，最终 ZIP 严格只含单个 `Delta/` 顶层目录；launcher 每次从当前源码重建，避免复用旧版本资源。
  - 修复显式选择位于 Windows `AppData` 下的工作区时 ripgrep 系统目录排除规则误伤整个搜索根的问题；生成目录仍按原规则排除。
  - 发布验证通过：后端 `1197 passed, 3 skipped`，前端 `151 passed`、TypeScript 检查和生产构建通过；ZIP 校验和、绝对路径泄漏扫描、launcher/GUI/sidecar 启停与便携数据目录 smoke 均通过。

### 移除 (Removed)

#### 2026-08-24 07:06

- **旧发布与设计资产清理**
  - 移除已退出发布链路的 MSI/NSIS、macOS DMG 和 updater manifest 脚本及 DMG 素材。
  - 移除旧 OpenCoworker UI 原型、未引用的说明图与重复尺寸 Logo；保留 README 与当前便携构建实际使用的源资产。

### 新增 (Added)

#### 2026-08-23 23:06

- **UI Runtime Contract 第二批（UI-005、UI-006、UI-008、UI-015、UI-016）**
  - 建立后端 Pydantic 与前端维护型契约层，明确 session/message/approval/artifact/model 五类核心 DTO 的必需字段、默认值和 additive-fields 规则。
  - 当前全部 session/app-wide WebSocket 出站事件原子切换为唯一 v1 包络 `type/version/sessionId/sequence/payload`；协议与事件版本均固定为 1，无会话 app-wide 事件使用 `sessionId: null`，前端只解析此格式。
  - sidecar token `401` 原子切换为唯一 `code/message/details/retriable` HTTP 错误包络；业务 2xx `{ok:false,...}` 响应不纳入本阶段。
  - 增加后端 schema/事件端到端测试及前端默认值、额外字段、禁用字段 fixture 测试；当前源码 build 与 PyInstaller sidecar 启动 smoke 通过，未迁移其余 REST 业务响应或 Provider 管理接口。

#### 2026-08-23 22:11

- **UI Runtime Contract 第一批（UI-001～UI-004、UI-007）**
  - 完成 GUI REST、WebSocket/SSE 边界盘点与 UI 领域边界 ADR；保持 Delta 本地实现、Provider 扩展和桌面运行方式不变。
  - `/v1/health` 增加必需的 `protocolVersion` 与 `capabilities`；GUI 要求当前 protocol v1，未知 capability、额外字段及未知/畸形事件产生去重诊断并被忽略。
  - 增加后端协商字段测试和前端严格 bootstrap/事件降级测试，不拆仓、不移动组件、不改变用户可见流程。

#### 2026-08-22 07:10

- **`build_portable.ps1` 步骤 3 的 PowerShell 5.1 stderr 陷阱修复**
  - `npm run tauri build` 内部的 `@tauri-apps/cli` 将 info 行（"Info Looking up installed tauri packages…"）输出到 stderr；宿主捕获 stderr 时，PowerShell 5.1 把每行包装为 ErrorRecord，与全局 `$ErrorActionPreference="Stop"` 叠加会让 `--no-bundle` 在首条 info 行即终止构建（exit 1，实际是 `NativeCommandError` 误判）。已将错误偏好收敛到该调用局部、仅以退出码判定成败，与步骤 1 的 PyInstaller 修复同一模式。

#### 2026-08-22 06:31

- **便携版顶层目录与启动器图标**
  - 便携版 ZIP 改为单一顶层 `Delta/` 目录（解压即用、可整体移动），归档内不再出现散落的 `./Delta.exe`/`./App` 条目。
  - 根启动器 `Delta.exe` 通过 build-time `winres` 内嵌 Delta 图标（`icon.ico`），资源管理器/任务栏显示真实应用图标而非通用占位；winres 为纯构建期依赖，不改动启动器的运行时依赖面。
  - `build_portable.ps1` 步骤 1 修复：PyInstaller 的进度日志经 stderr 输出、被宿主捕获时在 PowerShell 5.1 下被包装为 ErrorRecord，与全局 `$ErrorActionPreference="Stop"` 叠加会让 `--clean` 每次都把首个 INFO 行误判为失败；已将错误偏好收敛到该调用局部、仅以退出码判定成败（`--clean` 前保持 `$ErrorActionPreference="Stop"` 的全局行为不变）。

### 修复 (Fixed)

#### 2026-08-24 06:38

- **会话与模型设置体验五项**
  - 人类消息 hover 元数据右对齐（末元素对齐气泡右缘），并移除 Provider/模型前缀，仅保留时间与编辑/复制操作。
  - 设置 ▸ Models：「你的服务商」网格前置；「自定义服务商」卡片改为可折叠，已有服务商时默认收起。
  - 思考深度触发器样式与模型选择一致（无边框透明、hover 显背景），档位文案更新为默认/轻量/深度/最大及对应描述；chip 仅显示档位名。
  - 会话输入框空态高度提高至两行（约 62px），最大仍为四行。
  - 模型切换提示本地化：后端 notice 增加结构化 `model`/`image_warning` 字段，前端按语言渲染「模型已切换为 {model}」；旧会话回退原文。

#### 2026-08-24 01:37

- **会话推理、自定义服务商、附件与重试体验修复**
  - 修复 `reasoning_effort` 在引擎保存、WebSocket 断开及会话列表重载后回退为 `auto`；Composer 模型选择器旁新增会话级「思考深度」入口，PATCH 成功后立即同步本地状态，下一条消息按当前档位发送。
  - 自定义服务商以 alias 作为注册表、卡片和详情页主标题，协议降为辅助信息；统一创建/编辑表单中文文案，alias 明确只读，创建成功或切换表单时清空已拉取模型与状态，并支持删除后重建。
  - picker、拖拽和粘贴统一使用同一附件策略：仅接受图片、PDF、文本/代码，明确报告不支持、超限、超过 8 个、总 payload 超限、重复、空内容及读取失败；前后端入口上限和数据 URL 校验对齐，发送前再次校验，附件拒绝不再写入模型 transcript。
  - Provider 可重试错误改为本地化摘要，原始基础设施详情默认折叠且可复制；重试中显示当前尝试状态，普通警告保持原文，不被误标为服务商故障。
  - 新增 reasoning 持久化、Provider alias/重置、附件契约、错误重试与布局回归；相关后端 90 项、前端 151 项测试、生产构建和 7 项 Playwright 用例通过。

#### 2026-08-24 00:27

- **聊天布局回归、标题/消息元数据精简与 portable launcher 生命周期修复**
  - 修复 Grid 第二列 auto min-size 与主内容 Flex 收缩链路：主聊天区、Transcript、思考过程和 Markdown 内容可正确收缩，普通长中英文/URL 不再撑到侧栏下方；主滚动区禁止横向滚动，代码块和宽表格仍仅在自身横向滚动。
  - 会话顶部仅保留标题，移除模型副标题及整行占位；用户消息元数据移除固定 Delta/persona 前缀，从实际 Provider/模型开始，时间与编辑/复制操作保持不变。
  - Playwright health、session/app-wide WebSocket 与消息持久化 fixtures 同步为当前唯一严格 v1 Runtime Contract，移除测试侧遗留的 `{type,data}` 线格式，并新增左右栏组合、展开思考、长正文/URL/代码/表格布局回归。
  - Windows portable 根 `Delta.exe` 经职责审计后改为 bootstrapper：完成目录/数据校验、环境与参数注入并成功启动 Tauri GUI 后立即退出；单实例、更新和 `Delta Server` 生命周期继续由 GUI 管理，不再为转发 GUI 退出码常驻第三个进程。
  - Delta 0.1.7 portable 完整重建、绝对路径泄漏扫描与真实进程 smoke 通过：重复启动仅保留一个 GUI，测试 GUI 退出后 sidecar 无孤儿；ZIP 内 launcher 与新 release build 哈希一致。

#### 2026-08-23 23:31

- **UI Runtime Contract 防回归与便携版打包（UI-017、UI-019）**
  - session WebSocket 在意外断开后自动重连；session/app-wide 流按 `sessionId + sequence` 抑制重复事件，未见过的乱序事件仍被交付，避免遗漏最终消息和 `turn_done` 等终态。
  - CI 新增显式后端 schema 与前端 contract fixture 步骤，契约破坏会在完整测试前快速阻断。
  - `build_portable.ps1` 改为直接调用仓库本地 Tauri CLI，修复 Node 24/npm 12 下 `--no-bundle` 被 npm 错误解析的问题；Delta 0.1.7 portable ZIP、路径泄漏扫描、SHA-256 与真实 launcher/sidecar 启动 smoke 均通过。

### 新增 (Added)

#### 2026-08-23 21:23

- **会话级思考深度控制（真正生效）**
  - 会话卡片 ⋮ 菜单新增「思考深度」组：默认/低/高/最大，按会话持久化（SQLite `reasoning_effort` 列 + 迁移）。
  - 生效链路：`get_engine` 构建时注入 `model_settings.reasoning_effort` → 透传至 provider 调用（OpenAI/兼容协议）；"默认"不下发任何值，由供应商自行决定。运行中的会话通过 `set_reasoning_effort` 即时改写活引擎的 model_settings，无需重建。
  - `PATCH /v1/sessions/{id}` 支持 `reasoning_effort`；会话列表携带该字段驱动菜单勾选态。
  - 测试稳定性：automation unseen 测试两条相邻 TaskRun 补 10ms 间隔（Windows 时钟粒度导致时间戳并列、"最新"判定歧义）。

### 修复 (Fixed)

#### 2026-08-23 13:36

- **进程命名收尾 + 冗余清理**
  - sidecar 图标嵌入 Delta logo（PyInstaller spec `icon=`，此前为 Python 默认图标）。
  - 清理冗余/旧文件：解除跟踪并删除 4 个误提交的构建日志（build_portable.log 等，补入 gitignore 覆盖）；删除 `.pytest_cache`、18 处 `__pycache__`（含旧仓库路径的陈旧缓存）、`coworker.egg-info`、`packaging/dist|build` 中间产物、Playwright test-results（约释放 195MB）。

#### 2026-08-23 09:54

- **已发送消息可编辑/撤回（opencode 式）**
  - 用户消息气泡 hover 显示「编辑」按钮：点击后截断该消息及之后的所有历史，原文本回填到输入框供编辑重发；运行中禁用。
  - 后端新增 `ConversationStore.revert`（JSONL 重写 + 计数同步）、`SessionManager.revert_session`（含内存引擎 messages 截断）、`POST /v1/sessions/{id}/revert` 端点。
  - 前端 `itemsFromMessages` 为用户消息附加原始消息索引，Transcript 透出 `onEditMessage`，App.tsx 调 revert API + 回填 + 刷新。
- **进程角色显示名（Task Manager）**
  - `delta-server.exe`（PyInstaller sidecar）：补版本资源 `FileDescription="Delta Server"`（新建 `delta-server-version.txt`，spec 引用），从裸文件名改为 "Delta Server"。
  - `Delta.exe`（便携版根启动器）：`build.rs` winres 补 `FileDescription="Delta"`，从 "delta-portable-launcher" 改为 "Delta"。
  - Tauri 主程序 `Delta.exe` 已通过 `productName="Delta"` 正确显示。
  - GUI 保持 "Delta"，后台保持可诊断的 "Delta Server"；FileDescription 仅提供进程角色显示名，不承诺或操控 Task Manager 的启发式进程树分组。

#### 2026-08-23 08:43

- **聊天窗口三处**
  - 错误通知自动折叠：警告类通知（如模型停用的 410 API 报错原文）出生时展开可立即查阅，6 秒无交互后自动折叠为一行摘要（悬停暂停折叠），点击随时再展开，Retry 按钮始终可见——此前原始报错全文永久平铺在会话里。
  - 助手气泡上方的"智能体"说话人标签移除（主流聊天 UI 仅靠对齐区分角色）。
  - "Waiting for agent..." 等待文案本地化：中文「正在思考…」、英文 "Thinking…"；压缩上下文提示同步走词条。

#### 2026-08-23 07:11

- **UI 反馈五项**
  - 启动画面未汉化：boot splash 文案（"Starting Delta…"/"Restoring your session…"）原为硬编码英文且渲染在 I18nProvider 之外；新增 `boot.*` 词条并改走 locale 字典解析，随系统/设置语言显示。
  - 品牌残留：默认人格注册名仍为 "OpenWorker"（`personas/registry.py`），系统提示词自称 "You are a Cowork agent"（`agents/cowork.py`）；统一改为 Delta。
  - 自定义服务商：OpenAI 兼容协议的 API key 改为可选（本地 LM Studio/vLLM/llama.cpp 等无需鉴权），保存后的编辑页新增「拉取模型」入口（此前仅创建表单有）。
  - 设置图标换成主流齿轮造型（lucide "settings"，齿圈+中轴），替换旧手绘八齿路径。
  - 主题「自动」不跟随系统：手动选浅色/深色会永久钉死窗口主题；新增 `follow_system_theme` 命令（`set_theme(None)` 解除钉定），切回自动时恢复系统跟随。

#### 2026-08-23 05:54

- **UI 三处反馈问题**
  - 侧栏搜索图标 hover 提示被标题栏遮挡：tooltip 由向上弹出改为向下弹出（`tip-below`）。
  - 搜索弹窗按 Esc 无法关闭：Esc 处理从容器 `onKeyDown`（依赖焦点在弹窗子树内）改为 window 级监听，点击弹窗内非交互区域导致焦点落到 body 后也能关闭。
  - 点击设置「语音」标签屏幕一闪：Windows 分支的 `voice_input_compatibility()` 每次状态轮询都无窗口标志地生成 `cmd.exe`，控制台窗口短暂闪现；补上 `CREATE_NO_WINDOW`。同类的 `stt` 代理探测（两次 `reg query`）一并修复。

#### 2026-08-23 00:30

- **全仓审计修复（24 个文件，后端测试 10 failed → 0 failed）**
  - **安全**
    - skills 上传 token 路径穿越：`skills/store.py` 校验 token 形如 `uuid4().hex` 且 resolve 后必须位于 staging 目录内，堵住 `confirm_upload`/`discard_upload` 被恶意 token 操纵 move/rmtree 任意目录的通道。
    - Webview CSP 从 `null` 收紧为最小化策略（`tauri.conf.json`），覆盖 Tauri v2 IPC、本地 sidecar 端口与 data:/blob: 图片。
    - `permissions.py` 写路径作用域检查从字面量 `"path"` 扩展到全部目标形参（`file_path`/`filepath`/`file`），防改名绕过可写根约束。
  - **Windows 正确性**
    - `tools/search.py` grep 结果解析改为正则匹配，修复 Windows 盘符路径（`C:\...`）下 ripgrep 输出解析全错。
    - `src-tauri/lib.rs` KeepAwakeGuard 的 30 秒长 sleep 拆为 500ms 短轮询，关闭"保持唤醒"/退出应用不再冻结 UI 最长 30 秒。
    - `workspace_trust.py` 改用 `secrets.write_private_text`（icacls ACL），替代 Windows 上的空操作 `chmod 0o600`。
    - `stt/src/lib.rs` 麦克风缺失错误文案按平台区分，Windows 用户不再看到 "Check your Mac sound settings"。
    - `free_port()` 删除静默回落固定端口 8765，bind 失败显式报错。
  - **健壮性**
    - `mcp/oauth.py` OAuth 单槽全局状态竞态：超时清理只在 pending 仍是自己时执行，不再杀死用户随后发起的登录流程。
    - `tools/shell.py` 后台任务输出缓冲改有界 deque（绝对索引保持 cursor 语义、丢弃即报告）、已结束任务条目回收、共享状态与 stdin 写入加锁。
    - `api.ts` `getInbox` 补 `?? []` 兜底；Session WebSocket 对畸形帧包 try/catch（对齐 connectEvents）。
  - **构建 / CI / 桌面集成**
    - `release.yml` 版本校验兼容 `app-v*` 标签（触发器接受但校验必拒导致三平台构建跑完才失败）；同步修复 release job 条件与 latest.json 版本剥离。
    - 注册 `tauri-plugin-opener`（Cargo.toml + builder + capabilities），修复桌面壳内外部链接可能静默失效；修正 lib.rs 中与 updater 实际配置相反的注释。
    - `server/run.py` watchdog 在非父死亡路径释放 `OpenProcess` 句柄；`personas/loading.py` git clone 加 `--` 分隔符；`automation/scheduler.py` 区分 runner 返回 None 与真实 error（记为 `skipped`）。
  - **测试修复（含根因）**
    - `test_send_target_resolution` 断言更新为 `@Delta`（6dfab88 改名遗漏）。
    - `test_ui_refresh_e2e` 预先 rename 会话以跳过 fire-and-forget 自动标题调用（FB-010），消除其异步 provider 调用撞进静音窗口断言的竞态。
    - relay/github 测试放宽 `wait_dispatched` 超时至 30s——本机对 `127.0.0.1:9` 的连接拒绝耗时 ~2.6s（防火墙拦截），每个事件 2–3 次名称解析远超原 2s 窗口。
    - Windows 环境性失败处理：symlink 无权限时 skip、POSIX-only 的 chmod 位断言、rename 前清理引擎派生的常驻 shell 子进程。

#### 2026-08-22 06:31

- **前端构建阻塞：未使用的 `ProviderInfo` 类型导入**
  - `ManageTabs.tsx` 中残留的 `type ProviderInfo` 导入在 23e35a7 重构后成为死代码，`tsc --noEmit`（进而 `npm run build` / `tauri build`）报 `TS6133` 直接失败；移除该导入后构建恢复通过。

### 变更 (Changed)

#### 2026-08-22 06:31

- **辅助测试依赖补装（环境，非源码改动）**
  - 为补齐 `[messaging]` 可选依赖（slack-bolt / aiohttp）与近端 runtests venv 中的 PyGithub 说明，本次验证过程中向测试 venv 补装了 `slack-bolt>=1.18`、`aiohttp>=3.9`（`pip install`），此前因缺失依赖挂掉的 Slack/GitHub 相关用例可真实运行；该改动仅涉及本地测试环境，不产生源码 diff。

#### 2026-08-21 22:07

- **自定义提供商表单加载与错误状态**
  - `/v1/protocols` 异步拉取显式跟踪 loading / error：慢速或失败时显示真实状态（「正在加载协议…」「无法加载协议列表」），不再静默渲染空表单（此前会出现「无 API Key 输入框」「无默认协议」两个现象）。
  - 协议下拉改用主题 token（`--ink`/`--panel`）+ 自定义箭头，修复深色模式下原生 `<select>` 黑底黑字不可读。

#### 2026-08-21 17:22

- **原生标题栏首帧即跟随主题（无浅色闪屏）**
  - 新增 `NATIVE_THEME_SCRIPT` 初始化脚本（随 sidecar 端点注入的同一 `initialization_script` 通道，document-start 执行，早于 HTML 解析与首帧绘制）：镜像 `theme.ts` 的解析（localStorage `openwork-theme` + `prefers-color-scheme`，缺省/非法 = auto），调用既有 `set_native_theme` 命令，使 Windows 深色用户的原生标题栏自第一帧起即为深色，此前 SPA 的 `theme.ts` 要等 webview JS 加载后才下推，可能先闪一帧浅色。`window.__TAURI__` 在初始化脚本中已可用（`withGlobalTauri` 的 bundle 作为初始化脚本先于用户脚本注入）；`theme.ts` 加载后仍会重新应用，此脚本仅为首帧前的抢占式预热。

#### 2026-08-21 02:31

- **模型下载代理回退（非持久化）**
  - `stt/src/lib.rs` 语音模型下载改为直连优先；仅当直连在传输层失败（DNS/连接/超时，非 HTTP 错误）时，自动探测系统代理并重试：依次检查 `HTTPS_PROXY`/`ALL_PROXY`/`HTTP_PROXY` 环境变量，Windows 下再读取注册表 `HKCU\…\Internet Settings` 的 `ProxyEnable`/`ProxyServer`。探测到的代理仅用于本次下载，绝不写回配置或环境变量，避免临时性企业/VPN 代理被永久化。

#### 2026-08-20 20:04

- **自定义提供商端到端测试（custom-provider e2e）**
  - 新增 `surfaces/gui/e2e/custom-provider.spec.ts`：走通「添加自定义提供商 → 输入别名 → 协议下拉默认 OpenAI 兼容 → 填 API Key → 拉取模型（`alias:模型ID` 前缀自动加入）→ 创建并保存 → 新卡显示 ✓ Connected」全流程，基于 hermetic 夹具（fixtures.ts 的 `/v1/protocols`、自定义别名创建、`/v1/providers/fetch` mock）无需 Python 后端即可回归。
  - 修正 `providers.fetchOk` 英文文案复数占位（`"Fetched {n} model{n}"` → `"Fetched {n} model(s)"`），与代码库既有 `(s)` 复数约定一致。

#### 2026-08-20 19:09

- **模型提供商配置：自定义提供商作为一等模块**
  - 设置 ▸ Models 与首次引导新增「添加自定义提供商」入口：别名输入 + 协议下拉（默认「OpenAI 兼容」，另含 OpenAI / Anthropic / Gemini / Ollama / Bedrock / Vertex 原生协议），创建即注册、可随时补全服务器地址与 API Key。
  - 新增「拉取模型」：按所选协议只读拉取模型列表，命中后以 `别名:模型ID` 前缀自动加入模型列表（幂等，已存在则跳过）。
  - 后端新增动态注册表 `CUSTOM_PROVIDERS`（alias → {protocol}）+ `get_descriptor(alias)` 按协议合成描述符，`alias:model` 正常路由并构建客户端；注册元数据持久化于 prefs，重启后仍可路由。

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

#### 2026-08-21 22:07

- **模型默认值全面移除（无预设供应商/模型）**
  - `Config.model` 由 `"gpt-5.6-sol"` 改为 `""`，`build_engine` / `SessionManager` / TUI `CoworkerApp` 的默认模型参数同步置空；各 Provider 客户端 `default_model`（此前从未在生产读取）清空。Delta 不再内置任何厂商/模型，首次配置的提供商（或 Settings ▸ Models 中的显式选择）接管默认。
  - 前端 `App.tsx` 的 model 初始值同步置空，由服务端 health 解析后接管；移除「编辑器选择器」卡片（`ComposerPickerCard`）及 `models.composerPicker*` / `models.removeFromPicker` i18n，废弃的 `removeModel` / `setDefaultModel` 导入停止使用。
  - 同步测试断言：`test_config.py`、`test_model_errors.py`、`test_provider_router.py` 均改为无预设默认；`ProviderSetup.test.tsx` 补充自定义表单标题/新增 ProviderSetupState 字段。

#### 2026-08-21 02:31

- **侧边栏底部图标即时提示 + 登录文案**
  - 四个底部操作图标（收件箱/活动/登录/设置）由原生 `title`（webview 内约 500ms 滞后）改用即时 CSS 提示（`.tip`/`data-tip`）；登录图标未登录态文案 `nav.signInCloud` 由「登录 Delta Cloud」改为「登录 一键连接服务」（en: "Sign in for one-click connections"）。
- **全局搜索入口移回左边栏**
  - 搜索图标由顶栏移回左边栏品牌行，与「Delta」同一行（Delta 居左、搜索居右），点击直接打开命令面板 `SearchModal`；修复此前顶栏拖拽区（`beginWindowDrag`）吞掉 pointerdown 导致搜索图标点击无响应。顶栏 `TopbarSearch` 组件随之移除。
- **空状态问候语居中**
  - `.intro .greeting` 增加 `justify-content: center`，「我能帮您做点什么？」现居中对齐。
- **设置二级菜单「语音输入」→「语音」**
  - `settings.voice.title` zh 由「语音输入」改为「语音」，en 由 "Voice input" 改为 "Voice"。
- **移除全部内置模型服务商，自定义服务商作为模型设置一级卡片**
  - 「模型」设置不再展示任何内置服务商（openai/anthropic/gemini/bedrock/vertex/ollama 及各 OpenAI 兼容厂商），仅保留用户自定义服务商；自定义服务商配置（自定义名称 + 协议下拉 + 服务器地址 + API Key + 测试 + 拉取模型）作为「模型」项下的一级卡片直接可见，无需点击进入二级页面。后端路由/校验逻辑保持不变（已配置的内置服务商仍可路由），过滤仅在前端完成，未改动 `coworker/` 与 `api.ts`。同步 Onboarding 提供商步骤。
- **桌面/启动器/托盘图标以 Delta VI 为唯一来源重新生成**
  - `src-tauri/icons` 全套（icon.ico/icns、Square*Logo、StoreLogo、各尺寸 PNG）由 `assets/logo/delta-logo-512x512.png` 经 `tauri icon` 重新生成；托盘图标 `tray.rgba`/`tray.png` 由彩色 Delta VI（`delta-logo-32x32.png`）重新生成，替换此前的单色模板图，使托盘与桌面图标一致。
- **标题栏与窗口间距**
  - 左边栏品牌行底部内边距 `pb-2` → `pb-3`，缓解标题栏与下方内容间距过小、视觉割裂感。

#### 2026-08-20 10:20

- **默认受信任工作区由 `~/OpenWorker` 改为 `~/Delta`**
  - 会话 scratch 目录默认根路径 `DEFAULT_SCRATCH_BASE` 由 `~/OpenWorker` 改为 `~/Delta`（`coworker/server/manager.py`），同步更新 docstring、设置测试断言与 e2e 夹具路径；按用户要求不做既有数据迁移。
  - 随后更新 `helpers.ts` 回退值与 `e2e/fixtures.ts` 中的 workspace/PRIMARY_ROOT 路径。

- **自动更新指向 Delta 发布 & 替换 minisign 公钥**
  - updater `endpoints` 由空数组改为 `https://github.com/fonga/delta/releases/latest/download/latest.json`，公钥替换为用户提供的 `3FC4BA4778974B1B`，停用旧的占位公钥。

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

#### 2026-08-21 02:31

- **空状态「选择文件夹 →」操作文案颜色不一致**
  - `.task-card-act` 默认由 `opacity:0;color:var(--faint)`（仅 hover 显现）改为 `opacity:1;color:var(--accent)`，与「配置」（gated）操作文案颜色一致。

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
