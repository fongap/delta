# Delta 治理

本目录保存 Delta 当前有效的长期治理规则。

治理目标统一服从：

```text
长期 | 稳定 | 高效 | 安全 | 敏捷
```

## 权威入口

- [`../DELTA_BLUEPRINT.md`](../DELTA_BLUEPRINT.md)：产品边界与三项核心能力；
- [`../architecture/target-architecture.md`](../architecture/target-architecture.md)：长期目标系统形态；
- [`../architecture/runtime-public-contract.md`](../architecture/runtime-public-contract.md)：稳定 Runtime / Human Control contract；
- [`../architecture/repository-layout.md`](../architecture/repository-layout.md)：当前物理结构与命名治理；
- [`../architecture/capability-abi.md`](../architecture/capability-abi.md)：Capability / Worker 边界；
- [`r6-execution-plan.md`](r6-execution-plan.md)：R6 当前执行状态、阶段和 Exit Condition。

## 治理文档

- [`development-policy.md`](development-policy.md)：开发、架构、命名和文档治理；
- [`quality-policy.md`](quality-policy.md)：CI、测试、质量门禁和安全验证；
- [`dependency-policy.md`](dependency-policy.md)：依赖治理；
- [`release-policy.md`](release-policy.md)：版本、构建和 Release；
- [`r6-execution-plan.md`](r6-execution-plan.md)：当前 Runtime Convergence 执行计划。

不再单独维护一份与执行计划重复的 `rust-core-migration.md`。长期原则已经进入 `development-policy.md`、`target-architecture.md` 和 `runtime-public-contract.md`；R6 阶段进度只由 `r6-execution-plan.md` 维护。

## 核心治理原则

1. 一级产品方向只有 **日常办公、研究分析、内容创作**。
2. 新能力优先作为 Skill / Capability，不优先增加 Agent、Runtime 或常驻服务。
3. 核心产品语言目标为 Rust + TypeScript；Python / PowerShell / Shell 是受控 Worker / Script。
4. 一个领域只有一个 Authority；完成迁移后删除旧 owner / forwarding path。
5. Worker、Skill、Connector、Automation、Learning 不得绕过 Trust / Policy / Approval / Ledger / Validation。
6. Learning 可以改善能力，不能自行扩大权限。
7. R6 迁移控制权和进程拓扑，不机械重写专业 Python 生态。
8. 当前实现和目标架构必须明确区分。
9. **独立边界用 `delta-*`，内部职责用语义名；品牌化用于识别产品边界，不用于包装每一个内部模块。**

## 文档治理

- 当前 active 文档只保留一个明确主题；
- 已完成且不再定义当前状态的一次性迁移说明直接删除；
- 历史事实由 Git / PR / ADR / CHANGELOG / audits 追溯；
- 不创建 `latest`、`final`、`new`、`v2` 副本；
- ADR 保留历史，不为匹配当前术语而改写；
- operations 只放平台和维护操作，不承担产品 / 架构定义。

## 文档分层

```text
Product        → DELTA_BLUEPRINT.md
Target         → target-architecture.md
Current facts  → runtime-public-contract.md / repository-layout.md
Execution      → r6-execution-plan.md
Decisions      → architecture/adr/
Evidence       → audits/
Operations     → operations/
Version record → CHANGELOG.md
```

治理规则应简洁、稳定、可执行。能通过 CI / Ruleset 强制的内容，文档只描述目标和边界，不重复维护脆弱操作步骤。