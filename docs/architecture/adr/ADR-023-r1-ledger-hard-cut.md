# ADR-023 — R1 Ledger Hard-Cut

- 状态：Accepted
- 日期：2026-09-08
- 范围：R1 Ledger 域权威硬切（Python → Rust，删除所有 fallback 路径）
- 决策类型：Hard-Cut 完成记录
- 关联：ADR-001 / ADR-009 / ADR-010 / ADR-011 / ADR-015（Superseded）/ ADR-017 / ADR-022 / `docs/governance/rust-core-migration.md`

## 背景

ADR-015 定义了 Ledger 域的阶段 B（opt-in delegate wrapper），ADR-017 记录了 R1 完成状态。ADR-022 完成了 Idempotency 域的 hard-cut。本 ADR 记录 **Ledger 域的 hard-cut**：删除所有 Python fallback 路径，Rust 成为 Ledger 域的唯一事实来源、写入者和决策者。

## §11 8 字段

### Domain

`ledger` —— `run-events.db` 表 `run_events` 的写入权威。`run_state` 通过 `RunEventLedger.derive_run_status()` 从 ledger 事件派生（ADR-017 P0-4）。

### Authority Before

Python `RunEventLedger` 通过 `DeltaCoreClient` 委托给 Rust `delta_core` 进程。存在 `RunEventLedgerWithDelegate` wrapper（`core/ledger_delegate.py`）和 `DELTA_RUST_AUTHORITY` 选择开关。

### Authority After

Rust `delta_core` 是唯一事实来源。Python `core/ledger.py` 是薄门面（facade），所有 append、events、runs、open_runs、run_status、verify、recover_stale 均由 Rust 执行。不存在 Python SQLite writer、delegate wrapper、fallback 路径或选择开关。

### Writer

`delta_core` Rust 进程（通过 `DeltaCoreClient` stdin/stdout JSON line protocol）。

### Reader

Python `RunEventLedger` 通过 `DeltaCoreClient` 发送查询命令到 `delta_core`，由 Rust 读取 SQLite 并返回结果。

### Production Path

```
SessionManager
  → core/ledger.py (RunEventLedger)
    → packages/delta_core_client.py (DeltaCoreClient)
      → delta_core (Rust subprocess)
        → SQLite (run-events.db)
```

### Deleted Legacy Paths

| 路径 | 说明 |
|---|---|
| `core/ledger_delegate.py` | `RunEventLedgerWithDelegate` wrapper 已删除 |
| `tests/test_ledger_delegate.py` | delegate 测试已删除 |
| `is_rust_authority("ledger")` 选择逻辑 | 从 `storage_authority.py` 移除 ledger 域 |
| `DELTA_RUST_AUTHORITY=ledger` 开关 | 不再有效，ledger 硬切到 Rust |
| `maybe_wrap_ledger()` 调用 | 从 `manager.py` 和 `analyzer.py` 移除 |
| Python SQLite writer | `core/ledger.py` 中无 `sqlite3` 导入或直接写入 |

### Database Impact

- SQLite schema 不变：`run_events` 表结构与 R1 前完全一致（含 `workspace` 列）
- 历史数据库完全兼容：旧 Python 写入的数据可被 Rust 正确读取
- `LedgerWriter::open` 自动执行 workspace index 迁移（`ALTER TABLE` + `CREATE INDEX IF NOT EXISTS`）
- 无数据迁移需求

### 删除的 Migration Path

- 三阶段渐进路径（ADR-015 阶段 B opt-in）已废弃
- delegate wrapper 模式（ADR-014 同构）已废弃
- `DELTA_RUST_AUTHORITY` 选择开关对 ledger 域不再有效
- 双写窗口不存在：不存在 Python 和 Rust 同时写入的阶段

### Rollback Method

仅通过 Git revert 回滚。不存在 runtime fallback —— 回滚意味着恢复到之前的 commit，重新引入 Python 写入路径。

### Bug Fix: canonical_json escape

Hard-cut 过程中发现 `canonical_json` 的 `escape_json_string` 是 no-op（直接返回原字符串）。当 payload 包含引号或特殊字符时（如 `result_preview` 中的嵌套 JSON），canonical 输出为无效 JSON。verify 阶段 `serde_json::from_str` 失败后 fallback 到 `Value::Null`，导致 hash chain 不匹配。修复了 `ledger.rs` 和 `idemlog.rs` 中的 `escape_json_string`，实现完整的 JSON 字符串转义。

### Tests

1. **Architecture guard**（`tests/test_ledger_authority_path.py`）：
   - delegate 文件已删除
   - `ledger.py` 无 Python writer 或 switch
   - 生产路径直接构造 Rust facade
   - 无生产文件导入已删除的 delegate

2. **Production chain**（`tests/test_production_authority_chain.py`）：8 个测试覆盖完整生产调用链、reverse guard、portable smoke、concurrent writes

3. **Cross-language**（`tests/test_rust_ledger_writer.py` / `test_rust_shadow_read.py`）：12 个测试验证 Python↔Rust 双向一致性

4. **Core contract**（`tests/test_run_ledger.py`）：10 个测试覆盖 append、events、verify、tamper detection、hash chain

5. **Workspace column**（`tests/test_run_workspace_column.py`）：13 个测试验证 workspace index 迁移

### Non-goals

- 不在本次 PR 中完成 Task Identity 或 Artifact 的 hard-cut
- 不引入 Capability Worker 主体
- 不引入 JSON-RPC / IPC 框架
- 不重命名 Python 模块

### CI Guard

`tests/test_idempotency_authority_path.py` 和 `scripts/check_rust_authority_migration.py` 确保：
1. `core/ledger_delegate.py` 文件不存在
2. `core/ledger.py` 不包含 `sqlite3`、`is_rust_authority`、`DELTA_RUST_AUTHORITY`
3. 生产代码直接构造 Rust facade（不经过 delegate）
4. 无生产文件导入已删除的 delegate 模块
5. `storage_authority.py` 中 ledger 不在 `RUST_WRITE_DOMAINS` 中

### Portable/Old-DB 验证

- 便携版 binary lookup 测试通过（中文+空格路径）
- 历史数据库兼容（workspace index 自动迁移）
- `delta_core` 正常退出、超时或协议异常时 fail-closed

## 不变量

- Rust 是 Ledger 域的唯一事实来源
- 不存在 Python SQLite writer
- 不存在 runtime fallback
- 不存在 `DELTA_RUST_AUTHORITY` 选择逻辑对 ledger 域的影响
- 历史数据库完全兼容
- 回滚方式仅为 Git revert

## 不做

- 不恢复 Python fallback writer
- 不重新创建 delegate wrapper
- 不降低、跳过或删除现有测试
- 不引入双写或双 Authority
