# Delta 蓝图

> Delta 的长期产品与架构边界。
>
> 本文定义 Delta 要解决的问题、核心原则、能力范围和演进方向。具体代码结构以 `docs/architecture/repository-layout.md` 为准；Runtime Authority、协议版本和迁移完成状态以当前 ADR、运行时公共契约和 CHANGELOG 中已经完成的事实为准。

---

## 1. 定位

Delta 是一个面向个人知识工作的本地优先 AI Agent。

核心场景是：

- 办公；
- 数据分析；
- 研究与资料整理；
- 文档处理；
- PDF / 表格等本地资料工作；
- 自媒体与内容生产；
- 其他能够形成明确交付物的个人知识工作。

Delta 不是单纯的聊天客户端，也不是模型聚合器，更不是 Coding Agent。

Delta 的基本目标是把真实工作从需求推进到可检查的成果：

```text
Goal
  ↓
Context
  ↓
Plan
  ↓
Execution
  ↓
Policy / Approval
  ↓
Validation
  ↓
Artifact
  ↓
Ledger
  ↓
Completion / Resume
```

Delta 关注的不是“模型回答了什么”，而是：

> **事情是否真正完成，以及完成过程是否可信。**

短期做可靠，中期做实用，长期做智能。能力数量不是目标。

---

## 2. 硬边界

以下边界属于长期产品约束，不因某个外部项目或短期功能需求随意扩大。

### 2.1 不做 Coding Agent

Delta 不以软件开发作为产品方向，不建设：

- IDE；
- 大型代码仓库理解；
- Git / Pull Request 工作流；
- 代码审查平台；
- 编译、测试、发布编排中心；
- 面向软件工程项目的专用 Agent Runtime。

已有 Coding 相关能力不得成为产品主线，后续应按真实依赖情况逐步缩小、隔离或移除。

### 2.2 保留任务型脚本能力

Delta 可以为完成知识工作编写、执行、检查和修正脚本，例如：

- Python 数据清洗、统计、绘图；
- Excel / CSV / PDF / DOCX 批处理；
- PowerShell 文件整理和 Windows 自动化；
- Shell 调用本地工具；
- 图片、音视频和内容素材的批量处理；
- 为特定任务生成可复用的小型自动化脚本。

脚本属于执行工具，不属于产品目标。

> **Delta 可以写脚本完成工作，但不把“开发软件”作为工作本身。**

脚本执行仍必须服从 Workspace Boundary、Policy、Approval、Run State、Timeout、Cancellation、Ledger、Artifact 和 Validation。

### 2.3 模型协议只保留两类

Delta 的模型协议层固定为：

```text
OpenAI-compatible
Anthropic-compatible
```

不新增第三类原生模型协议。

模型供应商、模型名称、账号体系和具体路由不是 Delta 的核心产品语义。只要 Endpoint 符合上述两类协议之一，即可由用户按需配置。

### 2.4 不做模型聚合器

Delta 不负责：

- Provider 聚合；
- 大规模模型目录；
- 多 Provider 权重调度；
- Provider fallback 网络；
- 成本路由；
- 区域路由；
- 模型市场。

这些能力如有需要，由兼容 Endpoint、AI Gateway 或其他独立基础设施负责。

Delta 只关心：

> **给定一个符合支持协议的 Endpoint，能否可靠调用并把结果纳入统一 Runtime。**

### 2.5 不设单一上游

Delta 是独立项目，不以任何外部项目作为上游产品或架构来源。

外部项目可以作为参考样本。某个项目在 Runtime、UI、安全、办公能力、数据能力或 Agent 设计上有更好的做法时，可以研究其语义和实现，但必须按照 Delta 自身边界重新评估和落地。

不得为了跟随外部项目而恢复已经淘汰的目录、协议、产品形态或 Compatibility Layer。

---

## 3. 产品原则

### 3.1 本地优先

Delta 的核心运行时和工作状态以本地为中心。

工作区、对话、任务与运行状态、模型配置、密钥、审批记录、来源索引、Memory、执行记录、交付物、自动化状态默认保存在本地或用户明确控制的位置。

网络服务是能力，不是核心运行的前置依赖。

