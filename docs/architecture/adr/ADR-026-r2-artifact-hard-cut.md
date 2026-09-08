# ADR-026 — R2 Artifact Registry Hard-Cut

- 状态：Accepted
- 日期：2026-09-09
- 范围：R2 Trusted Execution 第一域 — Artifact Registry 的 Hard-Cut（从"可切换"彻底迁移为 Rust 唯一 Authority）
- 决策类型：迁移阶段（单领域 hard-cut，per ADR-018 提议顺序 + ADR-022/023/024 的 hard-cut 模式）
- 关联：ADR-005（Artifact domain，WS2）、ADR-009（Delta Core Architecture）、ADR-018（R2 计划与顺序）、ADR-019（R2 Pre-plumbing）、ADR-020（Artifact Authority Switch，Superseded by 本 ADR）、ADR-022/023/024（R1 hard-cut 先例）、`docs/governance/rust-core-migration.md` §5 R2 / §6 单领域迁移流程 / §7 双写规则

## 背景

ADR-020（PR132）把 `artifact` 提升为**可选** R1-style 写权威：设置 `DELTA_RUST_AUTHORITY=artifact` 时走 `core/artifact_delegate.py` 的 Rust 委托路径，否则回退到 `core/artifact.py` 的 Python ledger 写路径。这是"authority switch"（可切换），不是 hard-cut。

R1 阶段已证明 hard-cut 模式：Idempotency（ADR-022）、Ledger（ADR-023）、Task Identity（ADR-024）在切权后立即删除 delegate、fallback、feature flag 和 shadow production path，Python 只剩薄门面。本 ADR 把同一纪律应用到 R2 第一域 Artifact Registry。

## 决策

### Domain

`artifact` —— `run_events.db` 表中 `artifact.registered` + `artifact.completed` 事件的写入权威。

Artifact 不是一张新表；它是 ledger 的两个特殊事件类型。Rust 写路径复用已就位的 `LedgerWriter`（R1 ledger delegate 路径），不引入新 SQLite 表、不新增 `artifacts.db`。

### Authority Before

- `core/artifact.py:register_artifact()` / `register_run_artifacts()`：Python 端 stat + sha256 + `ledger.append("artifact.registered")` + `ledger.append("artifact.completed")`
- 可选 delegate：`core/artifact_delegate.py`（`register_artifact_delegated` / `register_run_artifacts_delegated` / `maybe_wrap` / `_is_delegate_active`）
- `DELTA_RUST_AUTHORITY=artifact` 作为 feature flag，`is_rust_authority("artifact")` 决定走哪条路径
- 未设 env var 时 Python `RunEventLedger.append` 写这两条事件

### Authority After

- Python 只做 discovery：文件扫描、`stat`、`sha256`、kind classifier、Artifact candidate 构造
- `core/artifact.py` 通过 `DeltaCoreClient` 发送 `artifact.register` 命令（`default_client()`）
- `delta_core` Rust 进程接收命令 → `ArtifactRegistryWriter` 构造 `ArtifactInput` → 复用 `LedgerWriter` 追加两条事件
- Python 不再写 artifact facts 或 artifact ledger events

### Python Responsibilities

- workspace traversal（`Path.rglob`）
- stat / mtime 过滤 / skip dirs
- SHA256（`_sha256_of`）
- kind classifier（`services.server.manager_support._artifact_kind`）
- 形成 `Artifact` candidate

### Rust Responsibilities

- artifact registration authority
- artifact fact persistence
- artifact ledger events（`artifact.registered` + `artifact.completed`）
- duplicate / idempotent registration behavior
- ledger hash chain append

### Deleted Legacy Paths

- `core/artifact_delegate.py`
- `register_artifact_delegated`
- `register_run_artifacts_delegated`
- `maybe_wrap`
- `_is_delegate_active`
- `artifact` runtime authority selector（`is_rust_authority("artifact")`）
- Python artifact ledger writer
- production shadow / fallback path

### Failure Behavior

Artifact registration fails closed：`delta_core` 不可用、协议不匹配、`artifact.register` 返回 error、或 malformed response 时，`register_artifact()` / `register_run_artifacts()` 抛 `DeltaCoreError`。禁止任何 Python fallback、静默回退、或环境回滚。

文件级错误（path missing、stat OSError、hash OSError）保持非致命：`None` 或 `incomplete=True`，与历史语义一致。

### Compatibility

`register_artifact(...)` / `register_run_artifacts(...)` 的公开签名不变（API 兼容）。`Artifact` dataclass / `RunEventLedger.events()` 读路径不变。`ledger=` 参数现在提供 Rust 写目标（`RunEventLedger.db_path`）；传 `None` 时只计算 candidate、不持久化（历史"no ledger"路径）。

### Duplicate Registration

`same run_id + path + hash` 的重复注册保持当前行为：append-only，再次追加一对事件（不 de-duplicate、不破坏 ledger chain、不重复 completion 之外的额外语义）。有测试覆盖。

### Rollback

仅 Git revert / release rollback。不存在环境变量回退。

### Tests

- `tests/test_artifact_authority_path.py`：架构守卫（delegate 已删、facade 无 Python writer / switch、生产不 import 已删 delegate、`artifact` 不在 `RUST_WRITE_DOMAINS`）+ 单文件注册 + missing file + hash failure → incomplete + workspace relative path + 批量扫描 + duplicate + fail-closed。
- `tests/test_storage_authority.py`：`artifact` hard-cut 语义（`is_rust_authority("artifact")` 抛 `InvalidAuthorityTargetError`、`_parse_domains` 抛 `UnknownDomainError`）。
- `core/runtime-native/src/artifact.rs` 内 `ArtifactRegistryWriter` 单元测试保持不变。

## 不变量

- **Rust 是 Artifact 唯一 Authority**：`artifact.registered` / `artifact.completed` 事件只能由 Rust `delta_core` 写入。
- **Python 只负责 discovery / metadata / hash**。
- **Ledger 链不变**：`artifact.register` 复用 R1 `LedgerWriter` hash chain 规则。
- **Payload 形状不变**：`artifact.registered` 是完整 `Artifact.to_dict()`；`artifact.completed` 是 trimmed `{path, sha256, size}`。
- **可移植性不回归**：中文/空格路径在 Python→Rust JSON 协议中无损。

## 明确不做

- 不通把文件扫描 / SHA256 / kind classifier 迁进 Rust（保留 Python 生态优势）。
- 不新增 Artifact database（`artifacts.db` / `artifact_registry.db`）。
- 不重做 artifact model / 文件 scanner / UI。
- 不处理 Source/Citation、Validation、Approval、Policy、Checkpoint 的 hard-cut。
- 不处理 R3-R5（Retry / Timeout / Cancellation / Scheduler / Provider Core 等）。

## 完成标准

- ✅ `core/artifact_delegate.py` 已删除
- ✅ `register_artifact_delegated` / `register_run_artifacts_delegated` / `maybe_wrap` / `_is_delegate_active` 已删除
- ✅ `artifact` 不再属于 `RUST_WRITE_DOMAINS`
- ✅ 生产 `is_rust_authority("artifact")` 命中为 0
- ✅ `DELTA_RUST_AUTHORITY` 不再控制 artifact
- ✅ Python 不直接 append `artifact.registered` / `artifact.completed`
- ✅ production Artifact registration 全部走 `DeltaCoreClient`
- ✅ 无 Python fallback / 双写 / shadow production path / migration-only delegate