# ADR-012 — R1 Pre-Plumbing: 灰度开关与 CI Guard

- 状态：Accepted
- 日期：2026-09-05
- 范围：R1 authority switch 前的灰度基础设施
- 关联：ADR-009 / ADR-010 / ADR-011 / `docs/governance/rust-core-migration.md` §6 / §7

## 背景

PR #103-#106 完成了 R1 shadow-read 与 switch 计划。R1 authority switch 本身（PR12-16）按 `rust-core-migration.md` §6 单领域流程逐步推进，但**直接跳到 authority switch** 会让：

1. Python 与 Rust 写入者**没有显式开关**——无法灰度；
2. CI 无法检测"声称已切到 Rust 但 Python 仍在写"的状态；
3. 未来 R1 switch PR 每次都要在核心模块添加 import + 改逻辑——开 PR 时 diff 巨大。

本 ADR 落地 Pre-R1 plumbing：

- env-var 灰度开关
- 3 个 R1 候选模块 forward-declare 导入
- CI guard 强制 import 存在
- 第三个领域（Task identity）的 inspect binary

## 决策

### 1. `DELTA_RUST_AUTHORITY` 环境变量

- 默认未设：Python 是唯一权威；
- 设置为 `1` / `true` / `yes` / `on`：声明 Rust 是权威；
- 仅 5 个域：`idempotency` / `ledger` / `run_state` / `task_identity` / `storage_transaction`；
- **当前 PR11 不实际改变任何 Python 写入行为**——env var 只为 PR12+ 预留。

### 2. `packages/storage_authority.py`

单一职责：暴露 `is_rust_authority(domain: str) -> bool`。今天总是返回 `False`（除了 env var 真的设了且 domain 在白名单里）。

**不**做：

- 不引入 `StorageBackend` 协议；
- 不改写 `sqlite3.connect` 调用点；
- 不修改任何读 / 写路径；
- 不做进程级状态管理。

### 3. Forward-declare imports（强制）

3 个 R1 候选模块添加 `from packages.storage_authority import is_rust_authority  # noqa: F401`：

- `core/idemlog.py`（idempotency 域）
- `core/ledger.py`（ledger + run_state 域）
- `core/automation/store.py`（task_identity 域）

仅 import，不调用。这是"显式声明这个模块未来可能用上 helper"。CI guard 验证 import 存在。

### 4. CI guard：`scripts/check_rust_authority_migration.py`

- 扫描 `core/` 下所有 `*.py`；
- 找 `sqlite3.connect(` 调用点；
- 维护 `DOMAIN_TO_FILES` 显式表（不是文件名启发式）映射 4 个 R1 域到 owner file；
- 任何 owner file 缺少 `from packages.storage_authority import is_rust_authority` → 报错；
- 退出码 0 = pass / 1 = violation / 2 = missing prerequisites；
- 已加入 `ci.yml` 的 `layout-check` job（在 `python3 scripts/check_legacy_paths.py` 之后）。

### 5. 第三个领域 inspect binary：`inspect_tasks`

`core/runtime-native/src/bin/inspect_tasks.rs` + `core/runtime-native/src/taskstore.rs`：

- `TaskStoreReader` lib 暴露 `tasks()` / `runs(task_id)` / `find_run(run_id)`；
- 跨语言测试 `tests/test_rust_taskstore_shadow_read.py` 5 个测试：列表、按 task_id 列 run、按 run_id 查 run、空 task_id 返空、空 run_id 返空。

至此 3 个领域（Idempotency / Ledger / Task identity）都有 inspect binary 与跨语言测试。

## 不变量

- **Python 行为 100% 不变**：本 PR 不修改任何读 / 写路径；只加 forward import + env-var 读取 helper。
- **env var 默认值 = Python 权威**：未设 `DELTA_RUST_AUTHORITY` 时，所有 `is_rust_authority()` 返回 `False`，行为完全等同 main 现状。
- **CI guard 是 hard failure**：未来任何 commit 让 R1 候选模块移除 import，CI 失败。

## 结果

- `packages/storage_authority.py` 落地（46 行）
- `scripts/check_rust_authority_migration.py` 落地（87 行）
- 3 个 forward-declare imports 添加（3 行）
- `core/runtime-native` 新增 `taskstore.rs` lib（125 行）+ `inspect_tasks.rs` binary（105 行）
- 跨语言测试 5 个新增
- CI guard wire 到 `ci.yml` 的 `layout-check` job
- 完整测试套件：1477 passed（+ 5 新）/ 6 skipped，无回归
- Rust unit test：11 passed（+ 1 新）

## 明确不做

- 不实际切换任何权威；
- 不引入 `StorageBackend` 抽象层（PR12+ 才做）；
- 不改写 `sqlite3.connect` 调用点；
- 不引入 Process Supervisor / Capability Worker / JSON-RPC；
- 不删除任何 Python 模块。

## 后续路径

PR12+（每个领域一个 PR + PR-level ADR）按 ADR-011 §提议路径 推进：

- PR12 — Idempotency authority switch
- PR13 — Ledger authority switch
- PR14 — Run state authority switch
- PR15 — Task/Run identity authority switch
- PR16 — Storage transaction boundary

每个 PR 必须满足 `rust-core-migration.md` §6 + §11 全部要求。
