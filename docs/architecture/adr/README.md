# Architecture Decision Records (ADRs)

此目录记录 Delta 的架构决策和迁移证据。

ADR 用于回答：

- 当时面对什么问题；
- 为什么做这个决定；
- Authority / Contract 如何变化；
- 后续哪个 ADR 取代或收敛了它。

历史 ADR 不因为当前产品术语或目录变化而重写；当前长期目标以最新 Active / Accepted 决策和目标架构文档为准。

## 状态约定

常见状态：

```text
Proposed
Accepted / Active
Superseded
Deprecated
Removed
```

被后续 hard-cut / convergence ADR 替代的计划或过渡 ADR 仍保留，作为迁移证据。

## 当前长期基线

当前产品与目标架构首先看：

- [`../../DELTA_BLUEPRINT.md`](../../DELTA_BLUEPRINT.md) — 产品边界：**日常办公｜研究分析｜内容创作**；
- [`../target-architecture.md`](../target-architecture.md) — 目标逻辑模块和 Rust + TypeScript 语言职责；
- [`ADR-050-product-runtime-convergence.md`](ADR-050-product-runtime-convergence.md) — post-R5.1 产品与 Runtime 收敛决策；
- [`../runtime-public-contract.md`](../runtime-public-contract.md) — R5.1 当前稳定行为契约；
- [`../../governance/rust-core-migration.md`](../../governance/rust-core-migration.md) — R6 Runtime Convergence 治理。

## ADR 演进索引

为了避免把 README 维护成容易过期的 50 行流水账，按阶段组织。具体文件本身是完整事实来源。

### Foundation / Product Reliability — ADR-001 ～ ADR-009

覆盖：

- Run Event Ledger；
- Approval taxonomy；
- Provider protocol model；
- Reliable Task Runtime；
- Source / Citation；
- Run Analyzer；
- P1 / P2 / P3 基线；
- Delta Core 长期 Rust Control Plane + Capability Worker 方向。

其中 [`ADR-009-delta-core-architecture.md`](ADR-009-delta-core-architecture.md) 是后续 Rust Authority 迁移的基础架构决策。

### R1 State Foundation — ADR-010 ～ ADR-017 + Hard-Cut / Final Convergence ADRs

覆盖 Ledger、Idempotency、Task identity、Run state 和 storage coordination 从 shadow/delegate 到 Rust hard-cut 的迁移。

过渡 ADR 保留历史；最终 Authority 以对应 hard-cut / convergence ADR 为准。

### R2 Trusted Execution — ADR-018 ～ ADR-032

覆盖：

- Artifact；
- Source / Citation；
- Validation；
- Checkpoint；
- Policy；
- Approval。

R2 Final Convergence 确立这些 trusted domains 的 Rust Authority。

### R3 Execution Lifecycle — ADR-033 ～ ADR-040

覆盖：

- Tool lifecycle；
- Resume decision；
- Cancellation；
- Timeout；
- Retry；
- execution lifecycle convergence。

部分早期审计 ADR 被后续 authority ADR 修正或 supersede，历史保留不删。

### R4 Runtime Migration — ADR-041 ～ ADR-046

覆盖：

- Run lifecycle transition；
- Automation completion；
- Scheduler audit；
- Resume orchestration audit；
- R4 Final Convergence。

### R5 Provider Core — ADR-047 ～ ADR-048

- [`ADR-047-r5-provider-core-migration-plan.md`](ADR-047-r5-provider-core-migration-plan.md) — provider transport / decision migration plan；
- [`ADR-048-r5-final-convergence.md`](ADR-048-r5-final-convergence.md) — OpenAI-compatible / Anthropic-compatible transport 和主要 provider decision authority 收敛到 Rust，protocol v15。

### R5.1 Runtime & Human Control — ADR-049

[`ADR-049-r5.1-runtime-and-human-control-convergence.md`](ADR-049-r5.1-runtime-and-human-control-convergence.md)

完成：

- AF-01..AF-14 Runtime correctness closure；
- protocol v16 multiplexing；
- request identity / demux / real cancel / backpressure；
- single completion owner；
- side-effect fail-closed；
- citation validity 修正；
- first-class Steer / Follow-up / Cancel Human Control。

R5.1 不是 R6，也不表示 Python application backend 已退出。

### Post-R5.1 Target / R6 — ADR-050

[`ADR-050-product-runtime-convergence.md`](ADR-050-product-runtime-convergence.md)

冻结长期方向：

- 一级产品域：**日常办公｜研究分析｜内容创作**；
- 研究分析包含 DOE 和从第一版纳入的序贯试验设计；
- 内容创作包含文本、图文、图片和视频；
- 一个主 Delta Agent，Skill / Capability 是主要扩展方式；
- 逻辑模块：Experience / Runtime / Trust / Work / Capability / Automation / Learning；
- 核心产品语言目标：Rust + TypeScript；
- Python / PowerShell / Shell 作为受控 Worker / Script；
- Learning 可以改进能力，不能自行扩大权限；
- 默认模块化单体；
- R6 收敛 Python application/control plane，不机械重写专业 Worker 生态。

## 其他当前架构文档

- [`../target-architecture.md`](../target-architecture.md) — 长期目标系统形态；
- [`../repository-layout.md`](../repository-layout.md) — 当前物理仓库结构；
- [`../runtime-public-contract.md`](../runtime-public-contract.md) — 当前 Runtime / Human Control contract；
- [`../capability-abi.md`](../capability-abi.md) — Rust Capability Host ↔ Worker / MCP / Connector / External Adapter；
- [`../hub-federation-boundary.md`](../hub-federation-boundary.md) — 外部托管 / federation 边界；
- [`../relay-mode-removal.md`](../relay-mode-removal.md) — relay compatibility removal 记录。

## 维护规则

1. 已发生的历史决策不为匹配当前术语而改写。
2. 新 target decision 如果改变长期产品 / Authority / 安全边界，新增 ADR。
3. 单纯同 contract 的 Python → Rust implementation replacement 通常不需要重新发明领域模型，但需要 migration PR 和 contract tests。
4. ADR 索引保持阶段化和简洁；详细 PR / test 数量留在具体 ADR、PR 和审计文档。
