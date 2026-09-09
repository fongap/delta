# ADR-018 — R2 Trusted Execution: 计划与边界

- 状态：Accepted
- 日期：2026-09-07
- 范围：Delta Core 权威归属迁移 R2 阶段（Python → Rust）
- 决策类型：迁移阶段规划
- 关联：ADR-009（Delta Core Architecture）、ADR-011（R1 Authority Switch Plan，已 Superseded by ADR-017）、ADR-017（R1 State Foundation 完成）、`docs/governance/rust-core-migration.md` §5 R2 / §6 单领域迁移流程 / §7 双写规则 / §11 PR 规则

> 2026-09-08 后续决策：ADR-021 取代本 ADR 对 Source/Citation “只读影子、不存在权威切换语义”的定位，并暂停原计划中随后的 Approval / Policy / Checkpoint 权威迁移。新顺序为 Source/Citation → Validation → Trusted Execution Convergence → Runtime Boundary Audit → Product Reality Gate。

> 2026-09-09 后续决策：ADR-026 完成 Artifact Registry Hard-Cut。Artifact 域现在由 Rust `delta_core` 作为唯一 Authority（`core/artifact_delegate.py` 已删除，`artifact` 已从 `RUST_WRITE_DOMAINS` 移除，无 fallback / delegate / feature flag）。下一域为 Source/Citation。

> 2026-09-10 后续决策：ADR-027 完成 Source/Citation Hard-Cut。Source/Citation 域现在由 Rust `delta_core` 作为唯一 Authority（`core/source_citation_delegate.py` 已删除，`source_citation` 已从 `RUST_WRITE_DOMAINS` 移除，无 fallback / delegate / feature flag）。下一域为 Validation。

> 2026-09-10 后续决策：ADR-028 完成 Validation Hard-Cut。Validation 域现在由 Rust `delta_core` 作为唯一 Authority（`core/validation_delegate.py` 已删除，`validation` 在 `RUST_WRITE_DOMAINS` 中，无 fallback / delegate / feature flag）。下一域为 Checkpoint。
>
> 2026-09-10 后续决策：ADR-029 完成 Checkpoint Hard-Cut。Checkpoint 域现在由 Rust `delta_core` 作为唯一 Authority（`core/recovery.py` 是薄门面，`inspect_checkpoint` 已删除，`checkpoint` 在 `RUST_WRITE_DOMAINS` 中，无 fallback / delegate / feature flag）。下一域为 Policy。
> >
> > 2026-09-10 后续决策：ADR-030 完成 Policy Hard-Cut。Policy 域现在由 Rust `delta_core` 作为唯一 Authority（`core/gateway.py` 是薄门面，`enforce_level`/`restrict_grants`/`enforce_scope` Python 实现已删除，`policy` 在 `RUST_WRITE_DOMAINS` 中，无 fallback / delegate / feature flag）。下一域为 Approval。
> >
> > 2026-09-09 后续决策：ADR-031 完成 Approval Hard-Cut。Approval 审计写入域现在由 Rust `delta_core` 作为唯一 Authority（`core/approval.py` 是薄门面，`core/audit.py` 的 `append()` 委托给 Rust，交互式决策保留在 Python `engine.py`，`approval` 在 `RUST_WRITE_DOMAINS` 中，无 fallback / delegate / feature flag）。R2 全部 6 个域完成 hard-cut。

## 背景

R1 State Foundation（ADR-017）已在 2026-09-07 完成：5 个领域（Idempotency / Ledger / Task identity / Run state / Storage transaction boundary）的 Rust delegate wrapper + 统一 `delta_core` 进程入口 + CI gate 全部就位。Rust authority 能力可用（opt-in via `DELTA_RUST_AUTHORITY`），但生产默认权威仍是 Python（Pre-R2 Gate / R1.7 明确的三态澄清）。

R1.5 收口（PR #127）+ R1.6 Legacy Cleanup（PR #128）+ R1.7 Pre-R2 Gate（PR #129）建立了迁移期的基础设施纪律：

- `packages/storage_authority.py` 拆分 `RUST_WRITE_DOMAINS` / `DERIVED_DOMAINS` / `COORDINATION_DOMAINS`，未知 domain 抛 `InvalidAuthorityTargetError`（fail-closed）；
- `DeltaCoreClient` 在 subprocess 启动时发送 `hello` handshake，协议版本不匹配直接 fail-closed；
- Legacy branding gate（`check_legacy_branding.py`）含 identifier-aware 规则；
- R1 三个 PR-level ADR（014/015/016）标记 "Accepted (历史)"，R1 计划 ADR（011/013）标记 "Superseded by ADR-017"。

