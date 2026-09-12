# 参与 Delta 开发

本文件说明 Delta 的基本开发流程和当前长期边界。

完整治理规则见：

```text
docs/governance/
```

当前物理仓库结构见：

```text
docs/architecture/repository-layout.md
```

长期目标架构见：

```text
docs/architecture/target-architecture.md
```

## 产品范围

Delta 的一级产品方向固定为：

```text
日常办公
研究分析
内容创作
```

### 日常办公

文档、表格、PDF、演示材料、邮件 / 消息、会议材料、资料整理、格式转换、批量文件处理和轻量自动化等个人办公任务。

### 研究分析

从研究问题、统计 / 研究设计到正式报告的完整闭环，包括：

- 数据导入、清洗和探索；
- 统计推断、建模和可视化；
- 试验设计（DOE）；
- **序贯试验设计**；
- 样本量 / 功效、随机化、区组、筛选、因子设计、响应面和最优设计；
- 模型更新、信息缺口、下一轮试验推荐和停止判断；
- 结果解释、局限和正式分析 / 研究报告。

“研究分析”不是简单问数、汇总或画图，也不局限于分析已经存在的数据。

### 内容创作

面向个人和自媒体的完整内容生产，包括：

- 选题和资料研究；
- 写作、编辑和多平台改写；
- 图文笔记、信息卡片、封面和信息图；
- 图片生成与处理；
- 短视频脚本、分镜、字幕、轻量生成 / 处理和发布素材；
- 发布准备和内容效果复盘。

## 产品范围过滤器

任何新增能力进入实现前，必须明确回答：

> **它直接改善日常办公、研究分析或内容创作中的哪一个真实工作环节？**

如果不能明确回答，不进入产品主线。

PDF / Office、Search、Citation、Validation、Scripting、Automation、Connector、MCP、图片 / 视频模型等属于具体能力或支撑能力，不作为新的一级产品方向单独扩张。

## 产品术语治理

当前有效的 README、产品文档、开发文档、Issue 和 Pull Request 应统一使用：

```text
日常办公
研究分析
内容创作
```

旧一级名称“数据分析”只在历史语境或描述具体数据分析步骤时继续使用，不再作为 Delta 的一级产品方向。

不得把以下名称重新提升为并列产品方向：

- 知识工作；
- 数据分析；
- 研究；
- 资料处理；
- 文档处理；
- PDF / Office；
- 自媒体；
- 图像 / 视频；
- 脚本；
- 自动化；
- Connector；
- MCP；
- Coding。

历史 ADR、CHANGELOG 和 Git 历史保留当时真实用词，不为当前术语治理重写历史。

## Agent、Capability 与 Skill

Delta 默认保持一个统一主 Agent。

三个产品域不对应三个独立 Agent 或 Runtime。

扩展优先级：

```text
Skill / Capability
    > 新 Agent
    > 新 Runtime / 新服务
```

Skill 是主要工作方法扩展单位，可以组合 workflow、required capabilities、permissions、validation、templates 和 optional scripts。

只有真实使用证据证明独立 Agent 具有不可替代的权限、上下文或生命周期价值时，才考虑增加新的长期 Agent family。

## 架构模块

长期逻辑模块固定使用以下职责词汇：

```text
Experience
Runtime
Trust
Work
Capability
Automation
Learning
```

它们是逻辑职责，不要求机械创建七个 crate、目录或进程。

模块划分优先依据：

1. 状态所有权；
2. 决策 / Authority；
3. 安全隔离；
4. 变化速度；
5. 独立演进能力。

没有明确收益，不新增本地微服务、常驻后台或模糊的顶级抽象。

## 语言边界

长期核心产品语言收敛为：

```text
Rust + TypeScript
```

- TypeScript：Experience / UI；
- Rust：Runtime、Trust、Work、Automation、Learning authority 和 Capability Host。

Python、PowerShell、Shell 属于 Worker / Script 工作语言，可以用于研究分析、Office、媒体处理等具体任务，但不得拥有：

- Session；
- Run State；
- Policy / Approval；
- 核心数据库写权限；
- Artifact 正式状态；
- Secrets authority；
- Runtime control plane。

因此“新增 Python Capability”允许；“新增 Python Manager / Store / Runtime authority”原则上禁止。

## 长期硬边界

- Delta 不是 Coding Agent，不建设 IDE、仓库理解、Git / PR、代码审查或大型软件工程产品工作流。
- 保留任务型 Python / PowerShell / Shell Scripting。
- 模型层只维护 OpenAI-compatible 与 Anthropic-compatible 两类协议。
- Provider 聚合、多 Key、fallback、权重、额度和复杂路由不属于 Delta 产品职责。
- Skill、Worker、Connector、Automation 和 Learning 都不得绕开 Trust / Policy / Approval / Ledger / Validation。
- Learning 可以优化方法和 Skill，但不能自行降低权限、改变 Risk Level 或扩大 Network / Secrets / Sandbox 范围。

