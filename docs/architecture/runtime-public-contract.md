# Runtime Public Contract

> 本文记录 **R5.1 当前实现的稳定兼容契约**。它不是长期物理架构定义。
>
> 当前 Desktop 仍通过 Python application/server layer 使用 Runtime；多个 trusted domain 已由 Rust `delta_core` authoritative。R6 将把 Runtime Host 和 application control plane 收敛到 Rust，但在明确 ADR 破坏性变更前，应尽量保持本文的用户可见语义、领域对象和事件行为稳定。
>
> 目标架构见 `target-architecture.md`。

## 1. 范围

当前契约包含：

- Task / Session / Run identity 与 lifecycle 语义；
- RunEvent / Ledger 事件；
- SideEffect / Idempotency；
- Approval / Policy 对外行为；
- Artifact / Validation；
- Source / Citation；
- Checkpoint / Recovery；
- Steer / Follow-up / Cancel 的用户控制语义；
- `services/server/` 当前 HTTP / WS 表面；
- Python ↔ Rust protocol compatibility（当前 v16）。

不包含：

- Python class / mixin 内部结构；
- sqlite schema column 顺序；
- Tauri proxy 实现细节；
- Worker 内部实现；
- 测试 fixture；
- 目标 R6 物理目录。

R6 允许替换 Python 实现，但不能把“实现替换”伪装成可以随意改变产品 contract。

## 2. Core 领域对象

### 2.1 Workspace / Session

- Workspace 是本地资源和授权边界；
- Session 是持续工作上下文；
- Session 可以关联 Workspace / roots、model、mode、messages、agent、reasoning effort、grants 等当前兼容字段；
- R6 可改变存储 / IPC 实现，但应保留等价用户语义。

### 2.2 Task

当前 Automation Task 使用稳定 identity，并可产生 TaskRun。

Task 的 completion ownership 在 R5.1 中收敛：Scheduler 是 scheduled-run finalization 的唯一 owner，`complete_run` 原子更新运行结果与 task 调度状态。

### 2.3 Run

- `run_id` 是一次实际执行的稳定 identity；
- 交互 turn、automation 和 resume 都必须归属清晰 Run；
- resume 保持原 Run identity，不把恢复伪装成新 Run；
- Run 是否 recoverable-open 由**最新 lifecycle state**决定，而不是由历史上是否出现过 `interrupted` 决定；
- latest `running` / `resumed` → recoverable-open；
- latest `interrupted` → 不再次由 `open_runs()` 列出，避免 stale recovery 重复处理；
- 显式 `resumed` 后，同一 `run_id` 再次成为 recoverable-open，因此后续再次 crash 仍可恢复；
- completed / failed / skipped / cancelled / validation-failed 保持 closed。

### 2.4 RunEvent / Ledger

核心字段：

```text
run_id
type
seq
ts
actor
payload
workspace
```

长期事件类别包括：

```text
run.*
tool.*
approval.*
side_effect.*
artifact.*
validation.*
user.steer.*
```

R5.1 Steering 事件：

```text
user.steer.requested
user.steer.accepted
user.steer.applied
user.steer.deferred
user.steer.rejected
```

Steering 必须可复盘；UI 中的一次“改一下方向”不能只存在于瞬时内存。

### 2.5 SideEffect

Side-effect lifecycle 的 trusted decision 由 Rust authority 决定。

核心状态：

```text
Planned → Executing → Committed | Failed | Uncertain
```

不变量：

- operation identity 由 `run_id + tool_call_id` 稳定派生；
- 相同 operation identity + 不同 args hash = identity collision，fail-closed；
- 已执行但 trusted persistence 失败 → `Uncertain`，不得自动重放；
- timeout 后无法证明未执行完成的动作 → `Uncertain`，不能假装 Failed 后安全重试。

### 2.6 Artifact

正式 Artifact 必须由 trusted Work/Artifact authority 登记，而不是 Worker 自报“完成”。

核心信息至少包含：

```text
path
name
kind
size
sha256
run_id
incomplete
registered_at
```

R6 Worker 化后仍保持：

```text
Worker staging
  ↓
Boundary / hash / validation
  ↓
Formal Artifact registration
```

### 2.7 Validation

Validation 是“任务是否真正完成”的确定性证据之一。

当前 Criteria 包含文件数量、路径、内容、大小、完整性、CSV headers 和 Citation 等约束。

后续日常办公 / 研究分析 / 内容创作可以增加新的 Validator，但 Validator 不应被模型自然语言结论替代。

研究分析尤其应允许验证：

- 输入数据 identity / hash；
- 分析产物完整性；
- 设计矩阵 / 参数结构；
- 必需报告章节；
- 图表 / structured result 存在；
- Source / Citation consistency。

