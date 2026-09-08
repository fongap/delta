# ADR-020 — R2 Artifact Registry Authority Switch

> **Superseded（2026-09-09）**：本 ADR 描述的"可选 authority switch"（`DELTA_RUST_AUTHORITY=artifact` + `core/artifact_delegate.py` + `maybe_wrap`）已被 ADR-026 Hard-Cut 取代。`core/artifact_delegate.py` 已物理删除，`tests/test_artifact_delegate.py` 已删除，`artifact` 已从 `RUST_WRITE_DOMAINS` 移除。Artifact 域现在由 Rust `delta_core` 作为唯一事实来源，不存在 authority switch、Python fallback、delegate wrapper 或 feature flag。详见 ADR-026。

- 状态：Superseded by ADR-026
- 日期：2026-09-07
- 范围：R2 Trusted Execution 第一域 — Artifact Registry 的 Python→Rust 权威切换
- 决策类型：迁移阶段（单领域权威切换，per ADR-018 提议顺序）
- 关联：ADR-005（Artifact domain，WS2）、ADR-009（Delta Core Architecture）、ADR-012（R1 Pre-R1 plumbing 模式）、ADR-014（R1 Idempotency delegate 模式）、ADR-019（R2 Pre-plumbing：R2 reader scaffolding + storage_authority 扩展）、ADR-018（R2 计划与顺序）、`docs/governance/rust-core-migration.md` §5 R2 / §6 单领域迁移流程 / §7 双写规则 / §11 PR 规则

## 背景

ADR-018 把 R2 Artifact Registry 定为 R2 6 域迁移的第一域（"最接近 R1 模式"）。R1 已建立的模式（ADR-014 / ADR-015 / ADR-016）是：

1. Pre-R1 plumbing 提供 `DELTA_RUST_AUTHORITY` per-domain 灰度开关 + CI guard
2. 每个域一个 PR：Python 端写一个 `*_delegate.py` 包装 + Rust 端写一个 `XxxWriter`
3. Rust 写入走 `delta_core` 统一进程入口（NDJSON stdin/stdout）
4. Fail-closed：声明 Rust 域但 binary 不可用 → `DeltaCoreError`，不静默回 Python
5. CI guard 扩展：把新域加入 `scripts/check_rust_authority_migration.py` 扫描

R1.7 P0-1 收紧的 `RUST_WRITE_DOMAINS` 起初不含 `artifact`（artifact 在 ADR-019 的 `RUST_READ_DOMAINS` 中）。本 ADR 把 `artifact` **提升**为 R1-style 写权威，同时保留 reader（PR131）作为 cross-check 工具。

## 决策

### Domain

`artifact` —— `run_events.db` 表中 `artifact.registered` + `artifact.completed` 事件的写入权威。

注意：artifact 不是一张新表；它是 ledger 的两个特殊事件类型。Rust 写路径复用 R1 已就位的 `LedgerWriter`（R1 ledger delegate 路径），不引入新 SQLite 表。

### Authority Before

- `core/artifact.py:register_artifact()` / `register_run_artifacts()`：Python 端 stat + sha256 + `ledger.append("artifact.registered")` + `ledger.append("artifact.completed")`
- Python `RunEventLedger` 写这两条事件

### Authority After

- Python 端 stat + sha256（不变 — Rust 不是 file walker；与 R1.5 R0-1 决策一致）
- `core/artifact_delegate.py:register_artifact_delegated()` 通过 `DeltaCoreClient` 发送 `artifact.register` 命令
- `delta_core` Rust 进程接收命令 → `ArtifactRegistryWriter` 构造 `ArtifactInput` → 复用 `LedgerWriter` 追加两条事件
- Python 读路径（`register_artifact` 返回的 `Artifact` 对象、`RunEventLedger.events()`）完全不变

### Compatibility（PR132 阶段）

- **默认行为完全等同 R1.7**：未设置 `DELTA_RUST_AUTHORITY=artifact` 时，`maybe_wrap()` 返回的 wrapper 调用 `core.artifact.register_artifact`（Python 路径）。所有现有测试继续全绿。
- **声明了但 binary 不可用** → `DeltaCoreError`（fail-closed，R1.5 P0-3 模式）
- **声明了且 binary 可用** → 走 Rust 路径，Python stat + sha256 + Rust 写两条事件
- **Python 读路径无变化**：`Artifact` dataclass / `RunEventLedger.events()` / `register_artifact` 的返回类型一致
- **Ledger 链不变**：Rust 端 `LedgerWriter` 复用 R1 hash chain 规则（`sha256(prev_hash|seq|type|actor|ts|payload)`），与 Python 端 100% 一致

