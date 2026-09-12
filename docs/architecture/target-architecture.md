# Delta 目标架构

> 本文定义 Delta 的长期目标形态。它描述稳定的产品边界、逻辑模块、语言职责和依赖方向，不等同于当前物理目录，也不提前把尚未完成的迁移写成当前事实。
>
> 当前实现状态以 ADR、`runtime-public-contract.md`、`repository-layout.md` 和 CHANGELOG 为准；迁移治理以 `docs/governance/rust-core-migration.md` 为准。

## 1. 总体形态

Delta 是一个本地优先的个人工作 AI Agent。

产品只围绕三个工作域持续做深：

```text
日常办公 | 研究分析 | 内容创作
```

底层不是三个独立 Agent、三个 Runtime 或三个产品，而是一套统一工作系统：

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

## 2. 产品域

### 2.1 日常办公

覆盖个人高频办公成果：

- 文档创建、修改、整理与格式转换；
- 表格读取、整理、计算和交付；
- PDF 阅读、比较、提取和处理；
- 演示材料与会议材料；
- 邮件、消息和资料整理；
- 文件批处理和轻量自动化；
- DOCX / XLSX / PPTX / PDF / Markdown 等正式成果。

目标不是“能读文件”，而是形成：

```text
读取 → 理解 → 修改 / 创建 → 验证 → 预览 → 交付
```

### 2.2 研究分析

“研究分析”取代旧的一级名称“数据分析”。它不是简单问数、汇总或画图，而是完整研究闭环：

```text
研究问题
  ↓
假设 / 目标 / 约束
  ↓
试验设计
  ↓
序贯试验
  ↓
数据采集 / 导入 / 清洗
  ↓
统计分析 / 建模 / 可视化
  ↓
结果解释 / 不确定性判断
  ↓
下一轮试验 / 停止判断
  ↓
确认
  ↓
正式研究报告
```

能力至少覆盖：

- 数据完整性、清洗、缺失值和异常值；
- 描述统计、探索性分析和可视化；
- 参数 / 非参数检验、ANOVA、卡方、相关与回归；
- 模型诊断、效应量、置信区间和实际意义解释；
- 样本量 / 功效；
- 随机化、区组、全因子 / 部分析因、筛选设计、响应面和最优设计；
- **序贯试验设计**：首轮最小信息设计、中期分析、模型更新、信息缺口、下一轮推荐、范围收缩 / 扩展、停止规则和确认试验；
- 正式分析 / 研究报告。

序贯设计从第一版研究分析架构中就存在。系统可以推荐下一轮试验，但研究目标、允许调整范围、停止规则和高后果决策必须保持可记录、可解释，并由用户掌握最终决定。

### 2.3 内容创作

覆盖个人和自媒体内容从想法到发布复盘的完整生产链：

```text
选题 → 研究 → 写作 → 编辑 → 图文 / 图片 / 视频
     → 多平台适配 → 发布准备 → 数据复盘 → 下一轮优化
```

包括：

- 选题、受众、标题、系列策划和内容日历；
- 资料研究、事实核查和来源整理；
- 长文、短文、公众号、博客、社交媒体、口播稿和脚本；
- 编辑、压缩、扩写、风格统一和一稿多发；
- 图文笔记、信息卡片、长图、封面、信息图和数据图解；
- 图片生成、编辑、裁切和平台尺寸适配；
- 短视频脚本、分镜、字幕、图文转视频、素材编排、轻量生成 / 处理和发布素材包；
- Markdown / HTML / DOCX 等内容交付；
- 阅读、互动和传播数据复盘。

Delta 负责编排、资产和成果管理；专业图像 / 视频模型或编辑器可以作为受控外部能力，不要求核心 Runtime 自己实现媒体引擎。

## 3. 七个长期逻辑模块

模块按长期职责和变化速度划分，不按历史目录机械切层，也不要求立即拆成独立 crate。

### 3.1 Experience

**职责：用户可见的产品交互。**

主要由 TypeScript / React 实现：

- Workspace / Session；
- 对话与任务进度；
- Steer / Follow-up / Cancel；
- Approval / Ask User；
- 文件、文档、表格、图表、图片、视频和 Artifact 预览；
- Source / Citation 展示；
- Skill、Automation、Settings 管理。

约束：

> UI 只展示和发出意图，不自行推断权威业务事实。

### 3.2 Runtime

**职责：决定一个任务下一步做什么。**

Rust 最终负责：

- Session / Run / Turn；
- Agent loop；
- Context assembly / compaction；
- Model invocation / streaming；
- Tool selection / dispatch；
- Steer / Follow-up / Cancel；
- Retry / resume / recovery orchestration；
- OpenAI-compatible / Anthropic-compatible model protocol。

Runtime 不拥有无限权限；所有有后果的动作必须进入 Trust。

### 3.3 Trust

**职责：安全、权威和不可绕过的事实边界。**

Rust 负责：

- Policy；
- Permission / Risk；
- Approval；
- Ledger / Audit；
- Idempotency；
- Secrets；
- Sandbox / execution limits；
- Authority invariants。

核心原则：

> **能力可以进化，权限不能自行进化。**

任何 Skill、Worker、Connector、模型和 Learning 结果都不得绕过 Trust。

### 3.4 Work

**职责：用户工作的资料、证据和成果。**

Rust 负责正式事实：

- Workspace；
- Source；
- Citation；
- Artifact；
- Artifact Version；
- Validation；
- Provenance；
- Deliverable state。

典型结果：

```text
Run
├── Sources
├── Citations
├── Intermediate Artifacts
├── Validation Results
└── Final Artifacts
```

### 3.5 Capability

**职责：完成工作的能力扩展面。**