### 3.2 结果优先

Delta 的基本工作单位不是一轮对话，而是一个可以完成的 Task。

任务应尽可能形成明确结果，例如：

- 文档；
- 表格；
- 分析报告；
- 图表；
- 结构化数据；
- 处理后的文件；
- 内容素材；
- 脚本；
- 可恢复的后续任务。

### 3.3 人掌握后果

模型可以规划和调用能力，但不能天然获得所有权限。

高后果操作必须进入统一 Policy / Approval，例如：

- 覆盖或删除文件；
- 执行高风险命令；
- 访问工作区边界外资源；
- 联网和外部传输；
- 修改外部系统；
- 发送消息；
- 使用敏感凭据；
- 不可逆操作。

权限判断由系统约束，而不是依赖 Prompt 或模型自觉。

### 3.4 可验证

“模型认为完成”不等于任务完成。

任务成功应尽可能由确定性事实支撑，例如：

- Artifact 真实存在；
- 文件格式有效；
- 数据结构符合要求；
- 命令退出状态正确；
- 指定内容已写入；
- Citation 可以定位；
- 预定义 Validation 通过。

无法自动验证时，应明确验证边界。

### 3.5 可恢复

长任务可以暂停、等待审批、中断、关闭应用后继续。

恢复必须避免：

- 重复执行已经产生副作用的动作；
- 把半完成步骤误判为成功；
- 丢失等待中的审批；
- 丢失 Artifact；
- 丢失风险和未完成事项。

### 3.6 可复盘

Delta 应能够回答：

- 用户要求什么；
- 计划是什么；
- 使用了哪些来源；
- 调用了哪些模型和工具；
- 写了或执行了哪些脚本；
- 修改了哪些文件；
- 哪些动作获得审批；
- 哪一步失败；
- 如何恢复；
- 最终交付了什么；
- 结果如何验证。

重要事实进入统一 Run / Ledger 语义。

### 3.7 可改进但不可失控

长期行为变化应遵循：

```text
Candidate
  ↓
Evidence
  ↓
Evaluation
  ↓
Acceptance / Policy
  ↓
Use
  ↓
Revocation
```

短中期 Preference、Skill 和长期行为以用户显式维护为主，不因一次任务自动提升为长期规则。

---

## 4. 核心对象

Delta 围绕稳定领域对象收敛，不要求它们与代码目录一一对应。

### 4.1 基础闭环

- **Workspace**：用户工作的默认资源与授权边界。
- **Task**：用户希望完成的一件事情。
- **Run**：Task 的一次实际执行。
- **Plan**：完成 Task 的结构化方案。
- **Approval**：需要用户决策的执行边界。
- **Artifact**：可以独立查看、验证或交付的正式成果。
- **Validation**：对步骤、状态或 Artifact 的确定性检查结果。
- **Audit Event / Ledger**：任务运行中的关键事实记录。

### 4.2 证据与资料

- **Source**：任务使用的事实来源。
- **Citation**：结论与 Source 中具体证据位置的关系。

目标不是先建设通用知识库，而是先回答：

> **这个结论依据什么？**

### 4.3 恢复与长期能力

- **Context Snapshot**：恢复任务需要的结构化上下文。
- **Memory**：经过保留策略处理、可在未来任务中取回的信息。
- **Skill**：可重复使用的工作方法或执行能力。
- **Automation**：按时间或条件再次触发 Task 的调度定义。

---

## 5. 能力架构

Delta 使用七层逻辑职责模型。它们不是仓库顶层目录模板。

### 5.1 Workspace Layer

负责：

- Workspace；
- Task；
- Conversation；
- Artifact；
- Workspace Trust。

### 5.2 Orchestration Layer

负责：

- Goal 理解；
- Context 构建；
- Plan；
- 执行推进；
- Approval 衔接；
- Validation；
- Completion / Resume。

短期重点是统一现有执行路径，不新增第二套 Runtime。

### 5.3 Learning Layer

负责：

- Preference；
- Memory；
- Experience；
- Reflection；
- Skill。

短中期以人工控制为主，自动 Reflection、Failure Memory、Skill Evaluation 等延后。

