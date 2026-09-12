# R6 Execution Plan

> 本文是 R5.1 完成后的 **R6 执行计划**。它基于 2026-09-12 对 `feat/r5.1-runtime-human-control` 最新修复态的代码审计，不重新定义产品或目标架构。
>
> 产品边界见 `../DELTA_BLUEPRINT.md`；目标架构见 `../architecture/target-architecture.md`；长期迁移治理见 `rust-core-migration.md`。

## 1. R6 启动判断

结论：**GO**。

R5.1 已经提供足够稳定的 trusted foundation 和 Human Control contract，可以开始产品减法和 Rust + TypeScript Runtime Convergence。R6 不需要重做 R1–R5.1 已形成的 Ledger、Idempotency、Artifact、Validation、Source/Citation、Checkpoint、Policy、Approval、Tool lifecycle、Provider transport、Retry、Automation completion 和 Steer / Follow-up / Cancel 语义。

当前真正需要迁移的不是这些 Authority，而是仍由 Python 长期持有的 application/runtime orchestration。

## 2. 最新审计结论

### 2.1 已具备的迁移基础

- `core/runtime-native` 已是可复用 `rlib`，并拥有 Ledger、Idempotency、Artifact、Validation、Checkpoint、Policy、Approval、Source/Citation、TaskStore、Tool lifecycle、Retry、Provider 等模块；
- `RuntimePort` 已把 `run / resume / retry / steer / interrupt` 和 Runtime 所拥有的 context / model / tool / compaction / step loop 边界定义出来；
- R5.1 protocol v16 已完成 request identity、multiplexing、real cancel、backpressure 和 Human Control；
- 最新 R5.1 修复进一步收紧 recovery latest-state semantics 和 citation `range_unverified` 语义，CI / CodeQL 均通过；
- Tauri Desktop 已经存在 Rust application host，可直接成为未来 Runtime Host 的承载点。

### 2.2 当前最主要的架构债务

当前主路径仍然是：

```text
TypeScript / React
       ↓ HTTP / WS
Tauri localhost proxy
       ↓
Python delta-server / FastAPI
       ↓
SessionManager
       ↓
RuntimePort / TurnEngineAdapter
       ↓
Python TurnEngine
       ↓
Rust delta_core authorities
```

因此 Python 仍是 application host，而不是单纯 Worker。

主要迁移面：

- `services/server/app.py` 及一组 `manager_*` 仍承担 Session、Provider、Gateway、Automation、Inbox、Connections、Skills/Memory、Workspace 等 application orchestration；
- `core/engine.py` 的 `TurnEngine` 仍拥有 model ↔ tool step loop；
- `providers/openai_provider.py` 等仍保留 Python SDK compatibility fallback；
- `providers/router.py` 仍在 Python 维护 provider/model route decision；
- Desktop Tauri 仍启动并代理 `delta-server` sidecar；
- Windows Portable 仍必须打包、启动和验证 `delta-server.exe`；
- `aisuite` 仍深入 Tool metadata、Files、Web、MCP、Skills、Memory、Automation、Connector、Shell 等实现。

### 2.3 产品边界仍有历史残留

Delta 默认 persona 已经是 `delta`，但产品代码仍保留明显不一致：

- `core/agents/code.py` 仍是完整 Coding Agent；
- capability catalog 仍有 `code_files` 和 `git`；
- Persona Registry 仍把 Code 作为内建 persona，并支持第三方 Persona 扩展；
- `core/agents/registry.py` 的空值 fallback 仍是 `code`；
- `SessionManager.get_engine(... agent="code")` 和旧 record fallback 仍会落到 `code`；
- Delta prompt 仍使用旧的 `office work, data analysis, research...` 表述，尚未完全同步为 **日常办公｜研究分析｜内容创作**。

目标不是粗暴删除旧 session compatibility，而是停止创造新的 Coding / Persona 产品面，并在有明确迁移路径后删除 legacy resolver。

### 2.4 Skill / Learning 当前成熟度

现有 Skill 已具备：

- Global / Project scope；
- create / update / delete / move / upload；
- session-level enable/mute；
- progressive disclosure；
- `SKILL.md` instructions + optional resources/scripts；
- allowed-tools metadata。

但当前 Skill 仍主要是 instructions container。尚未形成目标架构需要的：

- domain / version / validation / template / worker contract；
- Experience / Failure evidence；
- Skill Candidate；
- Evaluation；
- Promote / Reject / Rollback / Retirement。

因此 Learning 目前仍是目标能力，不是已经完成的 Runtime 事实。R6 期间只冻结 contract 和安全边界，不应把完整自我进化与 Runtime 迁移绑在一起。

### 2.5 三产品域当前能力深度

**日常办公**已有文件读写、多根目录、PDF / XLSX / DOCX 读取、Citation、Spreadsheet preview 等基础，但正式 Office 创建 / 局部编辑 / 样式 / 交付验证仍需后续做深。

