# ADR-015 — R1 Ledger Authority Switch: 阶段 B（实际委托）

- 状态：Accepted (历史)
- 日期：2026-09-05
- 范围：R1 Ledger 域权威切换（Python → Rust）阶段 B
- 关联：ADR-009 / ADR-010 / ADR-011 / ADR-012 / ADR-013 / ADR-014 / `docs/governance/rust-core-migration.md` §5 / §6 / §7 / §11

> **R1.5 更新（2026-09-07）**：本 ADR 描述的"委托 `write_ledger` per-op subprocess"实现已被 P0-1 取代。R1.5 实际生产路径：delegate 改走统一 `delta_core` 进程（`DeltaCoreClient` → `delta_core` → SQLite），`write_ledger` 仅保留为诊断工具。详见 ADR-017 §R1.5 生产切权收口。

## 背景

PR #104 (Ledger cross-lang) 落地了 Rust `LedgerReader` + `verify_ledger` binary + 5 跨语言测试，证明 Rust 端能够 byte-equal 地验证 Python 端写入的 hash chain。**没有任何 Python 写路径被改动**。

本 ADR 描述 Ledger 域的阶段 B：让 Python `RunEventLedger` 在 env var 启用时**实际**委托 `append` 给 Rust `LedgerWriter` subprocess。

PR #109 (ADR-014 阶段 B) 已经在 Idempotency 域做了同构设计。Ledger 阶段 B 复用同一种 `maybe_wrap` 模式，但 append 路径要承担 hash chain 拼接——这是 Ledger 域独有的不变量。

## §11 8 字段

### Domain

`ledger` —— `run-events.db` 表 `run_events` 的写入权威。`run_state` 域共享同一张表，但其 authority switch 计划在 R2 单独描述（见 ADR-011 §提议路径）。

### Authority Before

Python `RunEventLedger` 直接写 SQLite。Rust 仅有 `LedgerReader` / `verify_ledger` shadow-read 工具。

### Authority After

**默认行为不变**：所有现存调用方继续使用 `RunEventLedger` 类，行为完全等同本 PR 之前。

**显式 opt-in 行为**：

- 当 `DELTA_RUST_AUTHORITY=1` **且** `core/runtime-native/target/debug/write_ledger(.exe)` binary 存在时，调用方使用 `core.ledger_delegate.RunEventLedgerWithDelegate`（或 `maybe_wrap_ledger`）会委托所有 `append` 调用到 Rust subprocess。
- 读方法（`events` / `events_in_workspace` / `runs` / `open_runs` / `verify` / `recover_stale`）保持 Python 端。
- 关闭 env var 或删除 binary → 自动回退到 Python-only 行为。

### Compatibility

- 现有所有调用方继续工作（`RunEventLedger` 不变）。
- 新增 `RunEventLedgerWithDelegate` 是 opt-in wrapper。
- **不引入双写**：写要么走 Python，要么走 Rust，不同时写两个。
- 读路径完全不动（Python `RunEventLedger` 直接读同一 SQLite 文件）。
- Hash chain byte-equal: `core/ledger.py` 和 `core/runtime-native/src/ledger.rs` 实现完全相同的 `sha256(prev_hash|seq|type|actor|repr(ts)|canonical(payload))` basis。

### Exit Condition

- 测试环境：5 跨语言 test + 6 delegate test 100% pass + 全部 1488 pytest test pass。
- 部署后：4 周观察期内 Python ↔ Rust 行为 byte-equal（hash chain / verify / sanitize contract）。
- 关闭条件：CI guard 升级到"如果 env var = 1 但 Python 仍直接写 ledger SQLite 则 hard fail"——本 PR 落地此 enforcement。

### Failure / Rollback

- **delegate 路径失败**（Rust binary 缺失 / subprocess 失败）：抛 `RuntimeError`——设计上的"fail-loud"，因为静默回退到 Python 写会违反 env var 的契约。
- **回滚本 PR**：`git revert` 即可，零生产影响。
- **生产环境回滚**：`unset DELTA_RUST_AUTHORITY`，所有现存调用方自动回退。
- **Hash chain 不一致**：理论上不可能（basis 完全相同）；如果发生，证明 Rust 端某处浮点 repr / canonical JSON 实现偏差，需要回滚本 PR + 修 bug。

