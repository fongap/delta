# ADR-017 — R1 State Foundation 完成

- 状态：Active
- 日期：2026-09-07
- 范围：Delta Core R1 State Foundation 全部 5 个领域 + 统一进程入口
- 决策类型：迁移阶段完成记录
- 关联：ADR-009（Delta Core Architecture）、ADR-010（Shadow-Read）、ADR-011（Authority Switch Plan）、ADR-012~016（各领域 Delegate）、`docs/governance/rust-core-migration.md` §5 R1

## 背景

ADR-011 提议"先不立即切换权威"，按领域逐个 PR 推进。ADR-012~016 落地了灰度开关、三个领域的 delegate wrapper 和跨语言写路径。

本 ADR 记录 R1 State Foundation 的**完成状态**：5 个领域的权威路径全部就位、统一进程入口已实现、CI gate 已落地。

## 决策

### R1 完成范围

R1 State Foundation 在 v0.4.0-dev 线上完成以下 13 个 PR：

| PR | 主题 | 关键交付 |
|---|---|---|
| P0-A | 版本边界 | `v0.3.2` ↔ `v0.4.0-dev` 边界明确；11 个版本源 bump |
| P0-B | SideEffect ↔ Ledger contract | 4 个集成测试；发现并修复 `_execute_sync` 未传 `ledger=` 的契约缺口 |
| P0-C | Per-domain authority selector | `DELTA_RUST_AUTHORITY` 从全局布尔改为逗号分隔的 per-domain 列表 |
| P0-D | Idempotency authority path | `sweep_stale` delegate bypass 修复；`mark_uncertain` 产生 ledger 事件 |
| P1-A | Ledger authority | `RunEventLedger` 并发锁（`threading.RLock`）；`register_artifact` ledger wiring；结构+并发+崩溃测试 |
| P1-B | Task identity authority | 结构守护测试；所有生产路径经 `maybe_wrap_taskstore` |
| P1-C | Run state authority | `RunEventLedger.run_status(run_id)` 从 ledger 事件派生运行状态（单一事实源） |
| P1-D | Storage transaction boundary | `CoreStorage` / `CoreTransaction` 抽象；确定性锁序；协调提交点 |
| P0-1 | Unified Rust Core process | 三个 delegate 全部走 `DeltaCoreClient`（统一 `delta_core` 进程）；per-op CLI 仅诊断 |
| P0-2 | Binary naming consistency | Cargo `delta_core` / build script `delta_core.exe` / `DeltaCoreClient` 查找名统一 |
| P0-3 | Authority fail-closed | authority 声明后 binary 缺失 → `DeltaCoreError`，禁止静默 fallback |
| P0-4 | Run state single source of truth | `RunEventLedger.derive_run_status()` 派生；`TaskRun.status` 仅为 cache |
| P0-5 | Storage transaction reality | `CoreTransaction` 文档与实现一致：协调锁序，非跨 DB 原子事务 |
| P0-6 | Production authority CI | 真实生产调用链测试（SessionManager → delegate → DeltaCoreClient → delta_core → SQLite → Python read-back） |
| P1-E | 统一 Delta Core 进程入口 | `delta_core` Rust 二进制（stdin/stdout JSON line protocol）+ `DeltaCoreClient` Python 客户端 |
| P1-F | Package Delta Core | 便携版构建脚本集成 `delta_core.exe`；多位置 binary lookup |
| P1-G | CI gates | `rust-core-smoke` CI job + `check_rust_core_smoke.py` 脚本 + 迁移数据库测试 |
| Docs | Authority Matrix + ADR | 本 ADR + Authority Matrix 更新 |

### Authority Matrix（R1 完成后 → R1 Hard-Cut 后）

| 领域 | R1 前 | R1 完成后 | R1 Hard-Cut 后（ADR-022） |
|---|---|---|---|
| Idempotency | Python | Python（opt-in Rust delegate） | **Rust（唯一事实来源）** |
| Ledger | Python | Python（opt-in Rust delegate） | Python（opt-in Rust delegate） |
| Task identity | Python | Python（opt-in Rust delegate） | Python（opt-in Rust delegate） |
| Run state | Python | Python（`run_status()` 从 ledger 派生） | — |
| Storage transaction | Python | Python（`CoreTransaction` 协调锁序；非跨 DB 原子事务） | — |

### 统一进程入口（P1-E）

`delta_core` Rust 二进制取代 per-write subprocess 模式：

