# ADR-024 — R1 Task Identity Hard-Cut

- 状态：Accepted
- 日期：2026-09-08
- 范围：R1 Task Identity 域权威硬切（Python → Rust，删除所有 fallback 路径）
- 决策类型：Hard-Cut 完成记录
- 关联：ADR-001 / ADR-009 / ADR-010 / ADR-011 / ADR-012 / ADR-016（Superseded）/ ADR-017 / ADR-022 / ADR-023 / `docs/governance/rust-core-migration.md`

## 背景

ADR-016 定义了 Task Identity 域的阶段 B（opt-in delegate wrapper），ADR-017 记录了 R1 完成状态。ADR-022 完成了 Idempotency 域的 hard-cut，ADR-023 完成了 Ledger 域的 hard-cut。本 ADR 记录 **Task Identity 域的 hard-cut**：删除所有 Python fallback 路径，Rust 成为 Task Identity 域的唯一事实来源、写入者和决策者。

## §11 8 字段

### Domain

`task_identity` —— `automation.db` 表 `scheduled_tasks` + `task_runs` 的写入权威。`compute_next_run()` 保留在 Python（R4 域），仅 task/run identity persistence 迁移到 Rust。

### Authority Before

Python `core/automation/store.py` `TaskStore` 直接写 SQLite。存在 `TaskStoreWithDelegate` wrapper（`core/automation/store_delegate.py`）和 `DELTA_RUST_AUTHORITY` 选择开关。Rust 仅有 `TaskStoreReader` / `inspect_tasks` shadow-read 工具。

### Authority After

Rust `delta_core` 是唯一事实来源。Python `core/automation/store.py` 是薄门面（facade），所有 save、get、list、delete、due_tasks、add_run、find_run、task_for_run_session、runs 均由 Rust 执行。不存在 Python SQLite writer、delegate wrapper、fallback 路径或选择开关。

### Writer

`delta_core` Rust 进程（通过 `DeltaCoreClient` stdin/stdout JSON line protocol）。

### Reader

Python `TaskStore` 通过 `DeltaCoreClient` 发送查询命令到 `delta_core`，由 Rust 读取 SQLite 并返回结果。

### Production Path

```
SessionManager
  → core/automation/store.py (TaskStore)
    → packages/delta_core_client.py (DeltaCoreClient)
      → delta_core (Rust subprocess)
        → SQLite (automation.db)
```

### Deleted Legacy Paths

| 路径 | 说明 |
|---|---|
| `core/automation/store_delegate.py` | `TaskStoreWithDelegate` wrapper 已删除 |
| `tests/test_taskstore_delegate.py` | delegate 测试已删除 |
| `is_rust_authority("task_identity")` 选择逻辑 | 从 `storage_authority.py` 移除 task_identity 域 |
| `DELTA_RUST_AUTHORITY=task_identity` 开关 | 不再有效，task_identity 硬切到 Rust |
| `maybe_wrap_taskstore()` 调用 | 从 `manager.py` 移除 |
| Python SQLite writer | `core/automation/store.py` 中无 `sqlite3` 导入或直接写入 |

### Database Impact

- SQLite schema 不变：`scheduled_tasks` + `task_runs` 表结构与 R1 前完全一致
- 历史数据库完全兼容：旧 Python 写入的数据可被 Rust 正确读取
- `TaskStore::open` 自动初始化 schema（`CREATE TABLE IF NOT EXISTS`）
- 无数据迁移需求

### Rollback Method

仅通过 Git revert 回滚。不存在 runtime fallback —— 回滚意味着恢复到之前的 commit，重新引入 Python 写入路径。

### Tests

1. **Architecture guard**（`tests/test_taskstore_authority_path.py`）：
   - delegate 文件已删除
   - `store.py` 无 Python writer 或 switch
   - 生产路径直接构造 Rust facade
   - 无生产文件导入已删除的 delegate

2. **Production chain**（`tests/test_production_authority_chain.py`）：8 个测试覆盖完整生产调用链、reverse guard、portable smoke、concurrent writes

3. **Core contract**（`tests/test_taskstore.py`）：6 个测试验证 save、get、list、delete、due_tasks、add_run

4. **Storage authority**（`tests/test_storage_authority.py`）：46 个测试覆盖 domain 解析、R1/R2 分离、hard-cut 行为

### Non-goals

- 不在本次 PR 中完成 Artifact 的 hard-cut
- 不引入 Capability Worker 主体
- 不引入 JSON-RPC / IPC 框架
- 不重命名 Python 模块
- 不迁移 `compute_next_run()` 到 Rust（R4 域，保留在 Python）

### CI Guard

`tests/test_taskstore_authority_path.py` 和 `scripts/check_rust_authority_migration.py` 确保：
1. `core/automation/store_delegate.py` 文件不存在
2. `tests/test_taskstore_delegate.py` 文件不存在
3. `store.py` 无 Python writer、switch 或 `is_rust_authority` 调用
4. 生产代码直接构造 Rust facade（不经过 delegate）
5. 无生产文件导入已删除的 delegate 模块
6. `storage_authority.py` 中 task_identity 不在 `RUST_WRITE_DOMAINS` 中
7. `PROTOCOL_VERSION` 从 3 升级到 4

### Portable/Old-DB 验证

- 便携版 binary lookup 测试通过（中文+空格路径）
- 历史数据库兼容（schema 自动初始化）
- `delta_core` 正常退出、超时或协议异常时 fail-closed

## 不变量

- Rust 是 Task Identity 域的唯一事实来源
- 不存在 Python SQLite writer
- 不存在 runtime fallback
- 不存在 `DELTA_RUST_AUTHORITY` 选择逻辑对 task_identity 域的影响
- 历史数据库完全兼容
- 回滚方式仅为 Git revert

## 不做

- 不恢复 Python fallback writer
- 不重新创建 delegate wrapper
- 不降低、跳过或删除现有测试
- 不引入双写或双 Authority
