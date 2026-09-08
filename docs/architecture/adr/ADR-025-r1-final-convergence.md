# ADR-025 — R1 Final Convergence

- 状态：Accepted
- 日期：2026-09-09
- 范围：R1 State Foundation 最终收口
- 决策类型：迁移阶段完成记录
- 关联：ADR-017（R1 State Foundation 完成）、ADR-018（R2 Trusted Execution Plan）、ADR-022（Idempotency Hard-Cut）、ADR-023（Ledger Hard-Cut）、ADR-024（Task Identity Hard-Cut）、`docs/governance/rust-core-migration.md`

## Goal

R1 Final Convergence — 完成 R1 State Foundation 最终收口，彻底清除 Run State fallback 与无生产价值的 Storage Transaction 脚手架。

## Run State

### Before

Ledger-derived status with `TaskRun.status` fallback.

```
Ledger 无事实
↓
回退 TaskRun.status
```

### After

Ledger is the sole Run State authority.

```
Run State = Ledger Events 派生结果
```

`TaskRun.status` 不参与任何：

- 调度
- 恢复
- 完成判断
- 失败判断
- 重试
- Automation 控制
- 生命周期控制

`TaskRun.status` 如果保留字段，仅作为 `legacy / denormalized display cache`。

## Storage Transaction

### Before

`core/storage_transaction.py` 提供 `CoreStorage` / `CoreTransaction` Python lock ordering helper。`rollback()` 是 no-op，无真实跨 DB transaction。

### After

Removed — no production authority / no false atomicity abstraction.

已删除：

- `core/storage_transaction.py`
- `tests/test_storage_transaction.py`
- `CoreStorage`
- `CoreTransaction`
- `rollback()` no-op
- `storage_transaction` coordination domain

## 补齐 Ledger 状态事件

R1 Final Convergence 补齐了正式 Ledger 状态事件：

| 状态 | Ledger Event | 派生状态 |
|---|---|---|
| started | `run.started` | `running` |
| resumed | `run.resumed` | `resumed` |
| completed | `run.completed` | `ok` |
| failed | `run.failed` | `error` |
| interrupted | `run.interrupted` | `interrupted` |
| skipped | `run.skipped` | `skipped` |
| cancelled | `run.cancelled` | `cancelled` |
| validation_failed | `validation.failed` | `validation_failed` |
| no events | — | `unknown` |

任何参与 Runtime 判断的状态必须由 Ledger 自身可表达。

## Deleted Legacy Paths

- `TaskRun.status` runtime fallback
- `CoreStorage`
- `CoreTransaction`
- `core/storage_transaction.py`
- `tests/test_storage_transaction.py`
- `rollback()` no-op
- `storage_transaction` coordination domain（`COORDINATION_DOMAINS` 已清空）

## Authority Matrix

| Domain | Authority |
|---|---|
| Task Identity | Rust |
| Run Identity | Rust |
| Run State | Rust Ledger |
| Ledger | Rust |
| Idempotency | Rust |
| Task persistence | Rust |
| Run persistence | Rust |
| Storage coordination | No separate authority |
| Python fallback | None |

## Data Compatibility

历史数据读取兼容允许保留，但必须明确区分 `Legacy Data Compatibility` ≠ `Runtime Authority`。旧历史 `TaskRun` 无 Ledger Events 时，可在展示层或一次性 migration 做兼容。禁止在正式运行路径中 `if ledger unknown: trust TaskRun.status`。

## Failure Behavior

- Ledger 无任何 event → 返回 `unknown`
- 禁止 `unknown → TaskRun.status` silent fallback
- 调用方必须显式处理 `unknown`
- Rust Core 不可用时 fail-closed（`DeltaCoreError`）

## Rollback

Git revert / release rollback only。

## Non-goals

- Artifact Hard-Cut
- Source/Citation Hard-Cut
- Validation Hard-Cut
- Approval
- Policy
- Checkpoint
- Retry
- Timeout
- Cancellation
- Tool lifecycle
- Scheduler migration
- Automation Runtime
- Provider Core
- Rust cross-DB transaction
- 2PC
- 新 unified DB