Rust 作为 Host / Registry / Policy boundary，具体能力可以由 Native Tool、Worker、MCP 或 Connector 提供。

包含：

- Tool Registry；
- Skill Registry / Loader；
- Script / Worker Runner；
- File / Web / Office / Research / Media capability；
- MCP / Connector tool surface；
- Validation hooks。

三个产品域主要在 Capability 层扩展，而不是通过增加 Runtime 或 Agent 数量扩展。

### 3.6 Automation

**职责：在没有当前前台交互时触发和推进工作。**

包括：

- Scheduler；
- Scheduled / Conditional Task；
- Wake；
- Inbox；
- Notifications；
- Messaging / inbound events；
- Background task；
- Connector lifecycle。

Automation 不能成为旁路。它创建或恢复 Run 后仍通过 Runtime、Trust、Work 和 Capability。

### 3.7 Learning

**职责：让 Delta 越用越适合用户，但不自行扩权。**

包括：

- Memory；
- Preference；
- Experience；
- Failure Memory；
- Workflow Experience；
- Skill Candidate；
- Skill Evaluation；
- Skill Version；
- Promotion / Rollback / Retirement。

三条循环：

```text
工作：Goal → Run → Artifact
学习：Run → Feedback → Experience
进化：Experience → Skill Candidate → Evaluate → Promote / Reject
```

## 4. Skill 是主要扩展单位

Delta 不通过持续增加专用 Agent 扩大产品。

一个 Skill 是可审查、可版本化的工作方法，至少可以描述：

```text
Skill
├── metadata
├── instructions
├── workflow
├── required capabilities
├── permissions
├── validation
├── templates
└── optional scripts
```

例如：

```text
稳定性趋势研究
├── 数据完整性检查
├── 批次 / 时间点识别
├── 描述统计
├── 趋势和批间比较
├── 统计方法与前提检查
├── 序贯下一步建议
├── 图表
└── 正式报告
```

Skill 可以由用户创建、导入、编辑、版本化和退役。Learning 可以提出 Candidate Skill，但不得未经规则和用户控制直接把一次任务变成永久能力。

## 5. 受控自我进化

允许系统根据真实 Run 证据改善：

- Prompt strategy；
- Skill instructions；
- Workflow；
- Templates；
- Tool selection；
- 报告结构；
- 脚本模板；
- 用户偏好；
- 已验证的失败规避规则。

禁止 Learning 自动修改或降低：

- Runtime authority；
- Policy / Approval；
- Risk level；
- Sandbox boundary；
- Secrets access；
- Network permission；
- Ledger / Validation authority。

推荐晋级链：

```text
重复任务 / 明确反馈
        ↓
Experience
        ↓
Candidate Skill
        ↓
历史任务回放 / Validation / Evaluation
        ↓
用户确认或明确治理规则
        ↓
Promoted Skill
```

## 6. 语言边界

### 6.1 产品实现语言

最终产品实现只保留：

```text
Rust + TypeScript
```

- TypeScript：Experience / React UI；
- Rust：Runtime、Trust、Work、Automation、Learning authority 和 Capability Host。

### 6.2 工作执行语言

Python、PowerShell、Shell 可以大量存在，但身份是 Worker / Script：

```text
structured input
      ↓
Rust Policy / Sandbox
      ↓
Python / PowerShell / Shell
      ↓
structured result / staged files
      ↓
Rust Validation / Artifact / Ledger
```

Worker 不得：

- 拥有 Session；
- 拥有 Run State；
- 写 Delta 核心数据库；
- 决定 Approval / Policy；
- 保存系统 Secrets authority；
- 决定 Artifact 正式状态；
- 绕过 Rust 自行获得额外权限；
- 成为 Delta 的常驻后台主控。

因此仓库中存在 Python 文件不意味着 Delta 是 Rust + TypeScript + Python 三语言核心架构。

## 7. 目标依赖方向

Delta 优先采用模块化单体，不为模块边界创建不必要的本地微服务。

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
- 没有充分证据，不增加新进程、新服务、新协议或新的顶级抽象。

## 8. 模型边界

Delta 只维护：

```text
OpenAI-compatible
Anthropic-compatible
```

复杂 Provider 聚合、多 Key、权重、fallback、额度和区域路由继续外置给兼容 Endpoint / AI Gateway。

## 9. 从当前实现到目标架构

R5.1 完成的是 Runtime correctness 与 human control 收敛，不等于 Python Backend 已经退出。

后续 R6 的目标是**控制权和进程拓扑收敛**，不是机械翻译全部 Python：

1. 删除不属于三产品域的 Coding 产品面；
2. 冻结 Python 控制面新增；
3. 建立 Rust Runtime Host；
4. TypeScript 逐步从 HTTP/WS Python sidecar 改为 Tauri Commands / Events 直连 Rust；
5. 迁移 Session / Scheduler / Application Control Plane；
6. 迁移 Agent Loop；
7. 将 Office / Research / Media Python 代码 Worker 化；
8. 删除常驻 Python Backend、重复 Provider SDK 路径和不再需要的中间 facade；
9. 完成 Rust + TypeScript hard-cut。

迁移期间当前稳定公共契约优先保持兼容，除非通过 ADR 明确批准破坏性变更。

## 10. 长期架构过滤器

任何新设计先回答：

1. 它直接改善日常办公、研究分析或内容创作的哪个真实工作环节？
2. 它属于七个长期模块中的哪一个？
3. 谁拥有状态和最终 authority？
4. 能否作为 Capability / Skill 扩展，而不是扩张 Runtime？
5. 是否引入了不必要的新语言、新进程、新协议或新服务？
6. 是否绕过 Trust、Work 或 Validation？
7. 能否安全回滚、恢复和审计？

如果这些问题不能得到清晰回答，就不进入主线。
