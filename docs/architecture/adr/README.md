# Architecture Decision Records (ADRs)

此目录收录 Delta 当前**有效**的架构决策记录。每个 ADR 使用 `ADR-XXX-<title>.md` 命名，遵循以下约定：
- **Status**: `Active`（仍在使用），`Superseded`（已被后续 ADR 替代），`Deprecated`（不再推荐），`Removed`（已从代码中删除）。
- 内容包括 **Context**, **Decision**, **Consequences**，以及代码实现位置的引用（文件:行号）。

本目录仅保存 **长期有效** 的设计决策。已失效或仅用于一次性整改的文档应在对应代码提交历史中保留，不在此目录维护。

当前收录的 ADR：
- `ADR-001-run-event-ledger.md` – 持久化、哈希链式运行事件记录。
- `ADR-002-approval-taxonomy.md` – 风险等级与自动审批模型。
- `ADR-003-provider-protocol-model.md` – Provider 的 Vendor / Protocol / Transport 三层概念。
- `ADR-004-openworker-decouple-hub-boundary.md` – 移除 OpenWorker Cloud 运行时依赖并建立 Delta Hub Capability Port 边界。
- `ADR-005-reliable-task-runtime.md` – 可靠任务运行时：验证门控、Artifact 领域对象、副作用安全恢复、Ledger 词汇表扩展。
- `ADR-006-p2-source-citation-and-convergence.md` – P2 实用：Source / Citation 提升为可定位的一类证据、Automation 收敛的结构化守护、Inbox 解析方向终止兼容。
- `ADR-007-p3-readonly-run-analyzer.md` – P3 长期「智能」第一刀：只读 Run Analyzer（`RunEventLedger` + `SourceStore` + `TaskRun` 的 query 层），零行为变更、不动 Skill / Memory / Preference 任何活跃状态，为 §7.3 治理链的 Evidence 步提供可复用基础。
- `ADR-008-p1p2p3-baseline.md` – P1 / P2 / P3 阶段第一刀基线说明：12 个 PR 的快照、关键设计契约、蓝图 §8.9 / §7.3 明确延后或条件限定的项、下一刀"不是 PR 而是真实使用"的判定标准。
- `ADR-009-delta-core-architecture.md` – Delta Core 长期架构：Rust Control Plane + Capability Worker；明确 Rust Core 唯一权威的领域（Task/Run/Policy/Approval/Ledger/Checkpoint/Artifact/Validation/Idempotency/Scheduler/Worker lifecycle/Provider Core）与 Python 端降级为 Worker 的边界；与 `docs/governance/rust-core-migration.md` + `docs/architecture/capability-abi.md` 共同构成 Rust 迁移的"先冻结契约再迁权威"基础。

相关架构文档：
- `hub-federation-boundary.md` – Delta Hub 联邦化边界设计，明确 OpenWorker 仅为可选适配器。
- `relay-mode-removal.md` – `mode: "relay"` 运行时路径最终移除的完成记录（2026-09 已 closed；P2 终止了 Inbox 旧 token 解析兼容；未来 Federation Adapter 位于 `integrations/managed/adapters/<provider>.py`）。
- `runtime-public-contract.md` – v0.3.2 之后 Python Runtime 的稳定契约（Task/Run/RunEvent/Approval/SideEffect/Artifact/Validation/Recovery/Source/Citation + HTTP 端点）；任何破坏性变更必须先经 ADR；Rust Core 迁移时权威从 Python 转向 Rust，但契约保持稳定。
- `capability-abi.md` – Delta Rust Core ↔ Capability Worker 的稳定交互边界（JSON-RPC / NDJSON over stdio + Data Plane handles + Capability Manifest + Progress / Heartbeat / Cancellation / Typed Error / Retry / Approval / Discovery / MCP Adapter / 版本兼容 / 安全 / 验收）。
- `repository-layout.md` – 仓库目录规范。
