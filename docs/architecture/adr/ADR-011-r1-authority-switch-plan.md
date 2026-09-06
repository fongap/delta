# ADR-011 — R1 Authority Switch: 计划与边界

- 状态：Proposed
- 日期：2026-09-05
- 范围：Delta Core 权威归属迁移（Python → Rust）
- 决策类型：迁移阶段规划
- 关联：ADR-009（Delta Core Architecture）、ADR-010（R1 Shadow-Read）、`docs/governance/rust-core-migration.md` §5 R1 / §6 单领域迁移流程 / §7 双写规则

## 背景

PR #103-#105 完成了 R1 的 **shadow-read** 阶段：

- `core/runtime-native/` 落地为 `delta-runtime-native` crate；
- `LedgerReader` + `IdempotencyReader` lib 已能验证 Python 写入的事实；
- `verify_ledger` + `dump_idemlog` CLI binary 通过跨语言测试证明 Python ↔ Rust 语义一致。

但**权威仍未切换**。Python 仍是 `run_events.db` / `side-effects.db` / `tasks.db` 的唯一写入者。

`docs/governance/rust-core-migration.md` §5 R1 包含 5 个领域：

1. **Task / Run identity** — `tasks.db` (scheduled_tasks + task_runs)；
2. **Run state** — `run_events.db` (run_events 表)；
3. **Ledger** — `run_events.db` (同一表，Run state 的子集)；
4. **Idempotency** — `side-effects.db` (side_effects 表)；
5. **Storage transaction boundary** — SQLite → 未来的事务边界。

R1 不允许"全栈切换"，必须按 §6 单领域流程逐步推进。

## 决策

R1 authority switch **不立即开始**。本 ADR 提议**先做规划，不动代码**。

### 理由

1. **范围尚未明确**：R1 包含 5 个领域，每个领域的写入者、读取者、灰度策略不同。一次 PR 切换 5 个领域的权威违反 `rust-core-migration.md` §6 "一个领域只能有一个 authority" + §7 "禁止长期双写"。

2. **灰度策略缺失**：R0 §7 明确要求"必须明确 primary authority / secondary 只能用于验证 / 不得由 secondary 驱动产品行为 / 必须设置删除条件 / 必须有一致性检查"。当前 Python 端没有任何开关允许灰度。

3. **回滚条件未定**：R0 §11 PR 规则要求 8 字段（Domain / Authority Before / Authority After / Compatibility / Exit Condition / Failure / Rollback / Tests）。当前没有 ADR 文档化任何特定领域的回滚步骤。

4. **CI Guard 缺失**：R0 §10 要求 "forbidden direct DB write" / "forbidden Python write after migration" guard 写到 CI，但当前 ci.yml 没有任何 Python 写入 SQLite 的静态检查。

5. **Capability Worker 边界未落地**：R0 §3 还需要 Process Supervisor、Capability Registry、Capability Discovery 的最小实现。仅做权威切换而没有 supervisor 边界，会让 Rust Core 变成"另一个 Python 写入者"，达不到 ADR-009 的目标。

### 提议路径

R1 之后的几个 PR 应按以下顺序落地：

#### PR10（**本 ADR 仅文档**）

本 ADR 提交后，未来任何 R1 authority switch 必须按 §单领域迁移流程写单独的 PR-level ADR（领域级 ADR-012/013/...）：

- 一个 PR 切换一个领域；
- 每个 PR 包含 §6 流程的全部 10 步证据；
- ADR 列出 §11 全部 8 字段。

#### PR11（**Pre-R1 plumbing**：灰度开关 + CI guard）

在写任何 Rust 写入代码之前，先做基础设施：

1. **`core/storage.py` 抽象层**：把 `sqlite3.connect` 包到 `StorageBackend` 协议下；
2. **`DELTA_RUST_AUTHORITY=0|1` 环境变量**：从 Python 端选择权威；
3. **CI static check**："Python module X writes to `tasks.db` only if `os.environ.get('DELTA_RUST_AUTHORITY') != '1'`"；
4. **`core/runtime-native/src/bin/inspect_*.rs`**：每个领域一个 inspect binary，用于 shadow-read 一致性检查；
5. **ADR-012 Pre-R1 Plumbing** 记录上述 5 件事的契约。