- 一个常驻进程处理多个 domain operation（`ledger.append` / `idem.record_planned` / `task.save` 等）
- Python 端通过 `DeltaCoreClient` 持有持久连接
- 连接缓存复用（同一 DB 的多次请求共享 `Connection`）
- 现有 per-operation CLI binary（`write_idemlog` / `write_ledger` / `write_tasks`）保留为迁移诊断工具

### CI Gates（P1-G）

1. **Authority Regression Guard** — `scripts/check_rust_authority_migration.py`（per-domain 强制）
2. **Cross-language Contract Tests** — `tests/test_migration_db.py`（Python 写 → Rust 读 + Rust 写 → Python 读）
3. **Smoke Gate** — `scripts/check_rust_core_smoke.py`（ping + ledger hash chain + idem full cycle + task save/run/delete）
4. **CI Job** — `rust-core-smoke` 在 `ci.yml` 中，Rust 源码变更时触发

### 不变量（R1 期间保持）

- 行为测试基线不漂移：62 个新测试全部通过
- Python 读取者继续工作：所有 `TaskStore.list` / `IdempotencyLog.committed_for_run` 等从同一 SQLite DB 读取
- Ledger 链不变：Python ↔ Rust `hash` / `prev_hash` 100% 一致
- 幂等性不变：`operation_id` 派生规则 100% 一致

## 结果

- R1 State Foundation 5 个领域 + 统一进程入口全部就位
- 灰度开关默认关闭（`DELTA_RUST_AUTHORITY` 未设置时行为等同 v0.3.2）
- CI gate 覆盖 authority regression + cross-language contract + smoke
- 下一步：R2 Trusted Execution（Artifact / Validation / Checkpoint / Policy / Approval）

## R1.5 生产切权收口（2026-09-07）

R1 全部 13 个 PR 完成后再做一次"生产切权"收口（`Delta R1.5 — Rust Core 生产切权收口`）：

| 任务 | 状态 |
|---|---|
| P0-1 统一 Rust Core 调用 | ✅ 三个 delegate 全部走 `DeltaCoreClient` |
| P0-2 二进制命名一致性 | ✅ 修复 `delta-core.exe` → `delta_core.exe`；新增便携式查找测试 |
| P0-3 Authority fail-closed | ✅ `DeltaCoreError` 禁止静默 fallback |
| P0-4 Run status 单一事实源 | ✅ `derive_run_status()`；Analyzer + manager_automations 改用 |
| P0-5 Storage transaction 真实化 | ✅ 文档与现实一致；`rollback()` 明确 no-op |
| P0-6 Production Authority CI | ✅ SessionManager → delta_core 全链路 + 反向守卫 |
| P1-1 DeltaCoreClient 生命周期 | ✅ command timeout + stderr 排水 |
| P1-2 Portable 实机 smoke | ✅ 中文+空格路径测试 |

**R1 状态三态澄清**：

| 维度 | 状态 | 说明 |
|---|---|---|
| R1 State Foundation 基础设施 | Complete | 5 个领域 delegate wrapper + 统一进程入口 + CI gate 全部就位 |
| Rust Authority 能力 | Available（opt-in） | 灰度开关 `DELTA_RUST_AUTHORITY` 可逐域启用；默认关闭 |
| Production Default Authority | Python（未切换） | `DELTA_RUST_AUTHORITY` 未设置时，所有写入走 Python 路径；生产主路径不变 |

> 注意：`Operationally Complete` 仅表示 R1 迁移阶段的基础设施交付完成，**不代表** 生产默认权威已切换到 Rust Core。默认权威切换是独立决策，需在 R2 前基于用户验证数据单独裁定。

## R1.5 Idempotency Hard-Cut（ADR-022）

2026-09-08：Idempotency 域完成 hard-cut（ADR-022）：

| 任务 | 状态 |
|---|---|
| `core/idemlog_delegate.py` 物理删除 | ✅ |
| `tests/test_idemlog_delegate.py` 删除 | ✅ |
| `DELTA_RUST_AUTHORITY=idempotency` 选择逻辑删除 | ✅ |
| `is_rust_authority("idempotency")` 从 `storage_authority.py` 移除 | ✅ |
| `core/idemlog.py` 改为 Rust Authority 薄门面 | ✅ |
| Rust Core 不可用时 fail-closed | ✅ |
| CI binary provisioning 修复 | ✅ |
| 架构守卫测试（4 个） | ✅ |
| 便携版 + 历史数据库验证 | ✅ |

## 明确不做

- 不在 R1 内删除 Python 写入路径（灰度开关默认关闭，Python 仍是默认权威）
- 不引入 Capability Worker 主体
- 不引入 JSON-RPC / IPC 框架
- 不重命名 Python 模块
- 不在 R1 内切换默认权威（需 R2 前的用户验证）