R2 范围（`rust-core-migration.md` §5 R2）包含 6 个领域：

1. **Artifact Registry** ✅ **Completed (ADR-026)** — `core/artifact.py`（`Artifact` dataclass + `register_artifact` helper + `register_run_artifacts` walker）；
2. **Source/Citation** ✅ **Completed (ADR-027)** — `core/sources.py` + `core/citation.py` + `core/analyzer.py:source_citation_hits`；
3. **Validation** ✅ **Completed (ADR-028)** — `core/validation.py`（`ValidationCriteria` / `ValidationCheck` / `ValidationResult`）；
4. **Checkpoint** ✅ **Completed (ADR-029)** — `core/recovery.py` 的快照（Rust `checkpoint.registered` ledger event）；
5. **Policy** ✅ **Completed (ADR-030)** — `core/gateway.py` 四切片（`classify` + `enforce_level` + `restrict_grants` + `enforce_scope`）统一由 Rust `policy.evaluate` 评估；
6. **Approval** ✅ **Completed (ADR-031)** — `core/audit.py` 审计写入（`audit_events` INSERT）由 Rust `approval.record` 持久化；交互式决策（`ApprovalOutcome` / `PermissionRequest` / `Approver`）保留在 Python `engine.py`。

**R2 不允许"全栈切换"**，必须按 `rust-core-migration.md` §6 单领域流程逐步推进。

## 决策

R2 Trusted Execution **不立即开始单领域切换**。本 ADR 提议**先做规划与 Pre-R2 plumbing，不动 R2 领域的写入代码**。

### 理由

1. **范围比 R1 更复杂**：R2 包含 6 个领域（vs R1 的 5 个），且 R2 领域的"写入者"语义比 R1 更隐式：
   - Artifact 不是"表的主键写入"，而是"文件系统 + ledger event"双写（`register_artifact` 同时改 `TaskRun.artifacts` list + 写 `artifact.registered` / `artifact.completed` ledger event）；
   - Policy 不是"数据写入"，而是"执行前评估函数"（`_evaluate_slice2_policy` 在每个 tool call 前被调用）；
   - Approval 不是"数据写入"，而是"交互式决策"（`ApprovalOutcome` enum + `audit` 表 approval 字段）；
   - Checkpoint 不是"独立表"，而是"全状态快照写入 conversations 表"。R1 Final Convergence（ADR-025）已删除 `CoreTransaction` 抽象；Checkpoint 后续应基于 Rust authoritative state + 明确 snapshot contract。

   直接切换任何一个领域都需要先明确"权威持有什么"——是文件 + ledger 写入权、还是评估函数权、还是交互决策权。

2. **灰度策略需要扩展**：R1 的 `DELTA_RUST_AUTHORITY` 灰度开关是"per-domain boolean"（R1.7 P0-1 后收紧为 per-domain list with fail-closed）。R2 的 Policy / Approval 不是"写"而是"评估 / 决策"，需要新的灰度维度（`DELTA_RUST_POLICY` / `DELTA_RUST_APPROVAL` 等）。`packages/storage_authority.py` 的 `ALL_DOMAINS` 需要扩展，CI guard 需要同步更新。

3. **Capability ABI 边界未完成**：R0 规划了 Capability ABI（process supervisor + capability registry + capability discovery），R1 只做了 Process Supervisor 最小集（`DeltaCoreClient` 统一进程入口）。R2 的 Policy / Approval 评估如果 Rust 化，Rust 端需要 capability 调用接口（"调用 Policy engine 评估这个 tool_call"）—— 这正是 Capability ABI 的设计目标，但 ABI 本身尚未落地。

4. **Checkpoint 跨领域耦合**：Checkpoint 是"全状态快照"，包含 R1 的 5 个领域 + R2 的 6 个领域状态。R1 切换时 Checkpoint 的 Rust 表示需要等所有领域都迁完才完整；R2 期间做 Checkpoint 切换应基于 Rust authoritative state + 明确 snapshot contract（ADR-025 删除 `CoreTransaction` 后，不再依赖 Python 假事务抽象）。

5. **Source/Citation 是只读领域**：`core/source_citation.py` 主要消费 ledger 事件 + 读 source 文件，没有"权威写入"概念。它的 R2 迁移实质是"Rust 端能够重放 citation 计算"——这是 R3 Execution Lifecycle 的影子读模式，不是 R2 的权威切换。

### 提议路径

R2 按以下顺序落地：

#### PR130（**本 ADR 仅文档**）

本 ADR 提交后，未来任何 R2 领域切换必须按 §6 单领域迁移流程写单独的 PR-level ADR（领域级 ADR-019/020/...）：

