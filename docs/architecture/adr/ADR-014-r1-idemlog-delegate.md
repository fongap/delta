# ADR-014 — R1 Idempotency Authority Switch: 阶段 B（实际委托）

- 状态：Superseded by ADR-022
- 日期：2026-09-05
- 范围：R1 Idempotency 域权威切换（Python → Rust）阶段 B
- 关联：ADR-009 / ADR-010 / ADR-011 / ADR-012 / ADR-013 / ADR-022 / `docs/governance/rust-core-migration.md` §5 / §6 / §7 / §11

> **Superseded（2026-09-08）**：本 ADR 描述的 `IdempotencyLogWithDelegate` wrapper 和 `maybe_wrap` 模式已被 ADR-022 hard-cut 取代。`core/idemlog_delegate.py` 已物理删除，`tests/test_idemlog_delegate.py` 已删除。Idempotency 域现在由 Rust `delta_core` 作为唯一事实来源，不存在 Python fallback、delegate wrapper 或选择开关。详见 ADR-022。

> **R1.5 更新（2026-09-07）**：本 ADR 描述的"委托 `write_idemlog` per-op subprocess"实现已被 P0-1 取代。R1.5 实际生产路径：delegate 改走统一 `delta_core` 进程（`DeltaCoreClient` → `delta_core` → SQLite），`write_idemlog` 仅保留为诊断工具。详见 ADR-017 §R1.5 生产切权收口。

## 背景

PR #108 (ADR-013 阶段 A) 落地了 Rust `IdempotencyWriter` + `write_idemlog` binary + 5 跨语言测试。**没有任何 Python 写路径被改动**。

本 ADR 描述阶段 B：让 Python `IdempotencyLog` 在 env var 启用时**实际**委托写入给 Rust。

## §11 8 字段

### Domain

`idempotency` —— `side-effects.db` 表 `side_effects` 的写入权威。

### Authority Before

Python `IdempotencyLog` 直接写 SQLite。Rust 仅有 shadow-read / inspect / writer 工具。

### Authority After

**默认行为不变**：所有现存调用方继续使用 `IdempotencyLog` 类，行为完全等同 PR #108 之前。

**显式 opt-in 行为**：

- 当 `DELTA_RUST_AUTHORITY=1` **且** `core/runtime-native/target/debug/write_idemlog(.exe)` binary 存在时，调用方使用 `core.idemlog_delegate.IdempotencyLogWithDelegate`（或 `maybe_wrap`）会委托所有写方法到 Rust subprocess。
- 读方法保持 Python 端。
- 关闭 env var 或删除 binary → 自动回退到 Python-only 行为（fall-through）。

### Compatibility

- 现有所有调用方继续工作（`IdempotencyLog` 不变）。
- 新增 `IdempotencyLogWithDelegate` 是 opt-in wrapper。
- **不引入双写**：写要么走 Python，要么走 Rust，不同时写两个。
- 读路径完全不动（Python `IdempotencyLog` 直接读同一 SQLite 文件）。

### Exit Condition

- 测试环境：4 个新 delegate test 100% pass + 全部 1488 pytest test pass。
- 部署后：4 周观察期内 Python ↔ Rust 行为 byte-equal（hash chain / operation_id / state machine）。
- 关闭条件：CI guard 升级到"如果 env var = 1 但 Python 仍直接写 SQLite 则 hard fail"（未来 PR）。

### Failure / Rollback

- **Phase B 实施未失败**：因为默认行为完全等同 main。如果用户不设 env var，所有 `IdempotencyLog` 调用照旧。
- **delegate 路径失败**（Rust binary 缺失 / subprocess 失败）：抛 `RuntimeError`——这是设计上的"fail-loud"，因为静默回退到 Python 写会违反 env var 的契约。
- **回滚 PR #109**：`git revert` 即可，零生产影响。
- **生产环境回滚**：`unset DELTA_RUST_AUTHORITY`，所有现存调用方自动回退。

### Tests

- 6 个新 test 在 `tests/test_idemlog_delegate.py`：
  1. `test_maybe_wrap_returns_plain_log_when_authority_off`
  2. `test_maybe_wrap_returns_delegate_when_authority_on`
  3. `test_delegate_writes_via_rust_and_python_reads_back`
  4. `test_delegate_writes_uncertain`
  5. `test_delegate_writes_failed`
  6. `test_plain_log_path_unchanged_when_authority_off`
- 完整测试套件：1482 + 6 = 1488 passed。
- Rust unit test：16 passed。

## 决策

PR #109（本 ADR 对应）落地：

1. `core/idemlog_delegate.py`：`IdempotencyLogWithDelegate` + `maybe_wrap`。
2. `tests/test_idemlog_delegate.py`：6 跨语言测试。
3. **不**改 `core/idemlog.py` 任何方法。
4. **不**改任何现存调用方。
5. **不**改 `is_rust_authority` 行为（仍读 env var）。
6. **不**升级 CI guard。

## 不变量

- `IdempotencyLog(db)` 的行为**完全等同** PR #109 之前；
- `IdempotencyLog` 不是 thread-unsafe 也不是 backward-incompatible（同一接口）；
- `IdempotencyLogWithDelegate` 读方法直接调 inner，与原 `IdempotencyLog` 字节级一致；
- `IdempotencyLogWithDelegate` 写方法在 env var = 1 时**短路** Python 写入（不双写）。

## 不做

- 不在 `IdempotencyLog` 类内部添加任何 Rust 委托逻辑；
- 不修改 `services/server/`、`core/engine.py` 等任何调用方；
- 不升级 CI guard；
- 不引入双写。

## 与 PR #108 (阶段 A) 关系

PR #108 给了 Rust 写工具。PR #109（本 ADR）给了 Python-side opt-in wrapper。下一步 PR #110+ 是**真正切换**：把所有调用方迁移到 `maybe_wrap` 或 `IdempotencyLogWithDelegate`。每个迁移一个 PR，便于回滚。