### Tests

- 5 个新 cross-lang test 在 `tests/test_rust_ledger_writer.py`：
  1. `test_rust_writes_run_started_python_reads`
  2. `test_rust_writes_hash_chain_verifies_via_python`
  3. `test_rust_writes_workspace_column`
  4. `test_rust_writes_runs_listing`
  5. `test_rust_writer_hash_matches_python_writer`（关键：Rust ↔ Python hash chain byte-equal）
- 6 个新 delegate test 在 `tests/test_ledger_delegate.py`：
  1. `test_maybe_wrap_returns_plain_ledger_when_authority_off`
  2. `test_maybe_wrap_returns_delegate_when_authority_on`
  3. `test_delegate_writes_via_rust_and_python_reads_back`
  4. `test_delegate_sanitizes_payload`
  5. `test_delegate_workspace_index`
  6. `test_plain_ledger_path_unchanged_when_authority_off`
- Rust unit test：20 passed (16 + 4 new writer tests)。
- 完整 Python 测试套件：1494 + 11 = 1505 passed（5 cross-lang + 6 delegate）。

## 决策

本 PR 落地：

1. `core/runtime-native/src/ledger.rs`：新增 `LedgerWriter` + 4 unit test（first event / extends chain / verifies via reader / independent runs）。
2. `core/runtime-native/src/bin/write_ledger.rs`：CLI binary 接受 `--db / --run-id / --type / --actor / --ts / [--payload] / [--workspace]`，输出 JSON。
3. `core/runtime-native/Cargo.toml`：新增 `[[bin]]` 条目（`write_ledger` + 之前 PR #108 隐式构建的 `write_idemlog` 都加显式条目以避免未来 cargo 行为变化）。
4. `core/runtime-native/src/lib.rs`：re-export `LedgerWriter`。
5. `core/ledger_delegate.py`：`RunEventLedgerWithDelegate` + `maybe_wrap_ledger`，scrubs payload via `packages.sanitize.sanitize_payload` before forwarding to Rust subprocess。
6. `services/server/manager.py`：`self.run_ledger` 从 `RunEventLedger(...)` 改为 `maybe_wrap_ledger(RunEventLedger(...))`。
7. `core/analyzer.py`：`_isolated_ledger()` factory 也通过 `maybe_wrap_ledger` 包装（避免 enforcement guard 误报）。
8. `scripts/check_rust_authority_migration.py`：enforcement 模式扩展到 `RunEventLedger(`（增加 `RUN_LEDGER_DIRECT` + `MAYBE_WRAP_OR_DELEGATE_LEDGER` + `LEDGER_REFERENCE` + `DELEGATE_FILES` 排除）。
9. `tests/test_rust_ledger_writer.py`：5 跨语言测试。
10. `tests/test_ledger_delegate.py`：6 delegate 测试。
11. 本 ADR。

## 不变量

- `RunEventLedger(db)` 的行为**完全等同**本 PR 之前；
- `RunEventLedger` 的 hash basis（`prev_hash|seq|type|actor|repr(ts)|canonical(payload)`）保持完全一致；
- 写之前通过 `packages.sanitize.sanitize_payload` 清洗 payload，hash basis 与 Python append 一致；
- `RunEventLedgerWithDelegate` 读方法直接调 inner，与原 `RunEventLedger` 字节级一致；
- `RunEventLedgerWithDelegate` 写方法在 env var = 1 时**短路** Python 写入（不双写）。

## 不做

- 不在 `RunEventLedger` 类内部添加任何 Rust 委托逻辑；
- 不修改 `core/runtime.py` / `core/idemlog.py` / `core/artifact.py` 等任何 `ledger.append(` 调用方（透明转发已经覆盖）；
- 不实现 `run_state` 域的 authority switch（ADR-011 §提议路径计划在 R2 单独 PR 处理）；
- 不引入双写。

## 与 ADR-014 关系

ADR-014 是 Idempotency 域的同构阶段 B。本 ADR 是 Ledger 域的同构阶段 B。两个域复用同一种 `maybe_wrap_X` 模式但每个域独立开关——idempotency 和 ledger 在 `DELTA_RUST_AUTHORITY=1` 下可独立激活或回退（domain check 在 `_is_delegate_active()` 内）。