- 一个 PR 切换一个领域（或一个领域的子集，如 Approval 的 audit 写入 vs 交互决策）；
- 每个 PR 包含 §6 流程的全部 10 步证据；
- ADR 列出 §11 全部 8 字段（Domain / Authority Before / Authority After / Compatibility / Exit Condition / Failure / Rollback / Tests）。

#### PR131（**Pre-R2 plumbing**：authority selector 扩展 + Rust reader scaffolding）

在写任何 R2 领域 Rust 写入代码之前，先做基础设施：

1. **`packages/storage_authority.py` 扩展**：新增 `R2_DOMAINS = {"artifact", "validation", "checkpoint", "policy", "approval", "source_citation"}`；`DELTA_RUST_AUTHORITY` 解析器接受 R2 域名的 list（与 R1 域共用一个 env var）；`is_rust_authority()` 支持 R2 域；
2. **`scripts/check_rust_authority_migration.py` 更新**：把 R2 域加入扫描列表（仅对有显式 `is_rust_authority(domain)` 调用的 Python 模块生效）；
3. **`core/runtime-native/` 新增 reader scaffolding**：每个 R2 域一个 `*_reader.rs` lib（参照 R1 的 `IdempotencyReader` / `LedgerReader` 模式），用于 shadow-read Python 写入的事实；**不**实现写入路径；
4. **`core/runtime-native/src/bin/` 新增 inspect binary**：每个 R2 域一个 inspect CLI（`inspect_artifact` / `inspect_validation` / `inspect_checkpoint` / `inspect_source_citation`；Policy / Approval 是评估/决策，不产生 inspect binary）；
5. **CI 跨语言测试**：`tests/test_r2_shadow_read.py` —— Python 写 → Rust 读（Artifact sha256 / Validation 规则评估 / Checkpoint 快照 / Source/Citation 范围）；
6. **ADR-019 Pre-R2 Plumbing** 记录上述 6 件事的契约。

这一步**不切换任何 R2 域的权威**——只为后续切换做准备。灰度开关默认全部关闭。

#### PR132+（**实际权威切换**，每个领域一个 PR）

按 §6 流程，对 6 个领域分别切换。**实际顺序**（从风险最低、语义最明确开始）：

- **PR132 — Artifact Registry authority switch** ✅ **Completed (ADR-026)** （`core/artifact.py` → Rust `ArtifactRegistryWriter`）
  - 原因：Artifact 是最接近 R1 模式的领域（写 `artifact.registered` / `artifact.completed` ledger event + 写 `TaskRun.artifacts` list）；sha256 计算在 Rust 端更稳定；失败回退到 Python register 路径明确。
  - 风险：Artifact 是产物证据，错误会导致引用断裂。必须 cross-language 一致性测试（Python register → Rust verify sha256）。

- **PR133 — Source/Citation hard-cut** ✅ **Completed (ADR-027)** （`core/sources.py` + `core/citation.py` → Rust `SourceCitationWriter` / `SourceCitationReader`）
  - 原因：Python 只做文件 I/O / sha256 / stat / candidate range 构造；Rust 拥有 Source identity、revision、Citation identity、range validation、stale detection、所有持久化。
  - 风险：低。Python facade 保持 API 兼容；Rust 拥有所有 trusted facts。

- **PR134 — Validation authority** ✅ **Completed (ADR-028)**（`core/validation.py` → Rust `ValidationWriter` / `ValidationReader`）
  - 原因：Validation 是纯函数式评估（输入：task + output，输出：ValidationResult），不依赖外部状态；最容易做 fail-closed 测试。
  - 风险：中。Validation 失败会导致 run 终止（`validation.failed` ledger event），需保证 Python ↔ Rust 评估规则 100% 一致。

- **PR135 — Checkpoint authority**（`core/conversations.py` checkpoint 路径 + `core/recovery.py` 快照 → Rust `CheckpointWriter`）
  - 原因：Checkpoint 是全状态快照，包含 R1 + R2 全部领域状态。放在最后是因为它依赖前面 5 个域的 Rust 实现。
  - 风险：高。Checkpoint 错误会导致 resume 失败；需 end-to-end test（Python session → checkpoint → Rust resume）。

- **PR136 — Approval audit authority**（`core/audit.py` approval 字段写入 → Rust；交互决策保留 Python）
  - 原因：把 Approval 拆成"audit 写入"（可迁移）和"交互决策"（保留 Python，因为需要 LLM 上下文）。这是 §6 流程允许的"领域子集切换"。
  - 风险：中。audit 行是合规证据，必须 byte-equal 跨语言。

