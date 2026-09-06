# ADR-016 — R1 Task Identity Authority Switch: 阶段 B（实际委托）

- 状态：Accepted
- 日期：2026-09-06
- 范围：R1 Task identity 域权威切换（Python → Rust）阶段 B
- 关联：ADR-009 / ADR-010 / ADR-011 / ADR-012 / ADR-014 / ADR-015 / `docs/governance/rust-core-migration.md` §5 / §6 / §7 / §11

## 背景

PR #107 (ADR-012) 落地了 `inspect_tasks` binary + `taskstore.rs` shadow-read + `TaskStoreReader`。证明 Rust 端能够读取 Python 端写入的 `scheduled_tasks` / `task_runs` 表。**没有任何 Python 写路径被改动**。

本 ADR 描述 Task identity 域的阶段 B：让 Python `TaskStore` 在 env var 启用时**实际**委托 `save` / `delete` / `add_run` 给 Rust `TaskStoreWriter` subprocess。

## §11 8 字段

### Domain

`task_identity` —— `tasks.db` 表 `scheduled_tasks` + `task_runs` 的写入权威。

### Authority Before

Python `core/automation/store.py` `TaskStore` 直接写 SQLite。Rust 仅有 `TaskStoreReader` / `inspect_tasks` shadow-read 工具。

### Authority After

**默认行为不变**：所有现存调用方继续使用 `TaskStore` 类，行为完全等同本 PR 之前。

**显式 opt-in 行为**：

- 当 `DELTA_RUST_AUTHORITY=1` **且** `core/runtime-native/target/debug/write_tasks(.exe)` binary 存在时，调用方使用 `core.automation.store_delegate.TaskStoreWithDelegate`（或 `maybe_wrap_taskstore`）会委托所有写方法到 Rust subprocess。
- 读方法保持 Python 端。
- 关闭 env var 或删除 binary → 自动回退到 Python-only 行为。

### Compatibility

- 现有所有调用方继续工作（`TaskStore` 不变）。
- 新增 `TaskStoreWithDelegate` 是 opt-in wrapper。
- **不引入双写**：写要么走 Python，要么走 Rust。
- 读路径完全不动。
- Task data JSON blob 格式不变（Rust 存储原始 JSON 字符串，不解释结构）。

### Exit Condition

- 测试环境：5 cross-lang + 6 delegate test 100% pass + 全部 pytest test pass。
- 部署后：4 周观察期。
- 关闭条件：CI guard enforcement 已覆盖 `TaskStore(`。

### Failure / Rollback

- **delegate 路径失败**：抛 `RuntimeError`，fail-loud。
- **回滚本 PR**：`git revert` 即可。
- **生产环境回滚**：`unset DELTA_RUST_AUTHORITY`。

### Tests

- 5 cross-lang test (`test_rust_taskstore_writer.py`)
- 6 delegate test (`test_taskstore_delegate.py`)
- Rust unit test：24 passed (20 + 4 new taskstore writer tests)

## 决策

1. `core/runtime-native/src/taskstore.rs`：新增 `TaskStoreWriter` + `init_schema` + 4 unit test。
2. `core/runtime-native/src/bin/write_tasks.rs`：CLI binary。
3. `core/runtime-native/Cargo.toml`：`[[bin]]` 条目。
4. `core/runtime-native/src/lib.rs`：re-export `TaskStoreWriter`。
5. `core/automation/store_delegate.py`：`TaskStoreWithDelegate` + `maybe_wrap_taskstore`。
6. `services/server/manager.py`：`self.task_store` 通过 `maybe_wrap_taskstore` 包装。
7. `scripts/check_rust_authority_migration.py`：enforcement 扩展到 `TaskStore(`，`DELEGATE_FILES` 增加 `store_delegate.py`。
8. `core/analyzer.py`：错误消息中的 `TaskStore(` 改为 `<TaskStore instance>` 以避免 regex 误报。
9. ADR-016。

## 不变量

- `TaskStore(path)` 的行为**完全等同**本 PR 之前；
- `TaskStore` 的 JSON blob 格式不变；
- `TaskStoreWithDelegate` 读方法直接调 inner；
- `TaskStoreWithDelegate` 写方法在 env var = 1 时短路 Python 写入。

## 不做

- 不在 `TaskStore` 类内部添加 Rust 委托逻辑；
- 不实现 `run_state` 域或 `storage_transaction` 域的 authority switch；
- 不引入双写。