## R5.1 与后续 R6

R5.1 收敛 Runtime correctness 和 Human Control（Steer / Follow-up / Cancel）。

后续 R6 的目标是控制权和进程拓扑收敛：

```text
产品减法
→ Rust Runtime Host
→ TypeScript 直连 Rust
→ Application Control Plane 迁移
→ Agent Loop 迁移
→ Python capability workerization
→ 常驻 Python Backend 退出
→ Rust + TypeScript hard-cut
```

R6 不是逐行“Python → Rust 翻译”。迁移专业生态，不迁移无必要的实现细节。

## Issues 与 Discussions

Issues 只用于已经可以复现、执行和跟踪的问题；公开 Issue 默认通过「缺陷报告」表单提交。

安装、配置、模型接入、使用和排障问题请放到 Discussions 的「问答」分类；功能、交互、工作流和产品建议请放到「建议」分类。讨论形成明确结论并确认实施后，再转为 Issue 跟踪执行。

安全漏洞不得公开披露，按 [`SECURITY.md`](./SECURITY.md) 的方式报告。

## 分支

所有常规开发从 `main` 创建工作分支并通过 Pull Request 合入。

允许使用：

```text
feat/*
fix/*
refactor/*
ci/*
chore/*
docs/*
test/*
release/*
```

Delta 不维护单一上游，因此不再把 `upstream/*` 作为常规分支类型。

## Commit

推荐格式：

```text
<type>: <description>
```

例如：

```text
fix: handle truncated model streams
docs: update target architecture
refactor: move runtime control to Rust
```

工作分支允许存在临时 Commit。合入 `main` 时默认使用 Squash merge。

## Pull Request

所有进入 `main` 的变更必须通过 Pull Request。

合并前至少满足：

1. `ci-required` 通过；
2. Review conversation 已解决；
3. 不包含真实 Token、Secret、用户私有数据或内部敏感信息；
4. 相关测试完成；
5. 受影响文档同步更新；
6. 符合当前目录和模块职责；
7. 不扩大已冻结产品边界；
8. 新能力明确服务日常办公、研究分析或内容创作；
9. 新能力优先落在 Capability / Skill，而非新增 Runtime / Agent；
10. 不恢复 Python 核心 Authority 或已迁移领域的双 Authority；
11. 不允许 Learning / Automation / Connector 绕过 Trust；
12. 产品术语没有重新引入新的一级产品方向。

涉及 Runtime / Authority 迁移时，还应明确：

- Authority Before；
- Authority After；
- Compatibility；
- Exit Condition；
- Failure / Rollback；
- Tests。

## CI

`ci-required` 是 `main` 的稳定 Required Check。

`layout-check` 用于保护仓库结构和路径一致性。

不得通过删除测试、降低断言、扩大豁免或放宽规则规避真实失败。

质量要求见 `docs/governance/quality-policy.md`。

## 外部项目与代码吸收

Delta 不设单一上游项目。

外部项目只能作为产品、架构或实现参考。任何外部代码、行为语义或架构方案进入 `main` 前，都必须按照 Delta 当前三产品域、Target Architecture、Runtime Authority、许可证和测试要求独立评估。

不得为了跟随任何参考项目恢复已淘汰的目录、协议、Coding 产品形态、兼容层或多 Agent 结构。

## 依赖

依赖升级遵循 `docs/governance/dependency-policy.md`。

Major 升级不得自动合并。

在 R6 收敛期间，新依赖还应说明它属于核心 Rust / TypeScript 产品、还是受控 Worker 生态，避免把 Python 控制面重新固化。

## 发布

正式 Release 只能基于 `main` 创建。

版本 Tag 使用：

```text
vMAJOR.MINOR.PATCH
```

完整发布规则见 `docs/governance/release-policy.md`。

## 文档

正文以中文为主；代码标识、路径、命令、字段、协议和 Git/GitHub 固有术语保持英文。

文件名使用英文 `kebab-case`，并表达长期职责。

不使用 `final`、`latest`、`new`、`v2` 等阶段性名称维护当前有效文档。

文档层级：

- 产品长期边界：`docs/DELTA_BLUEPRINT.md`；
- 目标系统架构：`docs/architecture/target-architecture.md`；
- 当前物理结构：`docs/architecture/repository-layout.md`；
- 当前 Runtime 公共契约：`docs/architecture/runtime-public-contract.md`；
- 迁移治理：`docs/governance/rust-core-migration.md`；
- 历史决策：ADR / CHANGELOG / Git history。

目标文档可以描述尚未完成的目标，但必须显式标记为 Target / R6；当前实现文档只记录已经存在的事实。