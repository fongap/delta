# ADR-013 — R1 Idempotency Authority Switch: 第一刀设计

- 状态：Superseded by ADR-017
- 日期：2026-09-05
- 范围：R1 Idempotency 域权威切换（Python → Rust）
- 决策类型：迁移阶段设计（已被 ADR-017 的完成记录取代）
- 关联：ADR-009 / ADR-010 / ADR-011 / ADR-012 / ADR-017（R1 State Foundation 完成）/ `docs/governance/rust-core-migration.md` §5 / §6 / §7 / §11

> **Superseded 说明**：本 ADR 描述的三阶段渐进路径（A→B→C）已被 ADR-017 的实际实现取代，最终由 ADR-022 hard-cut 完成。ADR-022 删除了 delegate wrapper、fallback 路径和选择开关，Rust 成为 Idempotency 域的唯一事实来源。本文件保留作为历史设计参考。

## 背景

PR #103-#107 完成了 R0（R0.5）。本 ADR 描述 **PR12 — R1 Idempotency authority switch** 的设计，按 `rust-core-migration.md` §6 单领域流程推进。

§11 要求的 8 字段（Domain / Authority Before / Authority After / Compatibility / Exit Condition / Failure / Rollback / Tests）：

### Domain

`idempotency` —— `side-effects.db` 表 `side_effects` 的写入权威。

### Authority Before

Python `core/idemlog.py` `IdempotencyLog` 是唯一写入者。Rust `IdempotencyReader` 仅做 shadow-read。

### Authority After（**当前 PR12 提议的渐进路径**）

**阶段 A（本 PR）**：Rust `IdempotencyWriter` + 跨语言测试 + Python 调用路径（subprocess 委托）落地，但 `is_rust_authority("idempotency")` **永远返回 False**。Python `IdempotencyLog` 保持原行为。

**阶段 B（未来 PR，未在本次范围）**：当 PR12 阶段 A 在测试中验证 100% 一致后，**才**将 Python 端 `is_rust_authority` 返回 True 的实际开关 wire 到写入路径：
- Python `IdempotencyLog.record_planned` → 检查 `is_rust_authority("idempotency")` → True 时委托给 `write_idemlog` binary → False 时保留原 Python 写入。
- 双写窗口（§7）短期存在：Python 保留 read 路径，Rust 写 + Python shadow-read 验证。

**阶段 C**：CI guard 升级为"如果 env var 设为 1 但 Python 仍写则 hard fail"。

### Compatibility

- 阶段 A 不改 Python 行为；Python 端 `IdempotencyLog` 继续原样写。
- 阶段 B 短期双写（Python 写 + Rust 写同一行），预期 1-2 个 PR 内关闭 Python 写入。
- Python 读路径完全不动：`IdempotencyReader` 继续从同一 SQLite DB 读。
- Ledger chain、operation_id、args_sha256 全部保持 byte-equal（PR #104/105 已验证）。

### Exit Condition

- 阶段 A 退出条件：Rust `IdempotencyWriter` 全 unit test 通过 + 跨语言一致性测试通过。
- 阶段 B 退出条件：实际部署 `DELTA_RUST_AUTHORITY=1` + 4 周观察期内 hash chain + operation_id + state machine 全部一致。
- 阶段 C 退出条件：CI guard 升级后 main 上 4 周无 Python direct write 警告。

### Failure / Rollback

- 阶段 A 失败：revert PR12。Python 行为未变，零影响。
- 阶段 B 失败：删除 `DELTA_RUST_AUTHORITY=1` env var，Python 写路径自动 fallback（双写期间 Python 路径是 primary）。
- 阶段 C 失败：guard 退回到 "warn only"。

### Tests

阶段 A（本 PR）：

1. Rust unit test：6 个新增（writer_full_state_machine / writer_mark_failed / writer_mark_uncertain / operation_id_matches_python_format / args_sha256_stable + 旧 10 个 + 任务相关 1 = 16 个）。
2. Python 跨语言测试：`tests/test_rust_idemlog_writer.py` 5 个：Rust writer 写入 → Python reader 读取双向一致。
3. CI guard：仍是 `scripts/check_rust_authority_migration.py`，**不升级**。

阶段 B + C（未来 PR）：

1. Subprocess 委托的 5 个 integration 测试。
2. 双写一致性测试：同一 (run_id, tool_call_id) Python 写 + Rust 写后读取结果必须一致。
3. CI guard 升级到 hard fail。

## 决策

PR12 阶段 A 落地：

1. `core/runtime-native/src/idemlog.rs`：新增 `IdempotencyWriter` struct + `record_planned` / `mark_executing` / `commit` / `mark_failed` / `mark_uncertain` / `args_sha256` / `operation_id` 函数。
2. `core/runtime-native/src/lib.rs`：导出新符号。
3. `core/runtime-native/src/bin/write_idemlog.rs`：**新 binary**（可选），允许 subprocess 调用 writer。
4. `tests/test_rust_idemlog_writer.py`：5 跨语言测试。
5. `docs/architecture/adr/ADR-013-r1-idemlog-authority-switch.md`：本 ADR。
6. **不**改 Python 端任何写路径。`is_rust_authority("idempotency")` 仍总是 `False`。

## 不变量

- 阶段 A 不改任何生产行为；
- Python `IdempotencyLog` 行为完全等同 PR12 前；
- Rust `IdempotencyWriter` 单元测试 + 跨语言测试必须 100% pass；
- CI guard 仍报 0 violations；
- 完整测试套件：1477 passed（+5 新）/ 6 skipped，无回归。

## 不做

- 不升级 CI guard 到 hard fail；
- 不 wire Python 写入路径到 Rust binary；
- 不引入双写（即使阶段 B 也只在未来 PR）；
- 不修改 Python `IdempotencyLog` 的任何方法。

## 与之前 PR 的关系

- PR #103-#105 给了 R0 + shadow-read；
- PR #106 ADR-011 给了 R1 计划；
- PR #107 ADR-012 给了 R0.5 plumbing；
- **PR12（本 ADR）**：R1 Idempotency 第一刀——Rust 写路径就位（不实际切换）。
