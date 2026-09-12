# Runtime Convergence Governance

> 本文规定 R5.1 之后 Delta 从过渡态收敛到 **Rust + TypeScript 核心产品架构**的长期治理规则。
>
> R1–R5.1 的具体迁移历史、Protocol 版本和单领域 hard-cut 证据保留在 ADR、PR、审计记录和 CHANGELOG 中；本文不再重复维护完整历史流水账。

## 1. 目标

目标不是“把所有 Python 翻译成 Rust”，也不是提高 Rust 行数占比。

目标是：

> **把核心产品控制权、事实和常驻 Runtime 收敛到 Rust，把用户交互收敛到 TypeScript，并把 Python / PowerShell / Shell 降为受控工作执行语言。**

最终主路径：

```text
TypeScript Experience
        ↓
Tauri Commands / Events
        ↓
Rust Runtime Host
   ┌────┼──────────────┐
   ▼    ▼              ▼
 Trust Work       Capability Host
                     ↓
             Worker / MCP / External
```

## 2. 已完成基础

R1–R5 已完成的核心方向：

- Run / Ledger / Idempotency / Task identity 等 trusted state 向 Rust hard-cut；
- Artifact / Source / Citation / Validation / Checkpoint / Policy / Approval 等 trusted execution authority 向 Rust hard-cut；
- Tool lifecycle / cancellation / timeout / retry decision authority 向 Rust 收敛；
- Run lifecycle / automation completion authority 向 Rust 收敛；
- OpenAI-compatible / Anthropic-compatible provider transport 与主要 decision authority 向 Rust 收敛。

R5.1 进一步完成：

- Runtime correctness AF-01..AF-14 closure；
- multiplexed protocol v16；
- request identity / demux / real cancel / backpressure；
- scheduler single completion owner；
- side-effect fail-closed 与 identity collision；
- citation validity 修正；
- Steer / Follow-up / Cancel 的 Human Control 基础。

这些能力构成 R6 的迁移 seam，不应在 R6 中重新发明。

## 3. R6 最终目标

### 3.1 核心产品语言

```text
Rust + TypeScript
```

- TypeScript：Experience / React UI；
- Rust：Runtime、Trust、Work、Automation、Learning authority、Capability Host。

### 3.2 工作执行语言

允许：

```text
Python
PowerShell
Shell
```

但身份只能是 Worker / Script。

Python 文件数量不是治理指标。判断标准是 Python 是否拥有系统 Authority。

### 3.3 最终禁止的主路径

R6 完成后不得继续存在：

```text
TypeScript
   ↓
Python HTTP/WS Server
   ↓
Python Manager / TurnEngine
   ↓
Rust Authority
```

也不得长期存在纯转发：

```text
TypeScript → Python facade → Rust
```

## 4. Authority Matrix

R6 目标：

| 领域 | 最终 Authority / Owner |
| --- | --- |
| Experience / UI state presentation | TypeScript |
| Session / Run / Turn lifecycle | Rust |
| Agent loop / Context / Compaction | Rust |
| Provider protocol / streaming / routing decision | Rust |
| Tool dispatch / lifecycle decision | Rust |
| Scheduler / Automation lifecycle | Rust |
| Policy / Risk / Approval | Rust |
| Ledger / Audit / Idempotency | Rust |
| Secrets authority | Rust |
| Workspace / Source / Citation | Rust |
| Artifact / Version / Validation / Provenance | Rust |
| Memory / Experience / Skill evaluation authority | Rust |
| Office / statistics / media algorithms | Capability Worker allowed |
| Python / PS / Shell process execution | Rust-supervised Worker |

一个领域只能有一个 Authority。

## 5. Python 冻结规则

从 R5.1 后开始，Python 控制面进入冻结状态。

### 允许

- 修复安全 / P0 / P1 问题；
- 保持迁移前行为；
- 补 contract / regression tests；
- 临时 migration shim；
- Office / Research / Media 专业算法；
- Worker / Script implementation。

### 原则上禁止新增

