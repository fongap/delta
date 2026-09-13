<p align="center">
  <img src="resources/brand/delta-logo-256x256.png" width="96" alt="Delta Logo">
</p>

# Delta

Delta 是一个本地优先的个人工作 AI Agent，只围绕三项核心能力持续做深：

> **日常办公｜研究分析｜内容创作**

目标不是增加模型、Agent 或工具数量，而是把真实工作推进为可检查、可恢复、可验证、可追溯的成果，并把有效工作方法沉淀为可审查、可版本化的 Skills。

## 三项核心能力

### 日常办公

文档、表格、PDF、演示材料、会议材料、邮件 / 消息、资料整理、格式转换、批量处理和轻量自动化。

目标链路：

```text
读取 → 理解 → 修改 / 创建 → 验证 → 预览 → 交付
```

### 研究分析

覆盖从研究问题到正式报告的完整研究闭环：

```text
研究问题
→ 假设 / 目标 / 约束
→ 试验设计（DOE）
→ 序贯试验设计
→ 数据采集 / 清洗
→ 统计分析 / 建模 / 可视化
→ 结果解释 / 不确定性判断
→ 下一轮试验 / 停止判断
→ 正式研究报告
```

重点包括统计推断、样本量 / 功效、随机化、区组、因子设计、筛选设计、响应面、最优设计、**序贯试验设计**、模型更新、下一轮试验推荐和正式研究报告。

### 内容创作

覆盖从选题到发布复盘的完整链路：

```text
选题 → 研究 → 写作 → 编辑 → 图文 / 图片 / 视频
     → 多平台适配 → 发布准备 → 数据复盘
```

包括长短文、公众号 / 博客 / 社交媒体、图文笔记、信息卡片、封面、信息图、图片生成与处理、短视频脚本 / 分镜 / 字幕 / 轻量生成处理和多平台发布素材。

## 一个 Delta，共享一套 Runtime

三项核心能力不是三个独立 Agent 或三个 Runtime。Delta 保持一个统一主 Agent，通过 Capability 和 Skill 组合完成不同任务。

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

Skill 是 Delta 的主要能力扩展单位。Learning 可以从真实 Run、用户修改、Validation 和最终采用结果中沉淀 Preference、Experience、Failure Memory，并提出 Skill Candidate。

长期原则：

> **能力可以进化，权限不能自行进化。**

Learning 可以改善工作方法、模板、Skill 和工具选择，但不能自动降低 Policy / Approval、改变 Risk Level、扩大 Secrets / Network / Sandbox 权限或绕过 Ledger / Validation。

## 目标架构

核心产品语言目标：

```text
Rust + TypeScript
```

- **TypeScript / React**：Experience / UI；
- **Rust**：Runtime、Trust、Work、Automation、Learning authority 和 Capability Host；
- **Python / PowerShell / Shell**：受控 Worker / Script，用于统计、Office、媒体和其他具体任务，不拥有 Delta 核心状态或控制权。

长期逻辑模块：

```text
Experience
Runtime
Trust
Work
Capability
Automation
Learning
```

这些是职责边界，不要求机械拆成七个 crate、目录或进程。Delta 默认采用本地模块化单体。

完整目标架构见 [`target-architecture.md`](docs/architecture/target-architecture.md)。

## 命名规则

> **独立边界用 `delta-*`，内部职责用语义名；品牌化用于识别产品边界，不用于包装每一个内部模块。**

例如独立 Runtime、协议或 SDK 可以使用 `delta-runtime`、`delta-protocol`、`delta-capability`；Runtime 内部的 `session`、`approval`、`artifact`、`learning` 等继续使用语义名。

## 当前 R6 状态

R1–R5.1 已完成 trusted state、trusted execution、provider core、runtime correctness 和 Human Control 的持续收敛。

R6 已进入执行阶段。PR #203 已落地第一批关键变化：

- Rust `RuntimeHost` 已进入核心 Runtime；
- Tauri Desktop 已可通过 Commands / Events 直接使用 in-process Rust Runtime；
- Desktop 主路径不再需要 `TS → localhost Python sidecar → Rust`；
- Python `SessionManager`、`TurnEngine`、Provider compatibility、部分服务和打包链仍属于后续清理对象；
- R6 architecture guard 已进入 CI，当前仍为迁移期 guard。

后续重点：Application Control Plane → Agent Loop / Provider hard-cut → Capability Workerization → Persistent Python Backend Removal → Hard-Cut Audit。

## 模型边界

Delta 只维护：

```text
OpenAI-compatible
Anthropic-compatible
```

Provider 聚合、多 Key、fallback、权重、额度和区域路由外置给兼容 Endpoint / AI Gateway。

## 开发

```bash
uv sync --locked --extra dev --extra messaging
cd apps/desktop
npm install
npm run tauri dev
```

Tauri Desktop 当前使用 Rust Runtime direct IPC；Python 环境仍用于尚未迁移的兼容层、浏览器开发路径、测试以及未来受控 Worker 生态。详细说明见 [`apps/desktop/README.md`](apps/desktop/README.md)。

## 项目文档

- 产品边界：[`DELTA_BLUEPRINT.md`](docs/DELTA_BLUEPRINT.md)
- 目标架构：[`target-architecture.md`](docs/architecture/target-architecture.md)
- 当前 Runtime 契约：[`runtime-public-contract.md`](docs/architecture/runtime-public-contract.md)
- Capability ABI：[`capability-abi.md`](docs/architecture/capability-abi.md)
- 当前仓库结构与命名：[`repository-layout.md`](docs/architecture/repository-layout.md)
- R6 执行计划：[`r6-execution-plan.md`](docs/governance/r6-execution-plan.md)
- 开发治理：[`development-policy.md`](docs/governance/development-policy.md)
- 贡献说明：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 来源与第三方治理：[`PROVENANCE.md`](PROVENANCE.md)
- 变更记录：[`CHANGELOG.md`](CHANGELOG.md)

当前实现事实以已合并代码、ADR、Runtime contract 和 CHANGELOG 为准；长期目标以 `DELTA_BLUEPRINT.md`、`target-architecture.md` 和 ADR-050 为准。