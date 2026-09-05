# ADR-010 — R1 State Foundation: Rust Shadow-Read Crate

- 状态：Accepted
- 日期：2026-09-05
- 范围：Delta Core Rust Runtime / State Foundation
- 决策类型：实现位置 + 迁移阶段
- 关联：ADR-009（Delta Core Architecture）、`docs/governance/rust-core-migration.md` §5 R1

## 背景

ADR-009 决定了 Delta 采用 Rust Control Plane + Capability Worker 架构。`docs/governance/rust-core-migration.md` §5 定义了 R0-R5 六个迁移阶段。

R0（Architecture Contract）的前置条件已在 PR #101 中完成：

- ADR-009 ✅
- Capability ABI ✅
- Authority Matrix ✅
- 行为测试基线 ✅（Python 测试套件）
- Error / Progress / Cancel / Artifact staging 协议规范 ✅

R0 剩余的缺口是 Process Supervisor basis（实际 Rust 代码）。本 ADR 落地 R1 的第一刀。

## 决策

### 1. 代码位置

Rust Core 代码放在 `core/runtime-native/`，不创建顶层 `crates/`。

理由：

- `repository-layout.md` 禁止顶层 `crates/`（CI layout-check 强制）；
- `core/` 的职责是 "Agent runtime、工作流、状态、记忆、权限等核心能力"——Rust Core 属于这一职责；
- 与现有 3 个 Rust workspace（`apps/desktop/src-tauri/`、`services/stt/`、`packaging/portable/launcher/`）模式一致：按职责放在已有目录的子目录。

### 2. Crate 名称

`delta-runtime-native`（`delta_runtime_native` lib name）。

### 3. 第一刀范围：Shadow-Read

R1 第一刀**只做读**，不做写。

- `LedgerReader`：打开 `run_events.db` read-only，验证 Python 写入的 hash chain；
- `IdempotencyReader`：打开 `side-effects.db` read-only，读取 side effect 状态机。

**不做**：

- 不写 SQLite；
- 不修改 Python 行为；
- 不创建第二套 DB 文件；
- 不替换 Python 的权威写入者；
- 不引入 JSON-RPC / Capability Worker IPC。

### 4. 验证契约

Rust shadow-read 必须能验证 Python 写入的数据：

- `LedgerReader::verify(run_id)` 重算 hash chain，与 Python `RunEventLedger.verify()` 结果一致；
- `IdempotencyReader::for_run(run_id)` 返回的 state / operation_id 与 Python `IdempotencyLog` 查询一致；
- canonical JSON 排序规则与 Python `_canonical()` 一致（sorted keys, compact separators）。

### 5. CI 集成

- `core/runtime-native/*` 路径变更触发 `rust=true`（ci.yml changes filter）；
- rust matrix 新增 `core/runtime-native` workspace；
- cargo-deny advisory check 覆盖该 workspace。

## 结果

- `core/runtime-native/Cargo.toml` + `src/lib.rs` + `src/ledger.rs` + `src/idemlog.rs` 落地；
- 10 个 Rust unit test 通过（canonical JSON / hash chain / state machine）；
- CI rust matrix 从 3 个 workspace 扩展到 4 个；
- `repository-layout.md` 明确 `core/runtime-native/` 的位置和职责。

## 未做

- **R1 后续 PR**：Python-Rust 一致性测试（Python 写入 → Rust 读出验证）；
- **R1 权威切换**：Python → Rust 写入者切换（需要 ADR + 灰度）；
- **R2-R5**：Artifact / Validation / Checkpoint / Policy / Approval / Tool lifecycle / Provider 等。

## 明确不做

本 ADR 不代表：

- Rust Core 开始写入；
- Python Runtime 退出；
- 引入 JSON-RPC / Capability Worker IPC；
- 迁移所有 Python 代码。

R1 的"State Foundation"目标是**先把 Rust 脚放进来，能读到 Python 写的事实，再谈写入**。