- Session Manager；
- Runtime Manager；
- 长期 Store Authority；
- 新 Scheduler authority；
- 新 Policy / Approval authority；
- 新 Provider control plane；
- 新核心数据库 writer；
- 新常驻 Python 服务职责；
- 新 TS → Python → Rust forwarding surface。

如果新能力需要控制状态，应默认进入 Rust。

## 6. R6 分阶段顺序

### R6-0 — Product Surface Cleanup

先做减法：

- 默认产品身份收敛到 Delta；
- 删除 / 隔离 Code Agent 产品入口；
- 删除 Coding 专用 Git / repo / PR 产品能力；
- 保留通用 File / Search / Shell / Script 能力；
- 不影响日常办公、研究分析、内容创作的真实任务执行。

目的：减少后续迁移面。

### R6-1 — Rust Runtime Host

基于现有 Runtime contract 建立 Rust implementation：

```text
run
resume
retry
steer
follow-up
cancel / interrupt
runtime events
```

要求：

- 复用已存在 Rust authority；
- 不创建第二套事实模型；
- 用 contract tests 对齐当前 Runtime 行为；
- Human Control v16 语义不能退化。

### R6-2 — TypeScript Direct IPC

逐步从：

```text
TS → HTTP/WS → Python Server
```

迁到：

```text
TS → Tauri Commands / Events → Rust
```

原则：

- 优先保持 `api.ts` 等前端 domain semantics；
- 换 Backend，不顺手重做 UI；
- 每切一组接口就删除对应 Python forwarding path。

### R6-3 — Application Control Plane

迁移：

- Session lifecycle；
- Runtime registry；
- Settings / Secrets authority；
- Scheduler / Inbox / Automation orchestration；
- Source / Artifact / Validation application wiring；
- Connector / MCP host boundary。

### R6-4 — Agent Loop

最后迁移最复杂的 intelligence loop：

```text
context
→ model
→ tool calls
→ trust decision
→ tool execution
→ result
→ next iteration
```

Agent loop 迁移前必须确认 Provider、Tool lifecycle、Steering、Resume、Cancel 和 process supervision 的 Rust seam 已足够稳定。

### R6-5 — Capability Workerization

将专业 Python 代码明确分类：

```text
KEEP AS WORKER
MOVE TO RUST
DELETE
```

Office、Research、Statistics、OCR、Media 等优先保留生态优势，改为受控 Worker，而不是为了语言纯度重写。

### R6-6 — Python Backend Removal

完成后删除不再需要的：

- persistent FastAPI / Uvicorn application control plane；
- Python SessionManager / TurnEngine authority；
- duplicate provider SDK / routing fallback；
- aisuite 等仅为旧 Runtime/tool abstraction 服务的依赖；
- PyInstaller `delta-server` sidecar packaging；
- pure forwarding DTO / facade / compatibility path。

### R6-7 — Hard-Cut

最终验收：

```text
Core app/runtime = Rust + TypeScript
Python = Worker / task scripts only
No persistent Python backend authority
No TS → Python → Rust forwarding main path
```

## 7. Worker 规则

Python / PowerShell / Shell Worker 必须通过 Capability ABI。

Worker 不得：

- 拥有 Session；
- 拥有 Run lifecycle；
- 直接写 core DB；
- 决定 Approval / Policy；
- 保存核心 Secrets；
- 决定 Artifact 正式状态；
- 绕过 Rust 获取 Network / File 权限；
- 作为常驻 application server 承担主控。

Worker 应支持：

- scoped input；
- staged output；
- timeout；
- cancel；
- process supervision；
- typed error；
- progress；
- resource / network grants。

## 8. Skill / Learning 边界

R6 不需要暂停全部 Skill / Learning 工作，但必须避免 Learning 反向扩大迁移面。

允许：

- Skill schema / registry contract；
- Experience evidence；
- Skill Candidate / Evaluation 数据模型；
- 只读分析和用户显式 Skill 管理。

禁止在 Trust / Runtime 尚未 hard-cut 前引入：

