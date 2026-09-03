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

相关架构文档：
- `hub-federation-boundary.md` – Delta Hub 联邦化边界设计，明确 OpenWorker 仅为可选适配器。
- `relay-mode-removal.md` – `mode: "relay"` 运行时路径最终移除的完成记录（2026-09 已 closed；P2 终止了 Inbox 旧 token 解析兼容；未来 Federation Adapter 位于 `integrations/managed/adapters/<provider>.py`）。