**研究分析**目前主要依靠通用 Shell / Script 能力。主 Python dependency 尚未正式引入完整统计研究栈；DOE、序贯试验设计、统计建模和正式研究报告属于后续 Capability 深化，不应在 R6 Runtime 重构中一次性完成。

**内容创作**目前已有通用写作、Web、文件和 Artifact 底座，但图文、图片、视频、发布和复盘仍需要后续 Media / Content Capability。

## 3. R6 执行原则

R6 只解决两件事：

1. **产品减法**：把产品入口收敛为一个 Delta + Skills / Capabilities；
2. **控制面收敛**：把 persistent product Runtime 从 Python 收到 Rust，让 TypeScript 直接使用 Rust application host。

R6 不把 Office / DOE / Video / Learning 的深度扩建混进 Runtime hard-cut。

每个 PR 必须能回答：

```text
Authority Before
Authority After
Temporary Compatibility
Exit Condition
Tests
Rollback without dual-authority
```

## 4. 推荐执行序列

### R6.0 — Baseline Lock & Product Surface Cleanup

目标：先缩小迁移面，不动核心 Runtime contract。

任务：

- 合并并冻结 R5.1 绿色基线；
- 合并产品 / 目标架构文档；
- 默认 / fallback agent 全部统一为 `delta`；
- Delta system prompt 同步为 **日常办公｜研究分析｜内容创作**；
- Code persona 从新建 / Settings 产品入口退出；
- `code_files` / `git` 不再作为 Delta 产品 capability；
- Chat / Ops / MyHelper / third-party Persona 扩展面进入 legacy review：保留兼容读取，不再扩大 Persona 平台；
- 保留 File / Search / Shell / Script；
- 对已有 `agent=code` session 提供显式 legacy migration / compatibility，不静默破坏历史数据；
- 增加 CI guard：新产品代码不得重新把 `code` 设为默认 agent。

**Exit Condition**：新用户和新 session 只有一个主 Delta 产品身份；Coding 不是可新增产品面；历史 session 可安全读取 / 迁移。

### R6.1 — Rust Runtime Host Foundation

目标：在 Rust 建立当前 `RuntimePort` 的等价实现，不先改 UI。

第一批 contract：

```text
run
resume
retry
steer
follow-up
interrupt / cancel
runtime event stream
```

任务：

- 在 Rust application/runtime host 中组合现有 `delta-runtime-native` authorities；
- 不复制 Ledger / Approval / Artifact 等事实模型；
- 把 R5.1 v16 的 cancel / backpressure / Human Control 语义转成 Runtime Host contract tests；
- 定义稳定 Rust ↔ TypeScript DTO / event envelope；
- 建立 fake/model/tool harness，先证明 Runtime loop 可以独立于 FastAPI 驱动。

**Exit Condition**：Rust Runtime Host 能在无 Python SessionManager / TurnEngine 参与的测试 harness 中完成最小 `run → tool → result → complete` 和 `steer / cancel / resume` 流程。

### R6.2 — TypeScript Direct IPC Slice

目标：证明桌面可以绕过 Python HTTP/WS 主链。

先选择小而稳定的只读 / 低风险 surface，例如：

- health / capabilities；
- runtime events；
- session metadata；
- artifact/source query。

任务：

- 保持前端 domain types；
- 新增 Tauri Commands / Events；
- 同一功能切换成功后删除对应纯转发 Python path；
- 不重做 React UI；
- 增加 TypeScript ↔ Rust IPC contract tests。

**Exit Condition**：至少一条真实 Desktop 用户路径完全不经过 localhost Python server，并且 E2E 与原行为一致。

### R6.3 — Application Control Plane Migration

目标：逐领域迁移 Python `SessionManager` 职责。

建议顺序：

```text
Session / Workspace metadata
→ Settings / Secrets
→ Source / Artifact / Validation wiring
→ Approval / Inbox
→ Scheduler / Automation
→ Connector / MCP host boundary
→ Skill / Memory application wiring
```

原则：

- 已有 Rust authority 的领域只迁 orchestration，不重写事实模型；
- 每迁一个领域立即删除对应 Python owner / facade；
- 不先拆多个 crate；优先保持模块化单体；
- Memory / Skill 先保留现有用户行为，不在本阶段实现 Learning promotion engine。

**Exit Condition**：Python `SessionManager` 不再是产品控制面的总入口，剩余职责都有明确删除计划。

### R6.4 — Agent Loop & Provider Hard-Cut

目标：迁移最复杂、最容易影响行为的 intelligence loop。

任务：

- Context assembly；
- model invocation；
- tool schema / selection；
- tool result continuation；
- compaction；
- retry / error mapping；
- steering safe-point；
- provider streaming；
- OpenAI-compatible / Anthropic-compatible message conversion；
- Python `ProviderRouter` decision 迁 Rust；
- 删除 Python OpenAI / Anthropic SDK compatibility fallback。

**Exit Condition**：正常 Desktop turn、tool call、streaming、retry、steer、cancel、resume 全部由 Rust Runtime Host 驱动；Python 不再调用模型 Provider。

