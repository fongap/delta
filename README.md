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

Delta 是一个本地优先的个人工作 AI Agent，只围绕三项核心能力持续做深：

> **日常办公｜研究分析｜内容创作**

目标不是增加模型、Agent 或工具数量，而是把真实工作从需求推进到可检查、可恢复、可验证、可追溯的成果，并在长期使用中把有效工作方法沉淀为可审查、可版本化的 Skills。

<p align="center">
  <img src="resources/screenshots/Delta-2026082401.webp" width="95%" alt="Delta">
</p>

## 三项核心能力

### 日常办公

围绕个人高频办公成果形成完整闭环：文档、表格、PDF、演示材料、邮件 / 消息、会议材料、资料整理、格式转换、批量文件处理和轻量自动化。

目标不是只“读取文件”，而是：

```text
读取 → 理解 → 修改 / 创建 → 验证 → 预览 → 交付
```

### 研究分析

“研究分析”不是简单问数、汇总或画图，也不局限于分析已经存在的数据。它覆盖从研究问题到正式报告的完整研究闭环：

```text
研究问题
  ↓
假设 / 目标 / 约束
  ↓
试验设计（DOE）
  ↓
序贯试验设计
  ↓
数据采集 / 导入 / 清洗
  ↓
统计分析 / 建模 / 可视化
  ↓
结果解释 / 不确定性判断
  ↓
下一轮试验 / 停止判断
  ↓
正式研究报告
```

重点包括统计推断、样本量 / 功效、随机化、区组、因子设计、筛选设计、响应面、最优设计、**序贯试验设计**、模型更新、下一轮试验推荐和正式研究报告。

### 内容创作

覆盖个人和自媒体内容从选题到发布复盘的完整链路：

```text
选题 → 研究 → 写作 → 编辑 → 图文 / 图片 / 视频
     → 多平台适配 → 发布准备 → 数据复盘
```

包括长短文、公众号 / 博客 / 社交媒体内容、图文笔记、信息卡片、封面、信息图、图片生成与处理、短视频脚本 / 分镜 / 字幕 / 轻量生成处理，以及多平台发布素材。

## 一个 Delta，共享一套 Runtime

三项核心能力不是三个独立 Agent 或三个 Runtime。

Delta 默认保持一个统一工作 Agent，通过 Capability 和 Skill 组合完成不同任务：

```text
                 Delta
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
     日常办公     研究分析     内容创作
        └──────────┼──────────┘
                   ▼
               Capability
                   │
                Runtime
```

PDF / Office、Search、Citation、Validation、Scripting、Automation、Connector、MCP、图像 / 视频模型等属于具体能力或支撑能力，不提升为新的一级产品方向。

## Skill 与受控学习

Skill 是 Delta 的主要能力扩展单位，而不是不断增加专用 Agent。

一个 Skill 可以组合：

- instructions / workflow；
- required capabilities；
- permissions；
- validation；
- templates；
- optional scripts；
- version / evaluation metadata。

Delta 可以从真实 Run、用户修改、Validation 和最终采用结果中沉淀 Preference、Experience、Failure Memory，并提出 Skill Candidate。

长期原则：

> **能力可以进化，权限不能自行进化。**

Learning 可以改善工作方法、模板、Skill 和工具选择，但不能自动降低 Policy / Approval、改变 Risk Level、扩大 Secrets / Network / Sandbox 权限或绕过 Ledger / Validation。

## 目标架构

Delta 的长期核心产品语言收敛为：

```text
Rust + TypeScript
```

- **TypeScript / React**：负责用户交互、任务进度、Steering、审批、预览、编辑和设置；
- **Rust**：负责 Runtime、状态、Policy、Ledger、Artifact、Validation、Automation、Learning authority 和 Capability Host；
- **Python / PowerShell / Shell**：保留为受控 Worker / Script，用于统计分析、Office、媒体处理和其他具体任务，不拥有 Delta 核心状态或控制权。

长期逻辑模块为：

```text
Experience
Runtime
Trust
Work
Capability
Automation
Learning
```

这七个名称表示稳定职责，不要求机械拆成七个 crate、七个目录或七个进程。Delta 默认采用本地模块化单体，没有真实隔离需求时不引入额外微服务。

