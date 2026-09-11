# 参与 Delta 开发

本文件说明 Delta 的基本开发流程。

完整治理规则见：

```text
docs/governance/
```

仓库结构见：

```text
docs/architecture/repository-layout.md
```

## 产品范围

任何新增能力在进入实现前，应先判断是否直接改善 Delta 的核心知识工作场景：办公、数据分析、研究、文档处理和内容生产。

长期边界如下：

- Delta 不是 Coding Agent，不建设 IDE、代码仓库理解、Git / PR、代码审查或大型软件工程工作流。
- Delta 保留任务型脚本能力。Python、PowerShell、Shell 等脚本用于完成办公、数据、资料和内容任务，不作为独立软件开发产品线。
- 模型层仅维护 OpenAI-compatible 与 Anthropic-compatible 两类协议，不新增第三类原生模型协议。
- Provider 聚合、复杂模型路由、fallback 和权重调度不属于 Delta 的产品职责。
- 新功能不得绕开统一的 Workspace Boundary、Policy、Approval、Run State、Artifact 和 Validation 语义。

产品与架构长期边界以：

```text
docs/DELTA_BLUEPRINT.md
```

为准。

## Issues 与 Discussions

Issues 只用于已经可以复现、执行和跟踪的问题；公开 Issue 默认通过「缺陷报告」表单提交。

安装、配置、模型接入、使用和排障问题请放到 Discussions 的「问答」分类；功能、交互、工作流和产品建议请放到「建议」分类。讨论形成明确结论并确认实施后，再转为 Issue 跟踪执行。

安全漏洞不得公开披露，按 [`SECURITY.md`](./SECURITY.md) 的方式报告。

## 分支

所有开发均从 `main` 创建工作分支。

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

各前缀含义和使用规则见：

```text
docs/governance/development-policy.md
```

## Commit

推荐格式：

```text
<type>: <description>
```

例如：

```text
fix: handle truncated model streams
docs: update repository governance
refactor: split provider routing
```

工作分支允许存在临时 Commit。

合入 `main` 时默认使用 Squash merge，保持主分支历史清晰。

## Pull Request

所有进入 `main` 的变更必须通过 Pull Request。

合并前至少应满足：

1. `ci-required` 通过
2. 需要处理的 Review conversation 已解决
3. 不包含真实 Token、Secret、用户私有数据或内部敏感信息
4. 相关测试已完成
5. 受影响文档已同步更新
6. 符合当前仓库目录和模块职责要求
7. 不扩大已冻结的产品边界

## CI

`ci-required` 是 `main` 的稳定 Required Check。

`layout-check` 用于保护仓库结构和路径一致性。

不得通过删除测试、降低断言、扩大豁免或放宽既有规则来规避真实 CI 失败。

质量要求见：

```text
docs/governance/quality-policy.md
```

## 外部项目与代码吸收

Delta 不设单一上游项目。

外部项目只能作为设计或实现参考。任何外部代码、行为语义或架构方案进入 `main` 前，都必须按照 Delta 当前产品边界、Runtime Authority、许可证和测试要求独立评估，并通过普通 Pull Request 引入。

不得为了跟随任何参考项目而恢复已淘汰的目录、协议、产品形态或兼容层。

## 依赖

依赖升级应遵循：

```text
docs/governance/dependency-policy.md
```

Major 升级不得自动合并。

低风险依赖更新是否允许自动合并，应由明确的 Dependabot 和 CI 策略决定。

## 发布

正式 Release 只能基于 `main` 创建。

版本 Tag 使用：

```text
vMAJOR.MINOR.PATCH
```

例如：

```text
v0.3.0
v1.0.0
```

完整发布规则见：

```text
docs/governance/release-policy.md
```

## 文档

正文以中文为主。

以下内容保持英文：

* 代码标识
* 文件名和路径
* 命令
* 配置字段
* 协议名称
* Git/GitHub 固有术语

文件名应使用英文 `kebab-case`，并表达长期职责。

不再使用 `final`、`latest`、`new`、`v2` 等阶段性名称维护当前有效文档。

产品定位、长期能力边界和“不做什么”写入 `docs/DELTA_BLUEPRINT.md`；具体实现状态只记录已经完成的事实，不提前把进行中的迁移写成完成态。
