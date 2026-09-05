# Rust Core Migration Governance

## 1. 目的

本文规定 Delta 从 Legacy Python Runtime 向 Rust Control Plane 迁移时的治理规则。

目标不是“重写 Python”。

目标是：

> **把核心领域控制权单向迁移到 Rust，同时保持产品语义、数据一致性和可回滚性。**

---

## 2. 总原则

迁移必须遵循：

1. 控制权按领域迁移；
2. Python → Rust 单向迁移；
3. 一个领域只能有一个 authority；
4. 迁移期间允许兼容，不允许长期双主控；
5. 产品行为先保持一致，再优化实现；
6. 不以语言占比作为 KPI；
7. 不借迁移扩大产品范围；
8. 不在迁移期间同步推进大规模 P3 智能能力。

---

## 3. Authority Matrix

目标状态：

| 领域 | 当前可能实现 | 最终 Authority |
| --- | --- | --- |
| Task | Python | Rust |
| Run | Python | Rust |
| Runtime | Python | Rust |
| Scheduler | Python | Rust |
| Policy | Python | Rust |
| Approval | Python | Rust |
| Ledger | Python | Rust |
| Checkpoint | Python | Rust |
| Resume | Python | Rust |
| Artifact Registry | Python | Rust |
| Validation | Python | Rust |
| Idempotency | Python | Rust |
| Tool lifecycle | Python | Rust |
| Worker lifecycle | 分散 | Rust |
| Provider Core | Python | Rust-first |
| PDF / Office | Python | Capability Worker |
| OCR / STT | Python / Rust service | Capability Worker |
| Data / ML | Python | Capability Worker |
| 特殊 SDK | Python | Capability Worker |
| UI | TypeScript | TypeScript |
| Tauri Host | Rust | Rust |

每次迁移必须更新实际 Authority Matrix。

---

## 4. Legacy Python Runtime 冻结规则

Legacy Python Runtime 可以：

- 修复安全问题；
- 修复 P0/P1 功能问题；
- 补充测试；
- 提供 Rust migration 对照；
- 保持现有功能可运行。

原则上不得新增：

- 新 Ledger 体系；
- 新 Approval 体系；
- 新 Scheduler；
- 新 Checkpoint 模型；
- 新 Artifact authority；
- 新 Validation authority；
- 新长期 Runtime 状态；
- 与 Rust Core 重叠的新控制面。

如果新增能力不可避免，应优先确认是否应该直接实现于 Rust Core。

---

## 5. 迁移阶段

### R0 — Architecture Contract

必须先完成：

- Rust Control Plane ADR；
- Capability ABI；
- Process Supervisor 基础；
- Worker Manifest；
- Error contract；
- Progress / Cancel contract；
- Artifact staging；
- Authority Matrix；
- 行为测试基线。

R0 完成前禁止大规模搬代码。

### R1 — State Foundation

迁移：

- Task / Run identity；
- Run state；
- Ledger；
- Idempotency；
- Storage transaction boundary。

### R2 — Trusted Execution

迁移：

- Artifact Registry；
- Validation；
- Checkpoint；
- Policy；
- Approval；
- Source / Citation 核心事实关系。

### R3 — Execution Lifecycle

迁移：

- Tool lifecycle；
- Retry；
- Backoff；
- Timeout；
- Cancellation；
- Worker restart；
- Resume decision；
- side-effect safety。

### R4 — Runtime

迁移：

- Task execution；
- Workflow lifecycle；
- Scheduler；
- Automation Runtime；
- Resume orchestration。

R4 完成后，普通任务不得继续依赖 Legacy Python Runtime 作为主控。

### R5 — Provider Core

Rust-first：

- OpenAI-compatible；
- Anthropic-compatible；
- streaming；
- tool call；
- reasoning metadata；
- usage；
- retry；
- timeout；
- routing；
- fallback。

---

## 6. 单领域迁移流程

每个领域按以下顺序执行：