### Exit Condition

- 4 周观察期内，hash chain + payload shape + 事件顺序与 Python 路径完全一致
- `tests/test_artifact_delegate.py::test_rust_and_python_paths_produce_identical_ledger` 持续通过

### Failure / Rollback

- 失败：unset `DELTA_RUST_AUTHORITY` → `maybe_wrap()` 立即返回 Python 路径（无重启）
- Rollback 测试：声明 `DELTA_RUST_AUTHORITY=artifact` 但 binary 不可用 → `DeltaCoreError` 抛出，**不**静默回 Python

### Tests

R1 模式复用，3 层：

1. **Rust 单元测试**（`core/runtime-native/src/artifact.rs` 内 `#[cfg(test)] mod tests`）：4 个新测试覆盖 `ArtifactRegistryWriter` 的 payload shape + 事件顺序 + incomplete skip + actor/workspace
2. **Python 单元测试**（`tests/test_storage_authority.py`）：5 个新测试覆盖 `artifact` 从 R2 reader 提升到 R1 write 后的 selector 语义
3. **Python 跨语言测试**（`tests/test_artifact_delegate.py`，7 个测试）：Python 调用 → Rust `delta_core` 写入 → Python reader 读取 → shape 一致性

### 强制约束

- 不得在 PR132 引入 Capability Worker 主体
- 不得引入 JSON-RPC / IPC 框架（沿用 R1 NDJSON stdin/stdout）
- 不得重命名 Python 模块
- 不得切换默认权威（`DELTA_RUST_AUTHORITY=artifact` 默认未设置；Python 仍是 production default）
- 不得修改 `core/artifact.py:register_artifact` 的对外签名（保持后向兼容）

## 不变量

- **行为测试基线不漂移**：`pytest tests/` 全绿（1743 + 新增 ≈ 1750 通过）
- **Python 读取者继续工作**：`RunEventLedger.events(run_id)` 返回的事件序列与 R1.7 完全一致
- **Ledger 链不变**：hash chain 100% 一致（Rust 复用 R1 `LedgerWriter`）
- **Payload 形状不变**：`artifact.registered` 是完整 `Artifact.to_dict()`；`artifact.completed` 是 trimmed `{path, sha256, size}`（与 Python 完全一致）

## 结果

本 PR：

- `packages/storage_authority.py`：把 `artifact` 从 `RUST_READ_DOMAINS` 提升到 `RUST_WRITE_DOMAINS`；保留 reader cross-check 工具
- `core/runtime-native/src/artifact.rs`：新增 `ArtifactRegistryWriter`（复用 `LedgerWriter`）+ 4 个 Rust 单元测试
- `core/runtime-native/src/bin/delta_core.rs`：新增 `artifact.register` 命令（PR132 是首个在 R1 NDJSON 协议上增加新命令的 PR — handshake 协议版本号 `1` 不变，因为是 additive）
- `core/artifact_delegate.py`（新）：`register_artifact_delegated` / `register_run_artifacts_delegated` / `maybe_wrap`，与 R1 delegate 模式一致
- `tests/test_artifact_delegate.py`（新）：7 个跨语言测试
- `tests/test_storage_authority.py`：5 个新测试覆盖提升语义
- `scripts/check_rust_authority_migration.py`：扩展 CI guard（详见 ADR-020 实施说明）

## 明确不做

- 不修改 Python 端 `core/artifact.py` 任何公开函数签名
- 不在 R2 域间共享状态（artifact 是单域独立 PR）
- 不切换默认权威
- 不在 PR132 引入 Approval / Validation / Checkpoint / Source-Citation / Policy 的任何写路径
- 不修改 `delta_core` 协议版本号（PR132 是 additive）

## 完成标准

PR132 完成判定：

- ✅ `artifact` 在 `RUST_WRITE_DOMAINS` 中
- ✅ Rust `ArtifactRegistryWriter` 实现 + 4 个单元测试
- ✅ `delta_core` 接收 `artifact.register` 命令
- ✅ `core/artifact_delegate.py` 提供 `maybe_wrap()` + `register_artifact_delegated()` + `register_run_artifacts_delegated()`
- ✅ 7 个跨语言测试通过
- ✅ 5 个新 selector 测试通过
- ✅ CI guard 扩展到 `artifact` 域
- ✅ R1 行为测试基线不漂移