### 2.8 Source / Citation

Source / Citation 记录任务实际依据。

Validity 不能把“尚未验证”当成“有效”。R5.1 明确：`range_valid = None` 不计为 fully valid，无法验证范围时使用 `range_unverified` 等明确 reason；只有 `reason == "valid"` 才计入 fully-valid citation 数量。

### 2.9 Checkpoint / Recovery

恢复事实必须由 trusted authority 保存。

恢复应保留：

- run identity；
- phase；
- pending action / human decision；
- last committed event；
- todo / work summary；
- recent artifacts；
- uncertain side effects；
- error context。

R6 可以替换 Python Recovery glue，但不得降低这些语义。

## 3. Human Control Contract

R5.1 将活动 Run 的用户控制明确分为三个不同语义。

### 3.1 Steer

- 修改**当前 Run** 的方向；
- 保持同一个 `run_id`；
- 在 Runtime safe point 应用；
- 必须进入可审计事件；
- 如果当前阶段不能安全应用，应 deferred / rejected，而不是悄悄丢失。

### 3.2 Follow-up

- 排队到当前 Run 完成后处理；
- 不修改当前 Run 已在执行的方向；
- 不应和 Steer 混成同一个“发送消息”动作。

### 3.3 Cancel

- 请求停止当前 Run；
- 必须尊重 side-effect uncertainty；
- 不得把已经产生的副作用伪装成未执行；
- 不得无提示丢弃 queued follow-up。

这三个语义在未来 TS → Rust direct IPC 后继续保持，不依赖 Python Server 存在。

## 4. Runtime Protocol v16

R5.1 当前 Python ↔ Rust control protocol 为 v16。

关键 contract：

- 每个 request 有 `request_id`；
- streaming / long command 不阻塞主输入读取；
- stdout reader + per-request demux；
- real `request.cancel` 按 `request_id` 取消；
- bounded inflight / backpressure；
- control request 可在普通 inflight 饱和时保持可用；
- protocol mismatch fail-closed；
- shutdown 采用 graceful cancel → bounded wait → force-close。

R6 将逐步减少跨进程 Python ↔ Rust protocol 在主产品路径中的地位，但这些并发、取消和 backpressure 语义应迁移到 Rust Runtime Host，而不是丢失。

## 5. 当前 HTTP / WS Compatibility Surface

`services/server/` 当前仍是 Desktop / browser 的 application API 边界。

已有用户可见行为和 JSON/WS 语义在 R6 迁移期间应通过 TypeScript domain API、Tauri commands/events 或兼容 shim 保持，除非经过 ADR 明确改变。

当前重要 API 类别包括：

- session / run driving；
- approval / inbox；
- artifacts / sources / validation；
- automation；
- provider / settings；
- connectors / skills / memory；
- app-wide / session event streams。

具体 Python endpoint 路径不是目标架构永远必须保留的网络协议；**用户行为和领域 contract 才是需要稳定迁移的部分**。

## 6. 不变量

1. **一个领域一个 Authority**：迁移不能恢复 Python/Rust 双主控。
2. **一份 run identity 贯穿事实链**：Ledger、Artifact、Validation、SideEffect、Source 等必须可归属到同一 Run。
3. **Worker 不修改核心状态**：Capability Worker 只返回 structured result / staging；Rust 正式登记。
4. **Uncertain 不自动 replay**：无法证明安全时 fail-closed。
5. **Human control 可审计**：Steer / Cancel 等影响执行方向的动作必须成为 Runtime 事实。
6. **UI 不推导 Authority**：React 显示状态，但不自行决定 Run / Approval / Validation 事实。
7. **Learning 不改变权限**：Experience / Skill promotion 不得绕过 Trust。
8. **迁移实现可以改变，产品语义不能静默漂移**。

## 7. R6 兼容策略

目标调用路径：

```text
TypeScript
   ↓
Tauri Commands / Events
   ↓
Rust Runtime Host
```

迁移优先顺序：

1. 保持前端 domain types / user semantics；
2. 在 Rust 实现等价 Runtime API；
3. 逐项切换 TypeScript 调用；
4. contract / E2E 双向验证；
5. 删除对应 Python endpoint / manager / facade；
6. 不长期维护 TS → Python → Rust 纯转发链。

## 8. 变更流程

### 非破坏性

新增字段、事件、validator 或 capability metadata，可以普通 PR，但必须有测试和文档。

### 破坏性

删除字段、改变 lifecycle / approval / side-effect / steering 语义、改变持久化 identity 等，必须先有 ADR。

### Runtime replacement

如果仅把同一 contract 从 Python 搬到 Rust，不需要重新发明产品模型；应以 contract test + E2E 证明行为保持。