这一步**不切换权威**——只为后续切换做准备。

#### PR12+（**实际权威切换**，每个领域一个 PR）

按 §6 流程，对 5 个领域分别切换：

- **PR12 — Idempotency authority switch**：side_effects 表的 Python 写入改为 Rust 写入（这是状态机最简单、风险最低的领域）；
- **PR13 — Ledger authority switch**：run_events 表的 Python 写入改为 Rust 写入；
- **PR14 — Run state authority switch**：run_events 的 seq 计数器 / 终止事件判定（与 Ledger 紧密耦合）；
- **PR15 — Task/Run identity authority switch**：tasks.db 的 scheduled_tasks / task_runs 表；
- **PR16 — Storage transaction boundary**：把 SQLite 抽象层换成真正的 Rust transaction boundary（与未来 R2-R5 共用）。

每个 PR 必须满足 §7 双写规则 + §9 测试要求 + §10 CI guard + §11 PR 规则。

### 强制约束

- **不得跳过 PR11**：Pre-R1 plumbing 必须先做，否则任何"权威切换"都会变成"新 Python 写入者"，违反 ADR-009 §1 "Rust 是唯一控制面"。
- **不得双 PR 切换两个领域**：每个 PR 独立一个领域，独立一个 PR-level ADR。
- **不得在 R1 内引入 Capability Worker 主体**：Capability Worker 是 R3 的范围。R1 只做 Process Supervisor 最小集（fork + wait + exit code + timeout）。
- **不得引入 JSON-RPC / IPC 框架**：capability-abi.md 的协议规范已就位，但 R1 不需要 IPC——Python 直接通过 SQLite 共享 state，Rust 独立验证。这是有意为之的"非最优雅但可证伪"路径。
- **不得重命名 Python 模块**：R1 期间 Python 端保持向后兼容；只有当某个领域完全切到 Rust 且 Python 写入路径被 CI 强制禁用后，才允许删除/重命名对应 Python 模块。

## 不变量（迁移期间永久成立）

- **行为测试基线不漂移**：`pytest tests/` 全部通过；每个领域切换后必须有 cross-language 一致性测试（PR #104、#105 的模式）。
- **Python 读取者继续工作**：R1 期间所有 Python 读取代码（`TaskStore.list` / `IdempotencyLog.committed_for_run` 等）继续从同一 SQLite DB 读取，行为不变。
- **Ledger 链不变**：`run_events.hash` 链从 Python → Rust 切换前后必须 100% 一致。
- **幂等性不变**：`operation_id` 派生规则（`sha256("{run_id}:{tool_call_id}")[:36]`）从 Python → Rust 切换前后必须 100% 一致。

## 结果

本 ADR 提议：

- **不立即**开始 R1 authority switch；
- **不**在本次 PR 写任何 Rust 写入代码；
- **不**引入任何 Python 端 storage 抽象（PR11 才做）；
- 后续 PR 必须按 §6 单领域流程，且每个领域一个 PR + 一个 PR-level ADR。

## 明确不做

- 不写 Rust 写入 path；
- 不引入 Process Supervisor 主体；
- 不引入 Capability Worker 主体；
- 不引入 JSON-RPC / IPC；
- 不重命名任何 Python 模块；
- 不在 R1 期间把 `core/` 拆分。

## 完成标准

R1 authority switch 完成的判定（与 `rust-core-migration.md` §14 类似）：

- 普通任务主路径的 5 个领域（Task identity / Run state / Ledger / Idempotency / Storage transaction boundary）由 Rust 权威持有；
- Python 端对应写入路径被 CI 强制禁用；
- 跨语言一致性测试覆盖每个领域；
- 在 R1 完成后，R2 (Artifact / Validation / Checkpoint / Policy / Approval) 启动。
