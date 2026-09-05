# ADR-009 — Delta Core Architecture (Rust Control Plane + Capability Worker)

- 状态：Accepted
- 日期：2026-09-05
- 范围：Delta Core Runtime / Execution / Storage Authority
- 决策类型：长期架构决策
- 取代：本 ADR 不取代既有 P1/P2/P3 阶段 ADR；与 ADR-005 / ADR-006 / ADR-007 / ADR-008 并列。命名上与 `ADR-006-p2-source-citation-and-convergence.md` 区分。

## 背景

Delta 起源于以 Python 为核心运行时的架构。
当前 Python Core 已经承载：

- Task / Run；
- Workflow；
- Policy / Approval；
- Ledger；
- Artifact；
- Validation；
- Resume；
- Automation；
- Provider；
- Tool / Integration。

这一架构有利于快速迭代，但随着 Delta 从"Agent 原型"向"本地优先、可恢复、可验证、可审计的个人 AI 工作系统"演进，控制面继续集中在 Python 会带来长期问题：

- 核心状态与能力执行耦合；
- Worker 崩溃容易影响主控生命周期；
- 状态、Policy、Ledger、Resume 等关键事实缺少强边界；
- Desktop 依赖本地 HTTP/WebSocket Python Server；
- Provider、Tool、Automation、Resume 等路径容易继续形成隐式第二事实源；
- Python Runtime 持续增加长期核心职责会抬高未来迁移成本。

Delta 不需要全面 Rust 重写。
需要迁移的是：

> **控制权，而不是所有代码。**

## 决策

Delta 采用：

> **Rust Control Plane + Capability Worker**

架构：

Rust Core 最终成为以下领域的唯一事实源：

- Task；
- Run；
- Runtime lifecycle；
- Workflow execution lifecycle；
- Scheduler；
- Policy；
- Approval；
- Ledger / Audit；
- Checkpoint / Resume；
- Artifact Registry；
- Validation；
- Idempotency；
- Tool lifecycle；
- Retry / Backoff / Timeout / Cancellation；
- Capability Registry；
- Worker supervision；
- 核心 Storage transaction boundary；
- 主要 Provider protocol / routing。

Python、Node、MCP Server 或其他进程只作为 Capability Worker / Adapter 存在。
Capability Worker 可以执行专业能力，但不得拥有核心状态和编排权。

## 核心原则

### 1. Rust 是唯一控制面

所有高后果状态变化最终通过 Rust Core 提交。

允许：

```text
Worker
  ↓
Structured Result
  ↓
Rust Core
  ↓
Policy / Validation / Transaction
  ↓
Ledger / DB / Artifact Registry
```

禁止：

```text
Python → Core DB
Rust   → Core DB
UI     → Core DB
```

### 2. Worker 只提供能力

Capability Worker 可以负责：

- PDF / Office；
- OCR；
- STT；
- 数据处理；
- AI / ML；
- 专业格式；
- 特殊第三方 SDK。

Worker 不得直接修改：

- Task；
- Run；
- Approval；
- Ledger；
- Checkpoint；
- Artifact Registry；
- Validation；
- Automation execution state。

### 3. 控制权单向迁移

迁移方向只能是：

```text
Python authority
      ↓
Rust authority
```

已经迁入 Rust 的领域不得重新由 Python 直接写入。
不允许长期双主控。

### 4. 不制造第二套 Runtime

迁移期允许 Legacy Python Runtime 临时存在。
但它必须是：

- 兼容执行路径；
- 行为测试基线；
- 尚未迁移领域的临时实现。

不得继续扩大其长期核心职责。

### 5. 不以语言占比作为 KPI

Rust Core Migration 不以：

- Rust 代码比例；
- Python 代码比例；
- crate 数量；

作为完成指标。
完成度以"领域控制权归属"判断。

## Capability ABI

Rust Core 与本地 Worker 之间通过稳定 Capability ABI 通信。
详细规范见 `docs/architecture/capability-abi.md`。

默认：

```text
Control Plane:
JSON-RPC / NDJSON over stdio

Data Plane:
file handle / path handle / artifact staging
```

大型：

- PDF；
- 图片；
- 音频；
- 视频；
- Office 文件；

不通过大体积 Base64 长期传输。

MCP 作为外部 Capability Adapter，而不是 Delta 内部 Runtime 定义。

## Provider 决策

Delta 不追求原生支持大量 Provider。
Rust Core 优先稳定：

- OpenAI-compatible；
- Anthropic-compatible。

包括：

- streaming；
- tool call；
- reasoning metadata；
- usage；
- retry；
- timeout；
- routing；
- fallback。

特殊非标准 Provider 可通过 Capability Adapter 或 Gateway 接入。

## Worker 隔离

子进程隔离不等同于强安全沙箱。
Rust Core 应逐步提供：

- process lifecycle；
- timeout；
- termination；
- workspace-bound input；
- output staging；
- 最小环境变量；
- resource limits；
- network permission metadata；
- crash detection。

平台能力成熟后再使用 OS 原生隔离机制。

## Ledger 安全语义

Rust 不等于"绝对不可篡改"。
Delta Ledger 的目标是：

> **tamper-evident**

即通过：

- append-only semantics；
- hash chain；
- transaction；
- checkpoint；
- 必要时的签名 / 外部 anchor；

让篡改可以被检测。

## 恢复语义

Delta 不承诺恢复到某个 LLM Token。
恢复边界是：

> **最后一个已提交且可证明的 Step / Event / Checkpoint。**

LLM 流输出和外部系统调用本身可能不具备确定性。

## v0.3.x 阶段基线（前置事实）

v0.3.1 release 完成后，Runtime 已经完成一组与 ADR-009 决策一致的"生产路径"硬化（PR #95-#99）：

- P0-A Side Effect Crash Safety（PR #95）
- P0-B Recovery Production Wiring（PR #96）
- P1-A Async Retry（PR #97）
- P0-B Reference Task Harness（PR #98）
- P1-D Artifacts / Citations / Analyzer 收口（PR #99）

本批之后下列接口属于"Delta Core 公共契约"，变更必须经 ADR：

- `core/idemlog.py` / `core/recovery.py` / `core/artifact.py` / `core/validation.py`；
- `services/server/{manager.py, app.py, manager_*.py}` 的对外契约。

这些接口未来在 Rust Core 迁移时由 Rust 实现，但 Python 端的语义、字段名、事件类型在迁移完成前**保持稳定**——这就是"先冻结契约再迁权威"。

## 结果

这一决策意味着：

- Desktop 正常主路径最终不再依赖 localhost Python FastAPI；
- Tauri 直接与 Rust Core 通信；
- Python Runtime 逐步退出系统主控位置；
- Python 生态继续保留；
- Rust Core 负责事实与后果；
- Capability Worker 负责专业执行。

## 明确不做

本 ADR 不代表：

- 全面 Rust 重写；
- 删除所有 Python；
- 迁移所有文档解析库；
- 自研所有 AI SDK；
- 把 MCP 当内部 Runtime；
- 用 Rust 代码比例衡量成功；
- 同时保留 Rust/Python 两套永久 Runtime。

## 迁移要求

迁移必须遵守 `docs/governance/rust-core-migration.md`。
协议边界必须遵守 `docs/architecture/capability-abi.md`。
仓库职责必须遵守 `docs/architecture/repository-layout.md`。
