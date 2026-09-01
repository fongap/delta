<p align="center">
  <img src="resources/brand/delta-logo-256x256.png" width="96" alt="Delta Logo">
</p>

<div align="center">

[![Version](https://img.shields.io/github/v/release/fongap/delta?style=flat-square&color=blue&label=Version)](https://github.com/fongap/delta/releases)
![Update](https://img.shields.io/github/last-commit/fongap/delta?style=flat-square&color=orange&label=Update)
![License](https://img.shields.io/github/license/fongap/delta?style=flat-square&color=green&label=License)

</div>

# Delta

Delta 是一个面向个人工作的本地优先 AI 助手，基于 OpenWorker 演进，并作为独立项目维护自身的产品定位、架构与发布节奏。

<p align="center">
  <img src="resources/screenshots/Delta-2026082401.webp" width="95%" alt="Delta">
</p>

## 项目简介

Delta 帮助用户理解任务、组织步骤、调度本地资源并完成实际操作。模型由用户自主配置，不绑定任何特定服务商。

## 主要能力

- **理解任务**：识别用户意图并拆解任务
- **规划执行**：组织步骤并推进工作流程
- **调度资源**：按需调用本地文件与工具
- **执行操作**：完成任务所需的具体操作
- **关键审批**：重要操作前请求用户确认

## 发展方向

Delta 将继续坚持本地优先，逐步完善：

- **任务执行**：支持长任务中断与恢复
- **来源引用**：结果可追溯至原始资料
- **执行记录**：便于检查、复盘与审计
- **受控学习**：在明确边界内积累和改进经验

目标是让复杂、长期任务能够持续执行、随时恢复、完整复盘并逐步改进，而不是停留在一次性对话。

## 安装部署

### 便携版（推荐）

从 [Releases](https://github.com/fongap/delta/releases) 下载 `Delta-Windows-Portable.zip`，解压到任意目录（支持中文和空格路径），运行 `Delta.exe` 即可：

- 所有数据（配置、密钥、日志、数据库）保存在 `Data/` 目录下，随文件夹整体迁移，不写入 `%APPDATA%`。
- 首次启动自动创建 `Data/` 并引导连接模型服务商。
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
````

Tauri 开发模式会使用仓库 `.venv` 中的 `delta-server`。

浏览器开发模式及 Desktop 测试说明见：

[`apps/desktop/README.md`](./apps/desktop/README.md)

### 构建便携包

```powershell
# 前置条件：Rust、Node.js、Python 3.11+、uv
uv sync --locked --extra bedrock --extra build
.\packaging\portable\build_portable.ps1
```

最终产物写入仓库根目录：

```text
releases/
```

## 项目状态

> Delta 仍处于开发阶段，并非 OpenWorker 官方发行版。

- 开发说明：[`CONTRIBUTING.md`](https://github.com/fongap/delta/blob/main/CONTRIBUTING.md)
- 仓库结构：[`repository-layout.md`](https://github.com/fongap/delta/blob/main/docs/architecture/repository-layout.md)
- 上游项目：[`OpenWorker`](https://github.com/andrewyng/openworker)
- 上游管理：[`UPSTREAM.md`](https://github.com/fongap/delta/blob/main/UPSTREAM.md)
- 变更记录：[`CHANGELOG.md`](https://github.com/fongap/delta/blob/main/CHANGELOG.md)
- 开源许可：[`MIT License`](https://github.com/fongap/delta/blob/main/LICENSE)