```text
1. 明确当前 Python 行为
2. 补齐行为测试
3. 定义 Rust domain contract
4. 实现 Rust authority
5. Shadow / dual-read 验证
6. 切换权威写入
7. 禁止 Python direct write
8. 删除不必要兼容路径
9. 更新 CI
10. 更新 Authority Matrix
```

---

## 7. 双写规则

原则上禁止长期双写。

如果迁移期必须双写：

- 必须明确 primary authority；
- secondary 只能用于验证；
- 不得由 secondary 驱动产品行为；
- 必须设置删除条件；
- 必须有一致性检查；
- PR 中说明结束时间点或退出条件。

禁止：

```text
Python 和 Rust 都能独立把 Run 标记 Completed
```

允许短期：

```text
Rust writes authoritative state
Python shadow-computes expected state
CI / test compares result
```

---

## 8. 数据迁移

数据迁移必须优先保持：

- Run identity；
- Ledger continuity；
- Approval history；
- Artifact relation；
- Validation result；
- Checkpoint semantics。

不得为了 Rust migration 随意重置用户历史。

如果 schema 必须改变：

- 提供 migration；
- 提供 rollback 或兼容读取策略；
- 测试旧数据库升级；
- 测试异常中断后的数据库一致性。

---

## 9. 测试要求

每个控制权迁移 PR 至少验证：

- existing behavior；
- restart；
- crash；
- approval；
- validation failure；
- side-effect replay；
- idempotency；
- artifact registration；
- ledger consistency。

涉及 Worker 的 PR 额外验证：

- worker crash；
- timeout；
- cancellation；
- malformed response；
- protocol mismatch；
- staging boundary。

---

## 10. CI Guard

CI 应逐步增加：

- Rust Core tests；
- Capability protocol compatibility；
- Legacy authority regression；
- forbidden direct DB write；
- forbidden Python write after migration；
- layout check；
- Rust/Python boundary check。

已经迁移的领域不得通过后续 PR 重新把 authority 放回 Python。

---

## 11. PR 规则

涉及 Rust Core Migration 的 PR 必须说明：

### Domain

迁移哪个领域。

### Authority Before

当前谁是权威。

### Authority After

合并后谁是权威。

### Compatibility

是否存在临时兼容路径。

### Exit Condition

何时删除兼容路径。

### Failure / Rollback

失败后如何回退。

### Tests

如何证明产品语义未漂移。

---

## 12. 冻结范围

Rust Control Plane 收敛前，原则上不主动扩大：

- 自动 Reflection；
- Failure Memory；
- Skill Evaluation；
- 高级 Long-term Context；
- 复杂 Multi-Agent；
- 大量新增 Provider；
- 大规模 Connector 扩张；
- 复杂 Federation；
- 新一轮独立 Runtime。

已有能力可以修复和收敛。

---

## 13. 不允许借迁移做的事

禁止把 Rust migration 变成：

- 全仓库重命名；
- 无关 UI 重构；
- 新设计系统；
- Provider 大扩张；
- Prompt 体系重写；
- 产品语义重做；
- 数据库全面换型；
- “顺手”重写所有 Python。

迁移 PR 应保持边界小而明确。

---

## 14. 完成标准

Rust Core Migration 完成不以：

- Rust 百分比；
- Python 文件数量；
- crate 数量；

判断。

完成标准是普通任务主路径满足：

```text
UI
 ↓
Rust Core
 ↓
Capability ABI
 ↓
Worker / MCP / External
```

并且以下领域由 Rust 权威持有：

- Task；
- Run；
- Policy；
- Approval；
- Ledger；
- Checkpoint；
- Artifact；
- Validation；
- Idempotency；
- Runtime；
- Scheduler；
- Worker lifecycle。

Python 可以继续占据相当代码量，只要它不再拥有系统控制权。

---

## 15. 最终原则

> **迁移控制权，不迁移生态优势。**

> **Rust 掌控事实与后果，Python 提供专业能力。**

> **一个领域，一个 authority。**
