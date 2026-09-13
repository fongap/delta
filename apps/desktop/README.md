# Delta Desktop

Delta Desktop 是 Delta 的桌面交互应用，基于 React + Tauri。

## 当前架构

从 R6 / PR #203 开始，Tauri Desktop 的主运行路径已经切到直接 Rust IPC：

```text
React / TypeScript
        ↓
Tauri Commands / Events
        ↓
in-process Rust RuntimeHost
        ↓
Trust / Work / Capability / Automation / Learning
        ↓
Controlled Worker / External Capability
```

Desktop 不再以本地 Python `delta-server` sidecar 作为主运行路径。

Python `services/server`、`SessionManager`、`TurnEngine`、Provider compatibility 等旧 application/runtime surface 仍存在于仓库，用于尚未完成的迁移、浏览器兼容、测试或过渡行为；它们不是新的 Desktop Authority。

目标架构见 `docs/architecture/target-architecture.md`，R6 当前进度见 `docs/governance/r6-execution-plan.md`。

## 开发环境

前端：

```bash
cd apps/desktop
npm install
```

Tauri Desktop：

```bash
npm run tauri dev
```

前置条件：

- Rust toolchain；
- Node.js；
- 当前仓库仍建议准备 Python 3.11+ 与 `uv`，用于尚未迁移的兼容层、测试和 Worker 生态。

Python 依赖：

```bash
uv sync --locked --extra dev --extra messaging
```

## 浏览器开发模式

浏览器模式目前仍可使用 Python `delta-server` 作为兼容开发 API：

```bash
uv run delta-server --cwd /path/to/project --port 8765
cd apps/desktop
npm run dev
```

这只是 browser compatibility surface，不代表 Tauri Desktop 需要 Python sidecar。

如使用该模式，相关环境变量仍包括：

```text
VITE_DELTA_HTTP
VITE_DELTA_WS
```

## Tauri Runtime IPC

当前 Tauri host 嵌入 `delta-runtime-native`，并通过 Rust commands / events 暴露 Runtime 能力。

当前核心动作包括：

```text
health
run
resume
retry
steer
follow-up
cancel
messages
switch-model
truncate
```

TypeScript 应围绕稳定 domain semantics 使用这些能力，而不是把 transport 细节扩散到 UI。

## TypeScript 职责

Desktop 属于 Experience 模块，负责：

- Workspace / Session 交互；
- 任务进度和 Runtime events；
- Steer / Follow-up / Cancel；
- Approval / Ask User；
- Source / Citation；
- Artifact / Version；
- 文档、表格、图表、图片、视频预览；
- Skill / Automation / Settings 管理。

TypeScript 不自行决定 Run、Approval、Validation、Artifact 等权威事实。

## 命名

独立产品 / 协议 / SDK 边界可以使用 `delta-*`；内部 UI 模块和 Runtime 内职责使用语义名。

例如：

```text
Delta / Delta.exe          用户产品
delta-runtime              独立 Rust Runtime boundary
delta-protocol             稳定跨语言 contract
@delta/*                   只有形成独立 TS package 时使用
```

不要把每个内部目录都品牌化成 `delta-*`。

## 测试

```bash
npx tsc --noEmit
npx vitest run
npx playwright test
```

R6 期间测试重点是保持用户语义和 TS ↔ Rust IPC contract，而不是维持旧 Python endpoint 的内部形态。