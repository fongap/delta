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

Delta 是一个本地优先的个人工作 AI Agent，只围绕三项核心能力演进：**日常办公｜数据分析｜内容创作**。

它的目标不是增加模型、Agent 或工具数量，而是把这三类工作从需求推进到可检查、可恢复、可验证、可追溯的实际成果。

<p align="center">
  <img src="resources/screenshots/Delta-2026082401.webp" width="95%" alt="Delta">
</p>

## 核心定位

- **日常办公**：文档、表格、PDF、邮件、会议材料、资料整理、格式转换和批量文件处理等个人办公任务。
- **数据分析**：从数据导入、清洗、统计设计、统计分析、可视化和结果解释，到完整分析 / 研究报告撰写的端到端工作。数据分析不是简单问数、汇总或画图，而是完整的数据统计分析与报告交付。
- **内容创作**：面向自媒体和个人内容生产的选题、资料研究、写作、编辑、配图 / 图文、多格式内容整理和发布准备。

这三个名称是 Delta 的一级产品方向。研究、文档处理、资料处理、PDF / Office、Search、Citation、Validation、Scripting、Automation、Connector、MCP 等均为三项核心能力中的工作环节或支撑能力，不单独扩展为新的产品方向。

其中“研究”不是第四条主线：数据分析中的研究服务统计分析、结果解释和正式报告；内容创作中的研究服务选题、事实核查和资料整理。

## 产品边界

Delta 不是 Coding Agent，也不是模型聚合器。

- **三类工作优先**：新增能力必须能够直接改善日常办公、数据分析或内容创作中的真实工作环节。
- **保留脚本能力**：可以为完成任务编写、执行和修正 Python、PowerShell、Shell 等脚本；脚本是执行手段，不是软件开发产品方向。
- **不建设 Coding 工作流**：不以 IDE、代码仓库理解、Git / PR、代码审查和大型软件工程任务作为核心能力。
- **协议保持收敛**：模型层仅支持 OpenAI-compatible 与 Anthropic-compatible 两类协议，不扩展第三类原生模型协议。
- **模型路由外置**：Provider 聚合、模型 fallback、权重和复杂路由不属于 Delta 的产品职责，可由兼容 Endpoint 或独立 AI Gateway 承担。

## 共同执行底座

三项核心能力共享同一套执行基础：

- **理解任务**：识别目标、资料、约束和预期成果。
- **规划执行**：组织步骤并持续推进多步骤工作。
- **调用资源**：使用本地文件、脚本、工具和用户明确启用的外部能力。
- **形成成果**：生成文档、表格、统计分析、图表、分析 / 研究报告、内容素材及其他 Artifact。
- **关键审批**：高后果操作进入统一 Policy / Approval 边界。
- **验证与追溯**：记录来源、执行事实、验证结果和任务恢复状态。

这些是共同底座，不构成新的产品方向。

## 发展方向

产品能力只围绕三项核心方向继续加深：

- **日常办公**：让文档、表格、PDF、资料整理、格式转换和批量处理形成稳定闭环。
- **数据分析**：覆盖数据导入与检查、清洗、统计设计、统计方法选择、统计计算、可视化、结果解释和正式报告撰写。
- **内容创作**：覆盖选题、资料研究、写作、编辑、图文、多格式内容和发布准备。

共同底座重点完善：可靠执行、来源引用、Artifact / Validation、执行记录、恢复与受控学习。

目标是让三类真实个人工作能够持续执行、随时恢复、完整复盘并形成可靠成果，而不是停留在一次性对话。

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
