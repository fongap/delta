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
- `ADR-010-r1-state-foundation-shadow-read.md` – R1 State Foundation 第一刀：`core/runtime-native/` crate scaffold + Ledger/IdempotencyLog shadow-read（只读验证 Python 写入的 hash chain + side effect state machine）。不写入，不替换 Python 权威。
- `ADR-011-r1-authority-switch-plan.md` – R1 authority switch 计划与边界：明确"先不立即切换权威"、未来每个领域一个 PR + PR-level ADR；强制 Pre-R1 plumbing（PR11）必须先做；列出 5 个领域（Idempotency / Ledger / Run state / Task identity / Storage transaction boundary）的迁移顺序与不变量。
- `ADR-012-r1-pre-plumbing.md` – R1 Pre-R1 plumbing：`DELTA_RUST_AUTHORITY` env-var 灰度开关 + CI guard + 第三个领域 inspect binary。
- `ADR-013-r1-idemlog-authority-switch.md` – R1 Idempotency authority switch 阶段 A：Rust `IdempotencyWriter` 写路径就位 + 跨语言测试。不切换权威。
- `ADR-014-r1-idemlog-delegate.md` – R1 Idempotency authority switch 阶段 B：`core/idemlog_delegate.py` 提供 `IdempotencyLogWithDelegate` opt-in wrapper + `maybe_wrap` 工厂。**默认行为完全等同 main**；env var `DELTA_RUST_AUTHORITY=1` + Rust binary 存在时显式委托写。
- `ADR-015-r1-ledger-delegate.md` – R1 Ledger authority switch 阶段 B：`core/ledger_delegate.py` 提供 `RunEventLedgerWithDelegate` + `maybe_wrap_ledger`；Rust `LedgerWriter` 镜像 Python `RunEventLedger.append` 的 hash basis (`sha256(prev_hash|seq|type|actor|repr(ts)|canonical(payload))`)；CI enforcement guard 扩展到 ledger 域。
- `ADR-016-r1-taskstore-delegate.md` – R1 Task identity authority switch 阶段 B：`core/automation/store_delegate.py` 提供 `TaskStoreWithDelegate` + `maybe_wrap_taskstore`；Rust `TaskStoreWriter` 镜像 Python `TaskStore` 的 save/delete/add_run 写路径；CI enforcement guard 扩展到 task_identity 域。
- `ADR-017-r1-state-foundation-completion.md` – R1 State Foundation 完成记录：5 个领域（Idempotency / Ledger / Task identity / Run state / Storage transaction boundary）权威路径全部就位 + 统一 `delta_core` 进程入口 + CI smoke gate。灰度开关默认关闭；下一步 R2。
- `ADR-018-r2-trusted-execution-plan.md` – R2 Trusted Execution 计划与边界：明确"先不立即切换 R2 域权威"、未来每个领域一个 PR + PR-level ADR；强制 Pre-R2 plumbing（PR131）必须先做；列出 6 个 R2 域（Artifact / Validation / Checkpoint / Policy / Approval / Source-Citation）的迁移顺序、风险评估与不变量。

相关架构文档：
- `hub-federation-boundary.md` – Delta Hub 联邦化边界设计，明确 OpenWorker 仅为可选适配器。
- `relay-mode-removal.md` – `mode: "relay"` 运行时路径最终移除的完成记录（2026-09 已 closed；P2 终止了 Inbox 旧 token 解析兼容；未来 Federation Adapter 位于 `integrations/managed/adapters/<provider>.py`）。
- `runtime-public-contract.md` – v0.3.2 之后 Python Runtime 的稳定契约（Task/Run/RunEvent/Approval/SideEffect/Artifact/Validation/Recovery/Source/Citation + HTTP 端点）；任何破坏性变更必须先经 ADR；Rust Core 迁移时权威从 Python 转向 Rust，但契约保持稳定。
- `capability-abi.md` – Delta Rust Core ↔ Capability Worker 的稳定交互边界（JSON-RPC / NDJSON over stdio + Data Plane handles + Capability Manifest + Progress / Heartbeat / Cancellation / Typed Error / Retry / Approval / Discovery / MCP Adapter / 版本兼容 / 安全 / 验收）。
- `repository-layout.md` – 仓库目录规范。
