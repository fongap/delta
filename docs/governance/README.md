# Delta 治理

本目录保存 Delta 当前有效的长期治理规则。

治理目标统一服从五个长期标准：

```text
长期 | 稳定 | 高效 | 安全 | 敏捷
```

治理文档规定持续遵循的开发、质量、依赖、发布和 Runtime 收敛规则，不记录一次性整改过程或临时操作步骤。

## 产品与架构基线

在阅读治理规则前，先明确两份长期基线：

- [`../DELTA_BLUEPRINT.md`](../DELTA_BLUEPRINT.md)：产品与能力边界，一级产品方向为 **日常办公｜研究分析｜内容创作**；
- [`../architecture/target-architecture.md`](../architecture/target-architecture.md)：目标系统架构，核心产品语言为 **Rust + TypeScript**，逻辑模块为 Experience / Runtime / Trust / Work / Capability / Automation / Learning。

当前物理仓库结构和当前 Runtime contract 分别见：

- [`../architecture/repository-layout.md`](../architecture/repository-layout.md)
- [`../architecture/runtime-public-contract.md`](../architecture/runtime-public-contract.md)

## 文档

- [`development-policy.md`](development-policy.md)：分支、Commit、PR、Review、Merge、产品 / 模块分类与文档同步；
- [`quality-policy.md`](quality-policy.md)：CI、测试、质量门禁、安全与 R6 contract 验证；
- [`release-policy.md`](release-policy.md)：版本、Tag、构建和 Release；
- [`dependency-policy.md`](dependency-policy.md)：核心 Rust / TypeScript 与 Capability Worker 依赖治理；
- [`rust-core-migration.md`](rust-core-migration.md)：R5.1 后 Runtime Convergence / R6 长期治理，规定 Python 控制面冻结、Authority 迁移、Worker 化与 hard-cut 标准；
- [`r6-execution-plan.md`](r6-execution-plan.md)：基于 R5.1 最终修复态代码审计形成的 R6 实际执行顺序、当前迁移面和阶段 Exit Condition。

## 当前核心治理原则

1. 一级产品方向只有 **日常办公、研究分析、内容创作**。
2. 新能力优先作为 Skill / Capability 扩展，不优先增加 Agent、Runtime 或常驻服务。
3. 核心产品语言目标为 Rust + TypeScript；Python / PowerShell / Shell 是受控 Worker / Script。
4. 一个领域只能有一个 Authority；已经 hard-cut 的领域不得恢复 Python writer 或双主控。
5. Worker、Skill、Connector、Automation 和 Learning 都不能绕过 Trust / Policy / Approval / Ledger / Validation。
6. Learning 可以改进方法和 Skill，但不能自行扩大权限。
7. R6 迁移的是控制权和进程拓扑，不机械重写专业 Python 生态。
8. 当前实现和目标架构必须明确区分，不能把 Target 写成已完成事实。

## 文档原则

1. 正文以中文为主。
2. 文件名使用英文 `kebab-case`。
3. 代码、命令、路径、配置字段、协议名和 Git/GitHub 固有名称保持英文。
4. 文件名描述长期职责，不描述某次工作状态。
5. 一个文件承担一个明确主题。
6. 已失效且无长期价值的当前文档直接删除；历史通过 Git / PR / ADR / CHANGELOG 追溯。
7. 现有规则变化时修改原文件，不创建 `latest` / `final` / `v2` 副本。
8. 当前事实、目标状态和历史决策分别放到对应文档层级。

禁止：

```text
final-report.md
latest-policy.md
new-governance.md
development-policy-v2.md
misc.md
```

## 维护原则

治理规则应简洁、稳定、可执行。

如果某条规则能通过 CI / Ruleset 自动强制，文档说明目标和边界即可，不重复维护脆弱的操作步骤。

平台操作放 `docs/operations/`；架构事实和决策放 `docs/architecture/`；审计闭环证据放 `docs/audits/`。