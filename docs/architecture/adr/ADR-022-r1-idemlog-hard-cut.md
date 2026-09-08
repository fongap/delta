# ADR-022 — R1 Idempotency Authority Hard-Cut

- 状态：Accepted
- 日期：2026-09-08
- 范围：R1 Idempotency 域权威硬切（Python → Rust，删除所有 fallback 路径）
- 决策类型：Hard-Cut 完成记录
- 关联：ADR-009 / ADR-010 / ADR-011 / ADR-013（Superseded）/ ADR-014（Superseded）/ ADR-017 / `docs/governance/rust-core-migration.md`

## 背景

ADR-013 定义了三阶段渐进路径（A→B→C），ADR-014 落地了阶段 B 的 delegate wrapper。R1.5 收口将 delegate 统一到 `DeltaCoreClient`。本 ADR 记录 **hard-cut**：删除所有 Python fallback 路径，Rust 成为 Idempotency 域的唯一事实来源、写入者和决策者。

## §11 8 字段

### Domain

`idempotency` —— `side-effects.db` 表 `side_effects` 的写入权威。

### Authority Before

Python `core/idemlog.py` `IdempotencyLog` 通过 `DeltaCoreClient` 委托给 Rust `delta_core` 进程。存在 `IdempotencyLogWithDelegate` wrapper 和 `DELTA_RUST_AUTHORITY` 选择开关。

### Authority After

Rust `delta_core` 是唯一事实来源。Python `core/idemlog.py` 是薄门面，所有状态转换、查询、stale sweep、operator resolution 均由 Rust 执行。不存在 Python SQLite writer、delegate wrapper、fallback 路径或选择开关。

### Writer

`delta_core` Rust 进程（通过 `DeltaCoreClient` stdin/stdout JSON line protocol）。

### Reader

Python `IdempotencyLog` 通过 `DeltaCoreClient` 发送查询命令到 `delta_core`，由 Rust 读取 SQLite 并返回结果。

### Production Path

```
SessionManager
  → core/idemlog.py (IdempotencyLog)
    → packages/delta_core_client.py (DeltaCoreClient)
      → delta_core (Rust subprocess)
        → SQLite (side-effects.db)
```

### Deleted Legacy Paths

| 路径 | 说明 |
|---|---|
| `core/idemlog_delegate.py` | `IdempotencyLogWithDelegate` wrapper 已删除 |
| `tests/test_idemlog_delegate.py` | delegate 测试已删除 |
| `is_rust_authority("idempotency")` 选择逻辑 | 从 `storage_authority.py` 移除 idempotency 域 |
| `DELTA_RUST_AUTHORITY=idempotency` 开关 | 不再有效，idempotency 硬切到 Rust |
| Python SQLite writer | `core/idemlog.py` 中无 `sqlite3` 导入或直接写入 |

### Database Impact

- SQLite schema 不变：`side_effects` 表结构与 R1 前完全一致
- 历史数据库完全兼容：旧 Python 写入的数据可被 Rust 正确读取
- `state` 列默认值 `'committed'` 确保 legacy 行被视为已提交
- 无数据迁移需求

### 删除的 Migration Path

- 三阶段渐进路径（ADR-013 阶段 A→B→C）已废弃
- delegate wrapper 模式（ADR-014）已废弃
- `DELTA_RUST_AUTHORITY` 选择开关对 idempotency 域不再有效
- 双写窗口不存在：不存在 Python 和 Rust 同时写入的阶段

### Rollback Method

仅通过 Git revert 回滚。不存在 runtime fallback —— 回滚意味着恢复到之前的 commit，重新引入 Python 写入路径。

### Tests

1. **Architecture guard**（`tests/test_idempotency_authority_path.py`）：
   - delegate 文件已删除
   - `idemlog.py` 无 Python writer 或 switch
   - 生产路径直接构造 Rust facade
   - 无生产文件导入已删除的 delegate

2. **Core contract**（`tests/test_idemlog.py`）：10 个测试覆盖 args_sha256、commit/lookup、recommit、ledger events、reopen

3. **Crash safety**（`tests/test_side_effect_crash_safety.py`）：17 个测试覆盖 6 个 crash point、acceptance criteria、operation_id stability、schema migration

4. **Production chain**（`tests/test_production_authority_chain.py`）：8 个测试覆盖完整生产调用链、reverse guard、portable smoke、concurrent writes

5. **Cross-language**（`tests/test_rust_idemlog_shadow_read.py` / `test_rust_idemlog_writer.py`）：11 个测试验证 Python↔Rust 双向一致性

6. **Migration DB**（`tests/test_migration_db.py`）：3 个测试验证历史数据库兼容

### Non-goals

- 不在本次 PR 中完成 Ledger 或 Task identity 的 hard-cut
- 不引入 Capability Worker 主体
- 不引入 JSON-RPC / IPC 框架
- 不重命名 Python 模块

### CI Guard

`tests/test_idempotency_authority_path.py` 中的 4 个架构守卫测试确保：
1. `core/idemlog_delegate.py` 文件不存在
2. `core/idemlog.py` 不包含 `sqlite3`、`is_rust_authority`、`DELTA_RUST_AUTHORITY`
3. 生产代码直接构造 Rust facade（不经过 delegate）
4. 无生产文件导入已删除的 delegate 模块

### Portable/Old-DB 验证

- 便携版 binary lookup 测试通过（中文+空格路径）
- 历史数据库 migration 测试通过（legacy 行默认为 committed）
- `delta_core` 正常退出、超时或协议异常时 fail-closed

## 不变量

- Rust 是 Idempotency 域的唯一事实来源
- 不存在 Python SQLite writer
- 不存在 runtime fallback
- 不存在 `DELTA_RUST_AUTHORITY` 选择逻辑对 idempotency 域的影响
- 历史数据库完全兼容
- 回滚方式仅为 Git revert

## 不做

- 不恢复 Python fallback writer
- 不重新创建 delegate wrapper
- 不降低、跳过或删除现有测试
- 不引入双写或双 Authority