### R6.5 — Capability ABI & Workerization

目标：保留 Python 生态，但把它从 Runtime 变成工作执行层。

统一 Worker contract 至少包含：

```text
job id
workspace scope
input files / hashes
arguments
permission grants
network grants
timeout / cancel
progress
typed result
staged artifacts
stderr / diagnostics
```

任务：

- Python / PowerShell / Shell 统一进入 Rust-supervised Worker Runner；
- Worker 不可直接写 core DB、Secrets、Run state 或 Artifact formal state；
- Tool / Skill 从 `aisuite` callable abstraction 迁到 Delta-owned Capability ABI；
- 先迁 File / Search / Shell / Skill load 等通用能力；
- Office / Research / Media 专业实现允许继续使用 Python；
- 为后续 `research.doe.*`、`research.sequential.*`、graphic/image/video worker 保留 stable capability namespace，但不在本阶段全部实现。

**Exit Condition**：专业 Python 能力可以继续被 Delta 调用，但 Python 不拥有产品 Runtime / Tool authority；`aisuite` 不再是主 Runtime / tool abstraction 的必要依赖。

### R6.6 — Python Backend Removal & Packaging Cutover

目标：删除常驻 Python application backend。

删除 / 替换：

- FastAPI / Uvicorn application server；
- `delta-server` console entry；
- Python SessionManager / TurnEngine；
- Python Provider SDK / Router control plane；
- localhost reverse-proxy 主产品路径；
- PyInstaller `delta-server.exe` sidecar；
- release 中 sidecar import / boot smoke；
- 仅服务旧 Runtime 的 `aisuite` 依赖。

Portable Release 改为验证：

```text
Delta.exe / Tauri
+ Rust Runtime Host
+ controlled Worker runtime/assets
```

**Exit Condition**：关闭 / 删除 Python 环境后，Delta 核心 UI、Session、Run、模型调用、Tool orchestration、Automation 和 trusted work flow 仍可启动运行；只有调用明确 Python Worker 的任务才需要 Python worker runtime。

### R6.7 — Hard-Cut Audit

最终验收：

```text
Core product = Rust + TypeScript
Python / PowerShell / Shell = controlled workers only
No persistent Python application backend
No TS → Python → Rust forwarding main path
No Python model-provider authority
No Python Run / Session / Policy / Artifact authority
No Coding product surface
```

并增加永久 CI guard，阻止这些职责回流。

## 5. R6 期间不要同步做的工作

为避免再次扩大变量，以下工作原则上放到 R6 hard-cut 之后：

- 完整 Office 编辑器能力扩建；
- 完整统计 / DOE / 序贯试验 / 研究报告能力实现；
- 图文 / 图片 / 视频生产链大扩建；
- Experience → Skill Candidate → Evaluation → Promotion 的完整 Learning engine；
- 新 Persona / Multi-Agent 平台；
- 新模型协议；
- 新 Workflow Engine；
- 大规模 UI 重设计。

R6 期间只允许为迁移验收补少量 representative task / capability fixture。

## 6. R6 后产品深化顺序

架构 hard-cut 后，按三个产品域分别做深，但共享同一 Runtime / Trust / Work / Capability 基础。

推荐：

```text
P1 日常办公
  → 创建 / 编辑 / 验证 DOCX / XLSX / PPTX / PDF

P2 研究分析
  → Statistics foundation
  → DOE
  → Sequential experimental design from day one
  → Visualization / interpretation
  → Formal research report

P3 内容创作
  → Research / Writing / Editing
  → Graphic / Image
  → Video
  → Multi-platform adaptation / publishing / analytics

P4 Learning
  → Experience / Failure evidence
  → Skill Candidate
  → Evaluation
  → Promote / Reject / Rollback
```

P1–P4 不是独立 Runtime，也不是四个 Agent。

## 7. 第一批开发任务建议

R6 正式开始后，第一批只做 **R6.0**，不要把 Rust Runtime Host 和产品清理混成一个 PR。

验收后再进入 R6.1。

原因：当前 Code / Persona / default-agent 残留本身会扩大 Runtime Host 需要兼容的产品面。先减掉不需要长期支持的产品分支，后续 Rust 迁移会更小、更稳定。

## 8. 工作量级判断

R6 不再按“Python → Rust 文件翻译”估算。

当前审计表明，复杂度主要集中在：

```text
SessionManager application orchestration
TurnEngine agent loop
Provider fallback / routing
Tool / aisuite abstraction
Desktop sidecar + release packaging
```

因此 R6 应被视为一轮中大型 Runtime convergence，而不是简单清胶水。实际投入应由每阶段 PR 和验收结果动态校准，禁止为了赶进度长期保留双 Authority。

## 9. 最终原则

> **先减产品面，再迁控制面。**

> **先保 contract，再换 implementation。**

> **迁移控制权，不迁移生态优势。**

> **一个 Delta，一个 Runtime，一个领域一个 Authority。**

> **长期、稳定、高效、安全、敏捷优先。**