- 自动降低审批；
- 自动调整 Risk Level；
- 自动扩大 Network / Secrets 权限；
- Learning 自改 Runtime；
- 自动把一次成功任务直接提升为永久高权限 Skill。

原则：

> **能力可以进化，权限不能自行进化。**

## 9. 产品扩展冻结边界

R6 期间可以继续修复和验证三产品域，但不把迁移与“大扩建”绑定。

原则上不同步进行：

- 新 WorkItem / Project 抽象；
- 复杂 Multi-Agent 平台；
- 新 Coding 产品线；
- 第三模型协议；
- 模型市场；
- 大规模 Connector 扩张；
- 重型视频编辑器；
- 无证据的新 Workflow Engine；
- 全新 UI 设计系统重写。

日常办公、研究分析和内容创作需要新增的 Capability 可以做，但应优先 Worker / Skill 化，不能重新扩大 Python control plane。

## 10. 单领域迁移流程

每个迁移域按：

```text
1. 审计当前真实 call graph
2. 补 contract / regression test
3. 明确 Authority Before
4. 定义 Rust contract
5. 实现 Rust owner
6. 切换调用方
7. fail-closed / compatibility verification
8. 删除 Python owner / forwarding path
9. 更新 CI / docs
10. 明确 Authority After
```

不要长期停在“Rust 已有一份、Python 也继续一份”。

## 11. 兼容规则

迁移期允许 compatibility seam，但必须满足：

- 只有一个 primary Authority；
- fallback 不得偷偷恢复已经 hard-cut 的 Python writer；
- 有明确删除条件；
- 有 contract / E2E 验证；
- PR 写清 Exit Condition。

新代码不得依赖“Rust 不可用就静默走 Python”作为长期可用性策略。

## 12. 数据与用户历史

迁移必须优先保持：

- Session / Run identity；
- Ledger continuity；
- Approval history；
- Source / Citation；
- Artifact / Version；
- Validation；
- Memory / Skill state；
- Automation state。

不能为了删除 Python Backend 重置用户数据。

## 13. CI Guard

R6 CI 应逐步增加：

- Rust Runtime contract tests；
- TypeScript ↔ Rust IPC contract；
- Capability ABI compatibility；
- forbidden Python authority regression；
- forbidden direct core DB write from Worker；
- forbidden new persistent Python server authority；
- no fallback to migrated Python owner；
- portable packaging smoke；
- E2E Human Control（Steer / Follow-up / Cancel）；
- layout check。

## 14. PR 规则

涉及 R6 的 PR 必须写清：

### Scope

迁移哪个明确职责，不混入大规模产品功能。

### Authority Before

当前谁拥有状态 / 决策。

### Authority After

合并后谁拥有状态 / 决策。

### Compatibility

有哪些临时 shim / fallback。

### Exit Condition

什么时候删除它们。

### Failure / Rollback

如何安全回退而不恢复双 Authority。

### Tests

如何证明用户行为、事实和安全边界未漂移。

## 15. 完成标准

R6 完成不以：

- Rust 百分比；
- Python 文件数；
- crate 数；
- 删除行数；

判断。

完成标准：

```text
TypeScript Experience
        ↓
Rust Runtime Host
        ↓
Trust / Work / Capability / Automation / Learning
        ↓
Controlled Worker / External
```

并且：

- 常驻 Python application backend 不再是产品依赖；
- Runtime / Session / Agent loop / Scheduler 不再由 Python 拥有；
- Worker 无核心 Authority；
- Office / Research / Media Python 生态仍可通过 Capability ABI 使用；
- Portable / CI / E2E 正常；
- 三产品域的现有真实工作不因迁移退化。

## 16. 最终原则

> **迁移控制权，不迁移生态优势。**

> **TypeScript 管产品交互，Rust 管系统与事实，Worker 负责具体工作。**

> **一个领域，一个 Authority。**

> **先删除重复控制面，再增加新抽象。**

> **长期、稳定、高效、安全、敏捷优先于语言纯度和架构形式感。**