- **PR137 — Policy evaluation authority** ✅ **Completed (ADR-030)**（`core/gateway.py` Slice 2 → Rust `policy.evaluate`）
  - 原因：Policy 是 tool_call 前置评估，不修改任何表，但决定 tool_call 是否执行。Rust 化后可以避免"Python 端 policy 评估 + Rust 端实际执行"的语义鸿沟。
  - 风险：高。Policy bug 可能让危险 tool_call 漏过评估。必须 Python ↔ Rust 评估结果 100% 一致 + 独立 fuzz test。

每个 PR 必须满足 §7 双写规则 + §9 测试要求 + §10 CI guard + §11 PR 规则。

### 强制约束

- **不得跳过 PR131**：Pre-R2 plumbing 必须先做，否则任何"R2 域切换"都会变成"新 Python 写入者"，违反 ADR-009 §1 "Rust 是唯一控制面"。
- **不得双 PR 切换两个领域**：每个 PR 独立一个领域（或子集），独立一个 PR-level ADR。
- **不得在 R2 引入 Capability Worker 主体**：Capability Worker 是 R3 的范围。R2 只做 Process Supervisor 扩展（`DeltaCoreClient` 已就位，R2 域的 command 加入统一协议）。
- **不得引入 JSON-RPC / IPC 框架**：R1 的 NDJSON stdin/stdout 协议已就位，R2 域沿用同一通道（`delta_core` 进程内新增 command，不引入新协议层）。
- **不得重命名 Python 模块**：R2 期间 Python 端保持向后兼容；只有当某个领域完全切到 Rust 且 Python 路径被 CI 强制禁用后，才允许删除/重命名对应 Python 模块。
- **不得切换默认权威**：R2 灰度开关默认关闭，Python 仍是 production default authority。默认权威切换是 R5 的事，需基于用户验证数据单独裁定。
- **不得在 R2 期间扩大产品范围**：不引入新 provider / connector / UI / 智能能力；R2 仅迁移现有 Python 权威到 Rust。

### R2 域与 R1.7 Authority Matrix 的关系

R1.7 P0-1 收紧的 `RUST_WRITE_DOMAINS = {idempotency, ledger, task_identity}` 保持不变。R2 域在 `DELTA_RUST_AUTHORITY` 未设置时全部走 Python；显式启用 R2 域需要新 env var 前缀（待 PR131 设计，建议 `DELTA_RUST_ARTIFACT` / `DELTA_RUST_VALIDATION` / `DELTA_RUST_CHECKPOINT` / `DELTA_RUST_POLICY` / `DELTA_RUST_APPROVAL` / `DELTA_RUST_CITATION`，与 R1.7 的 fail-closed 解析器一致）。

## 不变量（R2 期间永久成立）

- **R1 行为测试基线不漂移**：R1 切换后的 5 个领域在 R2 期间保持 Rust/Python 行为一致；`pytest tests/` 继续全绿。
- **Python 读取者继续工作**：R2 期间所有 Python 读取代码继续从同一 SQLite DB 读取，行为不变。
- **Ledger 链不变**：R2 域触发的 ledger event（`artifact.registered` / `validation.failed` / `checkpoint.created` 等）从 Python → Rust 切换前后必须 100% 一致。
- **Policy 评估不变**：`core/gateway.py:_evaluate_slice2_policy` 从 Python → Rust 切换前后必须 100% 一致；独立 fuzz test 覆盖边界条件。
- **Approval 决策语义不变**：交互式 approval UI 行为不变；audit 行 byte-equal 跨语言。

## 结果

本 ADR 提议：

- **不立即**开始 R2 任何领域的权威切换；
- **不**在本次 PR 写任何 R2 域的 Rust 写入代码；
- **不**引入新的 IPC / 协议层；
- 后续 PR 必须按 §6 单领域流程，且每个领域一个 PR + 一个 PR-level ADR。

## 明确不做

- 不写 R2 域的 Rust 写入 path（仅 PR131 写 reader scaffolding）；
- 不引入 Capability Worker 主体；
- 不引入 JSON-RPC / IPC 新协议；
- 不重命名任何 Python 模块；
- 不切换默认权威；
- 不在 R2 期间扩大产品范围。

## 完成标准

R2 Trusted Execution 完成的判定（与 `rust-core-migration.md` §14 类似）：

- 普通任务主路径的 R2 6 个领域（Artifact / Validation / Checkpoint / Policy / Approval / Source-Citation）由 Rust 权威持有；
- Python 端对应写入 / 评估 / 决策路径被 CI 强制禁用；
- 跨语言一致性测试覆盖每个领域；
- 在 R2 完成后，R3 (Tool lifecycle / Retry / Backoff / Timeout / Cancellation) 启动。