完整目标架构见 [`target-architecture.md`](docs/architecture/target-architecture.md)。

## 产品与架构硬边界

- **不做 Coding Agent**：不以 IDE、仓库理解、Git / PR、代码审查和大型软件工程任务作为产品主线。
- **保留任务型脚本**：Python、PowerShell、Shell 是完成工作的执行手段，而不是新的产品方向。
- **模型协议固定两类**：只维护 OpenAI-compatible 与 Anthropic-compatible。
- **复杂模型路由外置**：Provider 聚合、多 Key、fallback、权重、额度和区域路由由兼容 Endpoint / AI Gateway 负责。
- **一个领域一个 Authority**：Worker、Skill、Connector、Learning 不得直接拥有核心 Runtime / Trust / Work 事实。
- **优先扩 Capability / Skill**：没有充分证据，不增加新的 Runtime、常驻服务、协议、产品 Agent 或顶级抽象。

## 当前与下一阶段

R1–R5 已逐步把 trusted state、execution decision 和 provider authority 收敛到 Rust；R5.1 进一步收口 Runtime correctness 与实时的人机控制（Steer / Follow-up / Cancel）。

R5.1 之后的 R6 目标不是“把所有 Python 翻译成 Rust”，而是完成控制权和进程拓扑收敛：

```text
产品减法
  ↓
Rust Runtime Host
  ↓
TypeScript → Rust direct IPC
  ↓
Application Control Plane 迁移
  ↓
Agent Loop 迁移
  ↓
Python capability workerization
  ↓
常驻 Python Backend 退出
  ↓
Rust + TypeScript hard-cut
```

迁移期间，Office / Research / Media 的 Python 生态优势继续保留为 Worker 能力。

## 模型协议

Delta 的模型协议层固定为：

```text
OpenAI-compatible
Anthropic-compatible
```

具体模型和服务商不构成 Delta 的产品边界。只要 Endpoint 符合上述协议之一，即可由用户按实际需要配置。

## 安装部署

### 便携版（推荐）

从 [Releases](https://github.com/fongap/delta/releases) 下载 `Delta-Windows-Portable.zip`，解压到任意目录（支持中文和空格路径），运行 `Delta.exe` 即可：

- 所有数据（配置、密钥、日志、数据库）保存在 `Data/` 目录下，随文件夹整体迁移，不写入 `%APPDATA%`。
- 首次启动自动创建 `Data/` 并引导配置模型 Endpoint。
- 便携版不内置 WebView2 Runtime；Windows 10/11 通常已安装，缺少时需单独安装。

### 本地开发

当前实现仍处于 Rust Control Plane + Python application/runtime compatibility layer + TypeScript Desktop 的过渡阶段，因此开发环境暂时需要 Python 3.11+、`uv`、Node.js 和 Rust toolchain。

```bash
uv sync --locked --extra dev --extra messaging
cd apps/desktop
npm install
npm run tauri dev
```

当前 Tauri 开发模式仍使用仓库 `.venv` 中的 `delta-server`；这属于 R6 前的过渡实现，不代表目标架构继续保留常驻 Python Backend。

详细开发说明见 [`apps/desktop/README.md`](apps/desktop/README.md)。

## 项目文档

- 产品与能力蓝图：[`DELTA_BLUEPRINT.md`](docs/DELTA_BLUEPRINT.md)
- 目标架构：[`target-architecture.md`](docs/architecture/target-architecture.md)
- Capability ABI：[`capability-abi.md`](docs/architecture/capability-abi.md)
- 当前 Runtime 契约：[`runtime-public-contract.md`](docs/architecture/runtime-public-contract.md)
- Rust 收敛治理：[`rust-core-migration.md`](docs/governance/rust-core-migration.md)
- 开发说明：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 当前仓库结构：[`repository-layout.md`](docs/architecture/repository-layout.md)
- 变更记录：[`CHANGELOG.md`](CHANGELOG.md)
- 开源许可：[`MIT License`](LICENSE)

当前实现事实以已合并 ADR、Runtime contract 和 CHANGELOG 为准；目标架构以 `DELTA_BLUEPRINT.md`、`target-architecture.md` 和 ADR-050 为准。