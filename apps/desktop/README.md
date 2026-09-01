# Delta Desktop

Delta Desktop 是 Delta 的桌面交互应用，基于 React + Tauri。

同一套前端代码既可以在浏览器开发模式下运行，也可以作为 Tauri Desktop 应用运行。

## 开发环境

首次开发前，在仓库根目录安装 Python 后端及开发依赖：

```bash
uv sync --locked --extra dev --extra messaging
```

`uv` 会根据 `uv.lock` 创建或更新：

```text
.venv/
```

并安装 `delta-server` 等开发所需组件。

前端依赖在 `apps/desktop/` 下安装：

```bash
cd apps/desktop
npm install
```

## 浏览器开发

浏览器模式需要分别启动 Delta Server 和前端开发服务器。

### 启动后端

在仓库根目录执行：

```bash
uv run delta-server --cwd /path/to/your/project --port 8765
```

`--cwd` 指定当前 Workspace。

默认监听：

```text
http://127.0.0.1:8765
```

也可以通过配置或启动参数调整 Host 和 Port。

### 启动前端

另开一个终端：

```bash
cd apps/desktop
npm run dev
```

默认开发地址：

```text
http://localhost:5173
```

前端默认连接本地 Delta Server。

需要覆盖连接地址时，可使用：

```text
VITE_DELTA_HTTP
VITE_DELTA_WS
```

浏览器开发模式下，应先启动 Delta Server，再启动 Vite。

Standalone `delta-server` 会为当前端口生成临时 API Token 文件，例如：

```text
<state-dir>/sidecar-8765.token
```

如果重新启动 Delta Server，必要时同时重新启动 Vite，使开发环境重新读取当前 Token。

## Tauri 开发

Desktop 模式由 Tauri Shell 启动并管理本地 Delta Server，不需要单独启动后端终端。

前置条件：

* Rust toolchain（`rustup`）
* Node.js
* Python 3.11+
* `uv`
* 已通过 `uv sync` 创建 `.venv`

启动：

```bash
cd apps/desktop
npm run tauri dev
```

开发模式下，Tauri 会从仓库 `.venv` 中查找 `delta-server`。

Windows：

```text
.venv/Scripts/delta-server.exe
```

macOS / Linux：

```text
.venv/bin/delta-server
```

正式打包时使用的 Sidecar 由 `packaging/` 中的构建流程生成，不依赖开发环境中的 `.venv`。

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

e2e 使用隔离的模拟 API / WebSocket 环境，不要求启动真实 Python Server。