### 5.4 Source Layer

负责：

- 本地资料导入；
- PDF / 文档 / 表格解析；
- Citation；
- 必要的分块与检索；
- 来源失效检测。

原则：

> **先 Citation，再 Retrieval。**

### 5.5 Context Layer

负责有限模型上下文的组织，包括：

- Working Context；
- Source Context；
- Recovery Context；
- Long-term Context。

上下文压缩不能丢失来源、审批、风险、未完成事项和已经发生的副作用。

### 5.6 Execution Layer

负责真正执行工作，包括：

- OpenAI-compatible / Anthropic-compatible 模型调用；
- Tool selection；
- File operations；
- Python / PowerShell / Shell 脚本；
- Search / Web；
- Connector；
- MCP；
- Skill；
- Validation。

所有执行能力服从：

```text
Workspace Boundary
+ Policy
+ Approval
+ Run State
+ Ledger
```

### 5.7 Storage Layer

负责：

- SQLite；
- Local files；
- Artifact store；
- Secret store；
- Run ledger；
- Memory store；
- Source index；
- Automation state；
- Recovery state。

Storage 的第一目标是状态一致、可恢复、可追踪和可迁移。

---

## 6. 脚本能力边界

Scripting 是 Delta 的一级执行能力，但不是独立产品方向。

典型路径：

```text
Task
  ↓
Plan
  ↓
Need deterministic/local processing?
  ├── No  → use normal tools
  └── Yes → write reviewable script
              ↓
           Policy / Approval
              ↓
           Execute
              ↓
           Validate output
              ↓
           register Artifact / Ledger
```

脚本应遵循：

- 优先写入文件后执行，避免不可审查的大段内联命令；
- 明确输入、输出和工作区边界；
- 设定 Timeout / Cancellation；
- 高后果操作必须升级风险或重新审批；
- 产物纳入 Artifact；
- 运行事实进入 Ledger；
- 能够验证时必须 Validation；
- 不把脚本执行器扩展成 IDE 或软件工程 Runtime。

---

## 7. 模型协议边界

Delta 只维护两条协议路径：

### 7.1 OpenAI-compatible

覆盖 Delta 实际需要的 OpenAI 兼容调用路径。

### 7.2 Anthropic-compatible

覆盖 Delta 实际需要的 Anthropic 兼容调用路径。

协议支持以完成知识工作所需的能力为准，不以追齐所有厂商私有 API 为目标。

以下能力永久外置：

```text
provider aggregation
provider rotation
weighted routing
cost routing
regional routing
cross-provider fallback
model marketplace
```

Delta 可以连接一个本身已经完成这些工作的兼容 Endpoint，但不会在内部复制一套 AI Gateway。

---

## 8. 当前能力策略

短中期原则是**收敛已有能力，不横向扩张**。

| 能力 | 策略 |
| --- | --- |
| Task / Run | 收敛为唯一执行语义 |
| Approval / Policy | 加固，不新建第二套权限系统 |
| Artifact | 与 Run、Validation、Ledger 建立明确关系 |
| Durable Resume | 加固恢复与副作用幂等 |
| Automation | 复用普通 Task / Run Runtime |
| Source / PDF / Office | 核心投入方向 |
| Data Analysis | 核心投入方向 |
| Scripting | 保留并加固，服务知识工作 |
| Citation / Validation | 核心投入方向 |
| Memory / Skill | 保留，短中期冻结自动扩展 |
| MCP / Connector | 按真实高频价值维护，不追求数量 |
| Multi-Agent / Subagent | 不以数量为目标 |
| Model Protocol | 仅 OpenAI-compatible / Anthropic-compatible |
| Coding Agent | 非产品方向，逐步缩小 |

---

## 9. 演进路线

### 9.1 短期：可靠

目标：让 Delta 真正可靠完成一件真实知识工作。

优先验证：

- 正常任务能够形成 Artifact；
- Approval 后可以继续；
- 运行中断后能够 Resume；
- 已产生副作用的动作不会重复；
- 脚本失败可以分类、修正和重试；
- Artifact 不完整时不会误报成功；
- Provider / Endpoint 暂时失败可分类、可重试、可追踪；
- Validation 未通过时 Task 不进入成功状态。

