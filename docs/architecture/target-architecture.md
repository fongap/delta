# Delta 目标架构

> 本文定义 Delta 的长期目标形态。它描述稳定的产品边界、逻辑模块、语言职责和依赖方向，不等同于当前物理目录，也不把尚未完成的迁移写成当前事实。
>
> 当前实现见 `runtime-public-contract.md`、`repository-layout.md`、ADR 与 CHANGELOG；R6 当前执行状态见 `../governance/r6-execution-plan.md`。

## 1. 总体形态

Delta 是一个本地优先的个人工作 AI Agent，只围绕：

```text
日常办公 | 研究分析 | 内容创作
```

底层是一套统一工作系统，不是三个 Agent、三个 Runtime 或三个产品。

```text
                         Delta Desktop
                       TypeScript / React
                              │
                     Tauri Commands / Events
                              │
                              ▼
                         Delta Runtime
                              Rust
                              │
        ┌─────────────┬───────┼────────┬─────────────┐
        ▼             ▼       ▼        ▼             ▼
      Trust          Work  Capability Automation   Learning
                              │
                 ┌────────────┼────────────┐
                 ▼            ▼            ▼
              日常办公       研究分析       内容创作
                              │
                       Task Workers
                 Python / PowerShell / Shell
```

一句话边界：

> **TypeScript 负责产品交互，Rust 负责 Runtime、状态、规则和事实；Python、PowerShell、Shell 只作为受控工作执行语言。**

## 2. 三个产品域

### 日常办公

```text
读取 → 理解 → 修改 / 创建 → 验证 → 预览 → 交付
```

覆盖文档、表格、PDF、演示材料、会议材料、邮件 / 消息、文件处理和正式 Office / Markdown 成果。

### 研究分析

```text
研究问题
→ 假设 / 目标 / 约束
→ DOE
→ 序贯试验设计
→ 数据采集 / 清洗
→ 统计分析 / 建模 / 可视化
→ 结果解释 / 不确定性判断
→ 下一轮试验 / 停止判断
→ 正式研究报告
```

至少覆盖描述统计、统计推断、样本量 / 功效、随机化、区组、全因子 / 部分析因、筛选设计、响应面、最优设计、序贯设计、模型更新和正式研究报告。

序贯设计从第一版架构中存在。Delta 可以推荐下一轮试验，但研究目标、允许调整范围、停止规则和高后果决策必须可记录、可解释，并由用户掌握最终决定。

### 内容创作

```text
选题 → 研究 → 写作 → 编辑 → 图文 / 图片 / 视频
     → 多平台适配 → 发布准备 → 数据复盘 → 下一轮优化
```

包括长短文、自媒体内容、图文、信息图、图片生成与处理、短视频脚本 / 分镜 / 字幕 / 轻量生成处理和发布素材。专业媒体模型 / 编辑器可以作为受控外部能力，不要求核心 Runtime 自己实现媒体引擎。

## 3. 七个长期逻辑模块

### Experience

用户可见交互。主要由 TypeScript / React 实现：Workspace / Session、对话、任务进度、Steer / Follow-up / Cancel、Approval、预览、编辑、Skill / Automation / Settings。

UI 不自行推导权威业务事实。

### Runtime

Rust 负责 Session / Run / Turn、Agent loop、Context / Compaction、Model invocation / streaming、Tool dispatch、Steering、Retry / Resume / Recovery orchestration。

Runtime 决定下一步做什么，但不拥有无限权限。

### Trust

Rust 负责 Policy、Permission / Risk、Approval、Ledger / Audit、Idempotency、Secrets、Sandbox 和 Authority invariants。

> **能力可以进化，权限不能自行进化。**

### Work

Rust 负责 Workspace、Source、Citation、Artifact、Artifact Version、Validation、Provenance 和 Deliverable state。

### Capability

主要扩展面。Rust 作为 Host / Registry / Policy boundary；Native Tool、Worker、MCP、Connector、External Adapter 提供具体能力。

三个产品域主要通过 Capability / Skill 深化，不通过增加 Runtime 或 Agent 数量扩张。

### Automation

Scheduler、Scheduled / Conditional Task、Wake、Inbox、Notification、Inbound Event、Connector lifecycle。Automation 不能绕过 Runtime / Trust / Work。

### Learning

Memory、Preference、Experience、Failure Memory、Skill Candidate、Evaluation、Version、Promotion / Rollback / Retirement。

```text
工作：Goal → Run → Artifact
学习：Run → Feedback → Experience
进化：Experience → Skill Candidate → Evaluate → Promote / Reject
```

## 4. Skill 是主要扩展单位

Skill 是可审查、可版本化的工作方法，可组合：

```text
metadata
instructions
workflow
required capabilities
permissions
validation
templates
optional scripts
```

Learning 可以提出 Candidate Skill，但不能未经治理直接把一次成功任务提升为永久高权限能力。

## 5. 语言边界

核心产品：

```text
Rust + TypeScript
```

工作执行语言：

```text
Python / PowerShell / Shell
```

Worker 不得拥有 Session、Run State、core DB authority、Policy / Approval、Secrets authority、Artifact formal state 或 Runtime control plane。

## 6. 命名边界

> **独立边界用 `delta-*`，内部职责用语义名；品牌化用于识别产品边界，不用于包装每一个内部模块。**

独立 executable、crate / package、跨语言 protocol、SDK 或发布单元可以使用 `delta-*` / `@delta/*`。

示例：

```text
Delta / Delta.exe
delta-runtime
delta-protocol
delta-capability
delta-worker
```

内部职责继续使用：

```text
session
approval
ledger
artifact
validation
memory
experience
skill
```

是否形成独立边界由版本、API、发布、协议、进程和安全隔离需求决定，而不是由“模块重要性”决定。

## 7. 目标依赖方向

Delta 优先采用模块化单体，不为逻辑模块机械创建本地微服务。

```text
Experience (TypeScript)
        ↓
Runtime (Rust)
   ┌────┼────┐
   ▼    ▼    ▼
 Trust Work Capability
   │           │
   ├──────┐    ▼
   ▼      ▼  Worker / External
Automation Learning
```

原则：

- 变化快的 Capability / Experience 不污染变化慢的 Trust；
- Learning 不反向拥有 Trust；
- Worker 不反向拥有 Runtime；
- Automation 不绕过 Runtime；
- Work 的正式事实只由受信任控制面登记；
- 没有充分证据，不增加新进程、新服务、新协议或顶级抽象。

## 8. 模型边界

Delta 只维护：

```text
OpenAI-compatible
Anthropic-compatible
```

复杂 Provider 聚合、多 Key、权重、fallback、额度和区域路由外置给兼容 Endpoint / AI Gateway。

## 9. 成功标准

最终目标不是“Rust 文件更多”，而是：

```text
TypeScript Experience
        ↓
Rust Runtime / Trust / Work
        ↓
Capability / Automation / Learning
        ↓
Controlled Worker / External
```

并且：

- 三产品域可以持续扩展；
- Skill 是主要扩展单位；
- Worker 不拥有核心 Authority；
- Human Control、Recovery、Ledger、Validation 保持稳定；
- Learning 能改善方法但不能自行扩权；
- 本地核心工作不依赖不必要的外部服务；
- 架构和命名都由真实边界驱动，而不是品牌包装。