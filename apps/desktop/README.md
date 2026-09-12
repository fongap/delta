# Delta Desktop

Delta Desktop 是 Delta 的桌面交互应用，基于 React + Tauri。

当前同一套前端代码既可以在浏览器开发模式下运行，也可以作为 Tauri Desktop 应用运行。

> 当前 Desktop 仍通过本地 Python `delta-server` HTTP / WebSocket sidecar 驱动 application/runtime compatibility layer。这是 **R6 前的过渡实现**，不是长期目标架构。
>
> 长期目标：TypeScript / React 通过 Tauri Commands / Events 直接连接 Rust Runtime Host；Python、PowerShell、Shell 只保留为受控 Capability Worker / Script。

目标架构见：

```text
docs/architecture/target-architecture.md
```

## 当前开发环境

R6 hard-cut 完成前，开发环境仍需要 Python 后端及开发依赖。

在仓库根目录：

```bash
uv sync --locked --extra dev --extra messaging
```

`uv` 根据 `uv.lock` 创建或更新：

```text
.venv/
```

当前会安装 `delta-server` 等过渡期组件。

前端依赖：

```bash
cd apps/desktop
npm install
```

## 当前浏览器开发模式

浏览器模式目前需要分别启动 Delta Server 和前端开发服务器。

### 启动后端

仓库根目录：

```bash
uv run delta-server --cwd /path/to/your/project --port 8765
```

`--cwd` 指定当前 Workspace。

默认：

```text
http://127.0.0.1:8765
```

### 启动前端

另开终端：

```bash
cd apps/desktop
npm run dev
```

默认：

```text
http://localhost:5173
```

前端当前连接本地 Delta Server。覆盖地址时使用：

```text
VITE_DELTA_HTTP
VITE_DELTA_WS
```

Standalone `delta-server` 会为当前端口生成临时 API Token，例如：

```text
<state-dir>/sidecar-8765.token
```

后端重启后，必要时同时重启 Vite 以读取当前 Token。

## 当前 Tauri 开发模式

Desktop 模式目前由 Tauri Shell 启动并管理本地 Delta Server，不需要单独启动后端终端。

前置条件：

- Rust toolchain（`rustup`）；
- Node.js；
- Python 3.11+；
- `uv`；
- 已通过 `uv sync` 创建 `.venv`。

启动：

```bash
cd apps/desktop
npm run tauri dev
```

Tauri 当前从仓库 `.venv` 查找 `delta-server`：

Windows：

```text
.venv/Scripts/delta-server.exe
```

macOS / Linux：

```text
.venv/bin/delta-server
```

正式打包时的 Sidecar 由 `packaging/` 构建流程生成，不依赖开发环境 `.venv`。

## R6 目标调用路径

当前：

```text
React / TypeScript
        ↓
Tauri localhost proxy
        ↓
Python FastAPI / WebSocket
        ↓
Python application/runtime glue
        ↓
Rust delta_core authority
```

目标：

```text
React / TypeScript
        ↓
Tauri Commands / Events
        ↓
Rust Runtime Host
        ↓
Trust / Work / Capability / Automation / Learning
        ↓
Controlled Worker / External Capability
```

R6 Desktop 迁移规则：

- 优先保持现有 TypeScript domain API 和用户交互语义；
- 后端从 Python 切 Rust，不顺手大改 UI；
- Steer / Follow-up / Cancel 等 R5.1 Human Control 行为不能退化；
- 每迁移一组接口就删除对应 Python 纯转发路径；
- 最终删除 localhost Python sidecar、无必要 reverse proxy 和 `delta-server` packaging；
- 浏览器开发模式如继续保留，应使用明确的开发适配层，不反向要求产品保留 Python Backend。

## TypeScript 职责

Desktop 属于目标架构的 Experience 模块。

负责：

- Workspace / Session 交互；
- 任务进度和 Runtime events 展示；
- Steer / Follow-up / Cancel；
- Approval / Ask User；
- Source / Citation；
- Artifact / Version；
- 文档、表格、图表、图片、视频预览；
- Skill / Automation / Settings 管理。

TypeScript 不自行决定 Run、Approval、Validation、Artifact 等权威状态；这些事实来自 Rust Runtime / Trust / Work。

## 测试

TypeScript 类型检查和 Unit Test：

```bash
npx tsc --noEmit
npx vitest run
```

Frontend e2e：

```bash
npx playwright test
```

当前 e2e 使用隔离的模拟 API / WebSocket 环境，不要求启动真实 Python Server。

R6 迁移期间应逐步补充 TypeScript ↔ Tauri / Rust contract tests，使测试关注稳定用户语义，而不是绑定 Python endpoint 的内部实现。