Reference Task 优先选择真实办公 / 数据任务，例如：

> 读取本地 XLSX / CSV，完成清洗与分析，生成图表和可交付报告。

### 9.2 中期：实用

目标：稳定承担个人日常反复出现的一类工作。

重点：

- PDF、DOCX、XLSX、Markdown / TXT 等本地资料处理；
- 可定位 Citation；
- 数据分析与图表；
- 文档和内容生产；
- Automation 与普通任务共享同一 Runtime；
- 高频 Connector / MCP 的可靠接入；
- 任务执行中的实时用户修正和继续推进。

### 9.3 长期：智能

只有可靠和实用成立后，再评估：

- Reflection；
- Experience；
- Failure Memory；
- Skill Evaluation；
- Source Conflict / Freshness；
- 更完整的长期 Context；
- 条件型 Automation；
- 确有价值的多 Agent 协作。

长期行为仍必须可解释、可接受、可撤销。

---

## 10. 明确不做

无论阶段如何演进，以下方向不作为 Delta 主线：

- 云端账户依赖型核心 Runtime；
- 模型市场与模型聚合平台；
- 第三类原生模型协议；
- Coding Agent / Coding IDE；
- Git / PR / Code Review 产品；
- 企业协作平台和多租户 SaaS；
- 为展示“自主性”而堆叠 Agent；
- 允许 Automation 绕过权限；
- 为兼容历史实现长期保留错误架构；
- 为追随某个外部项目恢复其目录或产品设计。

不设以下数量型 KPI：

- 支持多少 Provider；
- 支持多少模型；
- 支持多少 Tool；
- 支持多少 Connector；
- 支持多少 MCP Server；
- 支持多少 Agent。

这些数字不能证明 Delta 是否真正有用。

---

## 11. 工程原则

### 11.1 一个领域，一个 Authority

同一领域只能有一个事实和决策权威，避免双写、双判断和兼容层长期共存。

### 11.2 不制造第二套 Runtime

Automation、Persona、Skill、Subagent、Resume 和 Scripting 必须复用统一执行基础。

### 11.3 不制造第二套权限系统

所有高后果能力进入统一 Policy / Approval。

### 11.4 不制造第二套历史系统

任务运行事实进入统一 Run / Ledger 语义。

### 11.5 不用 Prompt 代替工程约束

能够通过类型、Policy、状态机、Validation、Schema 和测试保证的事情，不只依赖 Prompt。

### 11.6 明确失败

错误必须可分类、可记录、可追踪，并能够恢复或明确终止。

### 11.7 复用优先于新增

已有能力优先收敛、加固和验收。只有现有实现无法满足真实 DoD 时才新增模块。

### 11.8 历史实现不约束未来方向

当现有能力与产品边界冲突时，应评估后保留、收敛、替换或删除，不为了兼容历史而制造长期复杂度。

---

## 12. 蓝图维护

本文只记录长期有效的产品原则、能力边界和阶段目标。

维护规则：

- 具体开发任务进入 Issue / Milestone；
- 当前代码结构变化更新 `docs/architecture/`；
- 稳定治理规则进入 `docs/governance/`；
- 运维和部署说明进入 `docs/operations/`；
- 外部项目只作为普通第三方参考，不单独建立“上游同步”产品治理；
- 实现状态只记录已经完成的事实，不提前宣布进行中的迁移完成；
- 历史 ADR 和 CHANGELOG 保留当时真实决策，不为当前品牌清理重写历史。

---

## 13. 最终目标

Delta 最终要解决的不是：

> “如何让 AI 回答更多问题？”

也不是：

> “如何让 AI 写更多代码？”

而是：

> **如何让 AI 在用户控制之下，可靠、持续、可恢复、可验证地完成真实个人知识工作。**

如果一项新能力不能明显改善：

- 完成质量；
- 可靠性；
- 可控性；
- 可验证性；
- 可复盘性；
- 办公 / 数据 / 研究 / 内容工作的日常价值；

它就不应因为“其他 AI 产品都有”而成为 Delta 的优先事项。
