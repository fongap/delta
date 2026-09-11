<p align="center">
  <img src="resources/brand/delta-logo-256x256.png" width="96" alt="Delta Logo">
</p>

<div align="center">

[![CI](https://github.com/fongap/delta/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/fongap/delta/actions/workflows/ci.yml)
[![CodeQL](https://github.com/fongap/delta/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/fongap/delta/actions/workflows/codeql.yml)
[![Version](https://img.shields.io/github/v/release/fongap/delta?label=Version)](https://github.com/fongap/delta/releases)
[![Windows Portable](https://img.shields.io/badge/Windows-Portable-0078D6?logo=windows&logoColor=white)](https://github.com/fongap/delta/releases/latest)
[![License](https://img.shields.io/github/license/fongap/delta?label=License)](LICENSE)

</div>

# Delta

Delta 是一个面向个人知识工作的本地优先 AI Agent，专注于办公、数据分析、研究、文档处理和内容生产等真实工作任务。

它的目标不是增加模型、Agent 或工具数量，而是把一件工作从需求推进到可检查、可恢复、可验证、可追溯的实际成果。

<p align="center">
  <img src="resources/screenshots/Delta-2026082401.webp" width="95%" alt="Delta">
</p>

## 产品边界

Delta 不是 Coding Agent，也不是模型聚合器。

- **知识工作优先**：重点服务办公、数据分析、研究、资料处理、自媒体和内容生产。
- **保留脚本能力**：可以为完成任务编写、执行和修正 Python、PowerShell、Shell 等脚本；脚本是执行手段，不是软件开发产品方向。
- **不建设 Coding 工作流**：不以 IDE、代码仓库理解、Git / PR、代码审查和大型软件工程任务作为核心能力。
- **协议保持收敛**：模型层仅支持 OpenAI-compatible 与 Anthropic-compatible 两类协议，不扩展第三类原生模型协议。
- **模型路由外置**：Provider 聚合、模型 fallback、权重和复杂路由不属于 Delta 的产品职责，可由兼容 Endpoint 或独立 AI Gateway 承担。

## 主要能力

- **理解任务**：识别目标、资料、约束和预期成果。
- **规划执行**：组织步骤并持续推进多步骤工作。
- **调用资源**：使用本地文件、脚本、工具和用户明确启用的外部能力。
- **形成成果**：生成文档、表格、分析结果、图表、结构化数据及其他 Artifact。
- **关键审批**：高后果操作进入统一 Policy / Approval 边界。
- **验证与追溯**：记录来源、执行事实、验证结果和任务恢复状态。

## 发展方向

Delta 将继续坚持本地优先，重点完善：

- **可靠执行**：长任务中断、恢复、重试与副作用幂等。
- **办公与数据能力**：文档、表格、PDF、数据分析和脚本执行。
- **来源引用**：重要结论可追溯至原始资料。
- **Artifact 与 Validation**：交付物可定位、可检查并具有明确完成条件。
- **执行记录**：任务过程可检查、复盘与审计。
- **受控学习**：长期行为变化具有明确边界、证据和撤销机制。

目标是让真实知识工作能够持续执行、随时恢复、完整复盘并形成可靠成果，而不是停留在一次性对话。

## 模型协议

Delta 的模型协议层固定为两类：

```text
OpenAI-compatible
Anthropic-compatible
```

具体模型和服务商不构成 Delta 的产品边界。只要 Endpoint 符合上述协议之一，即可由用户按实际需要配置。

Delta 不以增加 Provider 数量为目标，也不计划维护第三套原生模型协议。

## 安装部署

### 便携版（推荐）

从 [Releases](https://github.com/fongap/delta/releases) 下载 `Delta-Windows-Portable.zip`，解压到任意目录（支持中文和空格路径），运行 `Delta.exe` 即可：

- 所有数据（配置、密钥、日志、数据库）保存在 `Data/` 目录下，随文件夹整体迁移，不写入 `%APPDATA%`。
- 首次启动自动创建 `Data/` 并引导配置模型 Endpoint。
- 便携版不内置 WebView2 Runtime；Windows 10/11 通常已安装，缺少时需单独安装。

### 本地开发

前置条件：Python 3.11+、`uv`、Node.js、Rust toolchain。

```bash
# Python 环境
uv sync --locked --extra dev --extra messaging

# Desktop
cd apps/desktop
npm install
npm run tauri dev
```

Tauri 开发模式会使用仓库 `.venv` 中的 `delta-server`。

浏览器开发模式及 Desktop 测试说明见：

[`apps/desktop/README.md`](./apps/desktop/README.md)

### 构建便携包

```powershell
# 前置条件：Rust、Node.js、Python 3.11+、uv
uv sync --locked --extra build
.\packaging\portable\build_portable.ps1
```

最终产物写入仓库根目录：

```text
releases/
```

## 项目状态

`main` 为持续开发线。具体实现状态、Runtime Authority 和迁移进度以当前 ADR、运行时公共契约和 CHANGELOG 中已经完成的事实为准，README 不提前声明尚未完成的阶段。

- 产品与架构蓝图：[`DELTA_BLUEPRINT.md`](https://github.com/fongap/delta/blob/main/docs/DELTA_BLUEPRINT.md)
- 开发说明：[`CONTRIBUTING.md`](https://github.com/fongap/delta/blob/main/CONTRIBUTING.md)
- 仓库结构：[`repository-layout.md`](https://github.com/fongap/delta/blob/main/docs/architecture/repository-layout.md)
- 运行时契约：[`runtime-public-contract.md`](https://github.com/fongap/delta/blob/main/docs/architecture/runtime-public-contract.md)
- 变更记录：[`CHANGELOG.md`](https://github.com/fongap/delta/blob/main/CHANGELOG.md)
- 开源许可：[`MIT License`](https://github.com/fongap/delta/blob/main/LICENSE)
