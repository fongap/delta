# Delta 蓝图

> Delta 的产品与架构蓝图。
>
> 本文定义 Delta 要解决的问题、核心原则、能力边界和分阶段演进路径。
> 代码结构以 `docs/architecture/repository-layout.md` 为准，本文不替代该文档；上游同步与能力吸收以 `UPSTREAM.md` 为准。

---

## 1. 定位

Delta 是一个面向个人工作的本地优先 AI 工作系统。

它不是单纯的聊天客户端，也不是模型聚合器。

Delta 的目标是让 AI 能够围绕真实工作持续完成：

**理解 → 规划 → 执行 → 审批 → 验证 → 交付 → 复盘 → 改进**

用户给出目标后，Delta 理解本地资料和工作区，组织任务步骤，调用模型、文件、工具与外部能力，在明确的权限边界内推进工作，并最终形成可检查、可追溯的实际成果。

Delta 关注的不是“回答了一句话”，而是：

> **事情是否真正完成，以及完成过程是否可信。**

作为个人项目，Delta 的第一目标不是覆盖尽可能多的能力，而是先把“一件真实、多步骤的办公任务，能够被可靠完成，并且用户敢于让它跑完”这件事做到位，再逐步扩展。

Delta 的演进原则可以概括为：

> **短期做可靠，中期做实用，长期做智能。**

---

## 2. 产品原则

### 2.1 本地优先

Delta 的核心运行时和工作状态以本地为中心。

工作区、对话、任务与运行状态、模型配置、密钥、审批记录、来源索引、Memory、执行记录、交付物、自动化状态默认保存在本地。

连接远程模型、网页、Connector、MCP 或其他网络服务属于显式外部能力，不改变本地核心的数据所有权。

> **网络是能力，不能成为 Delta 核心运行的前置依赖。**

### 2.2 结果优先

Delta 的基本工作单位不是一轮对话，而是一个可以完成的 Task。

一次任务应尽可能形成明确结果，例如文档、表格、文件修改、分析报告、结构化数据、执行结果或可恢复的后续任务。

复杂任务不能因为单次上下文结束而自然消失。

### 2.3 人掌握后果

模型可以提出计划、调用能力和持续推进任务，但不能天然获得所有权限。

涉及明显后果的操作，例如：

- 覆盖或删除文件；
- 执行高风险命令；
- 修改外部系统数据；
- 发送外部消息；
- 使用敏感凭据；
- 访问超出工作区授权范围的资源；
- 执行不可逆操作；

必须经过统一的 Policy 与 Approval。

系统应明确区分：

**Allow / Confirm / Deny**

权限判断必须由系统约束，而不是依赖模型“自觉”。

### 2.4 可验证

“模型认为完成”不等于任务完成。

任务完成应尽可能由确定性结果证明，例如：

- 文件确实存在；
- 文件格式有效；
- 指定内容已写入；
- 命令退出码正确；
- 数据结构符合约定；
- 来源引用能够定位；
- Artifact 通过预定义验收条件。

无法自动验证的结果，应明确标记验证边界，而不是伪装成确定结果。

### 2.5 可恢复

长任务可以暂停、等待审批、因异常中断、关闭应用后继续。

恢复任务时，应依赖持久化 Run 状态、运行事件和必要的恢复上下文，而不是要求重新播放完整聊天历史。

恢复机制不仅要解决“重新打开还能继续”，还必须避免：

- 重复执行已经产生副作用的动作；
- 将半完成步骤误判为成功；
- 丢失等待中的审批；
- 丢失 Artifact 状态；
- 丢失风险和未完成事项。

### 2.6 可复盘

Delta 应能够回答：

- 用户最初要求什么；
- AI 制定了什么计划；
- 使用了哪些来源；
- 调用了什么模型和工具；
- 修改了哪些文件；
- 哪些动作获得了审批；
- 哪一步失败；
- 如何恢复；
- 最终交付了什么；
- 结果如何验证。

重要行为必须进入统一、可追踪的运行记录。

### 2.7 可改进但不可失控

Delta 可以从历史任务中积累经验，但不得让模型未经控制直接修改长期行为。

长期能力的变化应满足：

**候选 → 证据 → 评估 → 接受 → 使用 → 可撤销**

短中期阶段，Preference、Skill 和长期行为以用户显式维护为主，不自动从一次 Run 直接提升为长期规则。

只有在积累足够真实运行数据之后，才评估自动 Reflection、Skill Evaluation 和 Failure Memory 的必要性。

### 2.8 模型中立

模型是执行资源，不是 Delta 的产品边界。

用户可以自主选择模型、Provider、API 地址与密钥。不同模型能力通过统一能力描述进入 Runtime。

模型中立不等于追求 Provider 数量。

短期不以增加 Provider 为目标，而是优先收敛并跑稳主要协议和调用路径；其他 Provider 按实际使用价值维护。

---

## 3. 核心对象

Delta 围绕一组稳定的领域对象收敛。

这些对象描述产品语义，不要求与代码目录一一对应。

### 3.1 基础闭环

构成“任务能够真正跑完”的最小集合：

- **Task**：用户希望完成的一件事情，描述目标和预期结果，不等同于一段聊天。
- **Run**：Task 的一次实际执行，保存运行状态、步骤、工具调用、审批、验证和结果。一个 Task 可以产生多个 Run。
- **Approval**：需要用户决策的执行边界，必须绑定具体动作和作用范围。
- **Artifact**：任务形成的正式成果，包括文件、报告、导出结果及其他可以独立查看和交付的结果。

### 3.2 可信支撑

用于保证运行过程可靠、可控和可复盘：

- **Workspace**：用户工作的边界，包含文件、资料、任务和相关配置，并决定 Agent 可以访问的默认范围。
- **Plan**：完成 Task 的结构化方案，包含目标、步骤、所需资源、来源要求、风险点、审批点和验收条件。
- **Validation**：对步骤、状态或 Artifact 的确定性验证结果。
- **Audit Event**：任务运行过程中重要状态变化和高后果操作的明确记录。

### 3.3 资料能力

用于建立“结论来自哪里”的证据关系：

- **Source**：任务使用的事实来源，可以来自本地文件，也可以来自用户明确启用的外部来源。
- **Citation**：结论与 Source 中具体证据位置之间的关系。

### 3.4 长期演进

用于支撑恢复、自动化和长期能力积累：

- **Context Snapshot**：任务在特定阶段恢复所需的结构化上下文。
- **Memory**：经过保留策略处理、可在未来任务中按需取回的信息。
- **Skill**：可以重复使用的工作方法或执行能力。
- **Automation**：按照时间或条件再次触发 Task 的本地调度定义。

这些对象的完整形态不要求一次性实现。

已有能力优先复用和收敛；只有现有实现无法满足阶段验收条件时才新增模块。

---

## 4. 能力架构

Delta 的产品能力采用七层模型。

这些 Layer 是**逻辑职责层**，不是仓库顶层目录。

不得为了匹配本文创建：

`workspace/`、`orchestration/`、`learning/`、`source/`、`context/`、`execution/`、`storage/`

等新的顶层目录。

实际代码结构始终遵循 `docs/architecture/repository-layout.md`。

### 4.1 Workspace Layer

负责用户与工作世界之间的边界，包括：

- Workspace；
- Task；
- Conversation；
- Artifact；
- Workspace Trust。

Workspace 不只是目录路径，而是任务运行时的授权和上下文边界。

短中期优先保证单一工作区体验可靠，不以多 Workspace 和复杂 Workspace Policy 为主要目标。

### 4.2 Orchestration Layer

负责把用户目标转化为能够持续推进的执行过程：

```text
Goal
  ↓
Context Build
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
Completion
  ↓
Reflection
```

短期重点不是新增更多编排模块，而是让现有执行路径真正收敛成统一闭环。

Plan Critic 暂不要求独立模块。

短中期可通过规划阶段的结构化自检完成基本检查，例如：

- 是否遗漏必要步骤；
- 是否缺少来源；
- 是否存在明显风险；
- 是否识别审批点；
- 是否定义可验证结果。

只有真实运行证明独立 Plan Critic 有明显价值时，才拆分为专门环节。

### 4.3 Learning Layer

负责 Delta 的受控长期改进，包括：

- Preference；
- Memory；
- Experience；
- Failure Memory；
- Reflection；
- Skill Registry；
- Skill Evaluation。

事实、偏好、经验、技能和失败模式应保持语义区分，不塞入一个无边界的统一 Memory。

短中期以用户显式维护 Preference、Skill 和必要 Memory 为主。

现有 Memory / Skill 能力可以保留，但不继续扩展自动学习链路。

自动 Reflection、候选提升、Skill Evaluator 和 Failure Memory 进入长期阶段。

### 4.4 Source Layer

负责让任务建立在可追溯资料之上，包括：

- Source 导入；
- 文档解析；
- Citation；
- 分块；
- 索引；
- 检索；
- 来源冲突检测；
- 来源时效管理。

目标不是建设通用知识库，而是解决：

> **这个结论依据什么？**

短中期遵循：

> **先解决 Citation，再解决 Retrieval。**

优先支持常见本地资料格式，并让关键结论能够定位到具体来源文件和位置。

语义分块、向量检索、多来源冲突检测和来源时效管理均在确认真实需求后再扩展。

### 4.5 Context Layer

负责有限模型上下文的组织，而不是无限追加聊天记录。

长期模型包括：

- Working Context；
- Source Context；
- Recovery Context；
- Long-term Context；
- Archive Context。

上下文处理必须保证：

- 来源证据不被摘要替代；
- 审批状态不丢失；
- 未完成事项不丢失；
- 风险不因压缩消失；
- 已产生的副作用不会因恢复而被遗忘。

短期重点是 Working Context 与最小 Recovery Context，只保存恢复任务所必需的结构化状态。

Long-term Context 和 Archive Context 的高级检索与压缩策略放到长期阶段。

### 4.6 Execution Layer

负责真正执行工作，包括：

- Provider 调用；
- Model routing；
- Tool selection；
- File operations；
- Shell；
- Search；
- Web；
- Connector；
- MCP；
- Skill；
- Validation。

所有执行能力必须服从：

**Workspace Boundary + Policy + Approval + Run State**

模型不能绕开这些边界直接执行高后果操作。

Delta 已有的 MCP、Subagent、Connector、Automation 等执行能力在短期原则上保持和收敛，不继续横向扩展。

短期重点是把最常用的文件、Shell、模型调用和验证路径做可靠。

### 4.7 Storage Layer

负责本地状态的可靠持久化，包括：

- SQLite；
- Local files；
- Artifact store；
- Secret store；
- Run ledger；
- Memory store；
- Source index；
- Automation state；
- Recovery state。

Storage 的第一目标不是形成复杂数据平台，而是保证：

- 状态一致；
- 能恢复；
- 可追踪；
- 可迁移；
- 不因模块重复而产生多个事实来源。

复杂 Migration 框架只有在现有 schema 演进方式实际成为维护负担后再引入。

---

## 5. 权限与审批

Delta 使用统一 Policy，而不是由各模块自行弹窗确认。

基本流程：

```text
Action
  ↓
Risk Evaluation
  ↓
Policy: Allow / Confirm / Deny
  ↓
Confirm
  ↓
Approval
  ↓
Execute
  ↓
Audit
```

风险评估至少应覆盖：

- 写入、覆盖和删除文件；
- 执行程序或 Shell 命令；
- 访问 Workspace 边界外资源；
- 联网；
- 修改外部系统；
- 发送外部消息；
- 使用敏感凭据；
- 不可逆操作。

### 5.1 Standing Approval

Delta 已有 Standing Approval 基础能力。

短期不继续扩大 Standing Approval 的功能范围，而是优先验证：

- 作用域是否明确；
- 是否能够查看；
- 是否能够撤销；
- 是否有正确生命周期；
- 是否能够避免权限自然扩大；
- 高风险行为是否仍然受到限制；
- Automation / Resume 场景是否沿用同一权限模型；
- 任何路径是否可能绕过 Policy。

原则是：

> **已有能力先加固，不为了减少一次点击而扩大长期权限。**

---

## 6. 当前能力状态

蓝图中的路线图不默认意味着“新增模块”。

所有能力按以下状态理解：

- **已有**：已有实现，当前只需维护。
- **收敛**：已有实现，但需要统一语义、运行路径或可靠性。
- **新增**：现有能力不足以满足阶段验收条件，需要新增。
- **延后**：方向保留，但当前不投入。
- **冻结扩展**：保留已有能力，只修问题，不主动增加范围。

当前阶段重点如下：

| 能力 | 状态 | 当前重点 |
| --- | --- | --- |
| Task / Run | 收敛 | 统一任务与运行语义，避免多个执行事实来源 |
| Approval | 已有 / 收敛 | 统一 Policy 与高后果行为边界 |
| Standing Approval | 已有 / 冻结扩展 | 验证作用域、撤销和不可越权 |
| Artifact | 已有 / 收敛 | 与 Validation、Run、Audit 建立明确关系 |
| Audit / Run Ledger | 已有 / 收敛 | 成为执行事实和复盘基础 |
| Durable Resume | 已有 / 加固 | 处理异常、副作用和恢复一致性 |
| Automation | 已有 / 收敛 | 复用统一 Task / Run Runtime |
| Inbox | 已有 / 收敛 | 保持为待处理事项入口，不扩张成消息中心 |
| Self Wake | 已有 / 冻结扩展 | 只保留必要场景并复用统一执行路径 |
| Source / PDF | 基础已有 / 收敛 | 优先建立可定位 Citation |
| Validation | 需要加强 | 建立确定性完成条件 |
| Memory | 已有 / 冻结扩展 | 保留人工控制，不推进自动学习 |
| Skill | 已有 / 冻结扩展 | 保留显式维护，不自动提升 |
| MCP | 已有 / 冻结扩展 | 优先稳定现有能力 |
| Subagent | 已有 / 冻结扩展 | 不以 Agent 数量为目标 |
| Provider | 已有 / 维护 | 收敛主要协议路径，不追求数量 |
| Plan Critic | 延后 | 先使用结构化规划自检 |
| 自动 Reflection / Skill Evaluation | 延后 | 等待真实运行数据 |
| Source Conflict / Freshness | 延后 | 先做好 Citation |
| 条件型 Automation | 延后 | 先收敛已有 Automation |

---

## 7. 演进路线

### 7.1 短期：可靠

**目标：让现有 Delta 真正可靠完成一件真实工作。**

短期不以增加能力数量为目标。

核心工作是把已有能力收敛成统一、可验证、可恢复的工作闭环：

```text
Task
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
Completion
```

#### 主要范围

- 收敛 Task / Run 执行语义；
- 明确唯一的运行状态和事件事实来源；
- 复用现有 Approval、Standing Approval、Ledger、Inbox 和 Durable Resume；
- 不新增第二套 Runtime、权限系统或历史记录系统；
- 加强文件、Shell 和主要模型调用路径；
- 建立统一 Validation 语义；
- Artifact 必须与 Run、Validation 和 Audit 建立明确关系；
- 加固 Retry、Backoff、Timeout 和错误分类；
- 加固 Durable Resume；
- 检查副作用幂等和重复执行风险；
- 收敛 Automation 到同一 Task / Run Runtime；
- Provider 进入可靠性维护模式，不追求继续增加数量。

#### 重点异常场景

短期必须验证：

```text
正常执行
  → 能完成

等待 Approval 后退出
  → 重启后能继续

执行中崩溃
  → 能识别中断位置

Tool 已产生副作用但 Run 未结束
  → 恢复后不重复执行

Artifact 写入不完整
  → 不误报完成

Provider 超时或暂时失败
  → 错误可分类、可重试、可追踪

Validation 未通过
  → Task 不得进入成功状态
```

#### 短期验收

至少选择一个真实、可重复的办公任务作为 Reference Task，例如：

> 读取本地 CSV / XLSX 数据，完成分析并生成 Markdown 报告。

验收要求：

- 从用户目标到 Artifact 全流程可运行；
- 至少一次高后果动作经过正确 Approval；
- Artifact 真实存在且格式有效；
- Validation 能判断结果是否满足要求；
- Run 的关键执行、审批、失败和结果可以回放；
- 人为中断并重启后能够继续；
- 已产生的高后果副作用不会重复执行；
- 自动化触发与手动触发遵循相同权限和运行规则。

短期 DoD 未满足前，不主动扩大 Agent、Provider、MCP、Connector、Memory、Skill、Inbox 等能力范围。

---

### 7.2 中期：实用

**目标：让 Delta 能可靠覆盖个人日常反复出现的一类真实工作。**

中期重点从“系统本身能不能可靠跑”转向：

> **它是否真的能够稳定参与日常办公。**

#### 主要范围

##### Source 与 Citation

优先完善本地资料读取和证据定位：

- PDF；
- Markdown / TXT；
- DOCX；
- XLSX；
- 其他格式按真实需求增加。

Citation 首先做到：

- 来源文件明确；
- 页码、段落、单元格、行号或其他可定位位置明确；
- Artifact 中的重要结论可以回到 Source。

中期不以建设向量数据库为目标。

只有当常规定位和检索已经明显成为瓶颈时，再引入更复杂的分块和 Retrieval。

##### Automation 收敛

已有 Automation 必须复用普通任务的统一执行路径：

```text
Trigger
  ↓
Task
  ↓
Run
  ↓
Policy / Approval
  ↓
Execution
  ↓
Validation
  ↓
Artifact
  ↓
Ledger
```

Automation 不允许形成第二套执行模型。

无人值守任务：

- 不能绕过 Approval；
- 不能放宽 Workspace Boundary；
- 不能拥有额外隐藏权限；
- 失败后必须形成可追踪状态；
- 等待用户处理时进入统一待处理入口。

##### Inbox 收敛

Inbox 保持为统一待处理事项入口，重点承载：

- Approval；
- 用户问题；
- Resume；
- Automation 异常；
- 需要继续处理的任务。

中期不把 Inbox 扩张为聊天消息中心、通知平台或独立工作流产品。

##### Preference / Skill

保留用户显式维护机制。

Delta 可以在规划和执行时读取用户认可的 Preference / Skill，但不自动将单次经验升级为长期规则。

##### Connector / MCP

只增加真正高频、有明确个人工作价值的能力。

不以“支持数量”为指标。

#### 中期验收

至少满足：

- 一份真实资料型任务能够产生带可定位 Citation 的交付结果；
- 多种常用文件格式能够进入统一 Source 处理路径；
- 一个定时任务能够连续多次形成独立 Run；
- Automation 触发的高风险动作仍然正确进入 Approval；
- 中断、失败和人工等待均能进入现有 Resume / Inbox 路径；
- 手动任务和自动化任务共享同一 Ledger、Validation 和 Artifact 语义；
- 日常至少存在一类重复工作可以由 Delta 稳定承担，而不是只能演示。

---

### 7.3 长期：智能

**目标：在可靠和实用已经成立后，让 Delta 根据真实工作经验持续改善自己的工作方式。**

长期阶段不设固定完成时间，按真实使用价值决定优先级。

#### 受控学习

从真实 Run 中逐步建立：

- Reflection；
- Experience；
- Failure Memory；
- Preference Candidate；
- Skill Candidate；
- Skill Evaluation。

长期行为变化必须遵循：

```text
Candidate
  ↓
Evidence
  ↓
Evaluation
  ↓
User Acceptance / Policy
  ↓
Promotion
  ↓
Use
  ↓
Revocation
```

不允许模型因为一次任务成功就自动改变长期行为。

#### Source 完整能力

在 Citation 已经可靠之后，再考虑：

- 大文档分块；
- 语义检索；
- 混合检索；
- Source Index；
- 来源冲突检测；
- 来源时效判断；
- 文件变化后的索引失效；
- Citation 有效性检查；
- 图片、音频等更多资料格式。

#### Context 完整能力

逐步完善：

- Working Context；
- Source Context；
- Recovery Context；
- Long-term Context；
- Archive Context。

重点解决：

- 长任务上下文持续压缩；
- 历史经验按需检索；
- 来源证据保留；
- 审批状态保留；
- Side-effect Recovery；
- 未完成事项与风险在压缩后仍可恢复。

#### Automation 完整能力

Trigger 可以从定时进一步扩展到：

- 条件变化；
- 外部事件；
- Connector 事件；
- Inbox 驱动；
- 其他可验证条件。

无论 Trigger 来自哪里，都必须复用普通 Task / Run Runtime。

#### Plan Critic

只有当真实任务证明规划错误已经成为明显失败来源时，再引入独立 Plan Critic。

其职责包括：

- 检查步骤是否完整；
- 检查来源是否充分；
- 检查风险是否识别；
- 检查 Approval 点是否正确；
- 检查验收条件是否明确；
- 检查是否存在明显更简单的方案。

#### Multi-Agent / Subagent

只有任务确实存在：

- 可并行子任务；
- 独立审查需求；
- 明确角色隔离；
- 大规模上下文隔离；

时才扩大多 Agent 能力。

> **Agent 数量不是能力指标。**

不为了展示“自主性”增加 Agent。

---

## 8. 明确不做

无论阶段如何演进，以下方向不作为 Delta 的主线。

### 8.1 不做云端产品

不建设依赖官方云端账户才能使用的核心 Runtime。

云端模型、网页和外部服务可以作为可选能力，但不能成为本地核心运行的强制依赖。

### 8.2 不做模型市场

不经营模型购买、分销、复杂模型目录和商业化模型聚合。

Provider 是基础设施，不是产品本身。

### 8.3 不做企业协作平台

不以：

- 团队空间；
- 组织管理；
- 企业 RBAC；
- 企业知识库；
- 多租户；
- 企业审批流；

作为 Delta 的核心方向。

### 8.4 不做纯 Coding IDE

Delta 可以完成 Coding Task，但定位是个人 AI 工作系统，不是另一个 IDE。

### 8.5 不追求自主 Agent 表演

不以：

- Agent 数量；
- 超长“自主思考”时间；
- 复杂多 Agent 图；
- 自动生成大量角色；

作为产品指标。

### 8.6 不让 Automation 绕过用户

无人值守任务仍受：

- Workspace Boundary；
- Policy；
- Approval；
- Validation；
- Audit；

约束。

### 8.7 不为兼容旧结构牺牲架构

不恢复已经淘汰的目录，不长期保留仅为旧结构服务的 Compatibility Layer。

上游能力应评估、吸收和适配，而不是机械复制目录和产品设计。

### 8.8 短中期冻结扩展

以下已有能力短中期原则上保留，但不主动扩展范围：

- Standing Approval；
- MCP；
- Subagent；
- Memory；
- Skill；
- Inbox；
- Self Wake；
- Provider 数量；
- 多 Agent。

### 8.9 延后到长期

以下能力明确延后：

- 独立 Plan Critic；
- 自动 Reflection；
- Skill Evaluator；
- 自动 Failure Memory；
- 自动 Preference Promotion；
- Source 语义检索；
- Source Conflict Detection；
- Source Freshness；
- 高级 Long-term Context；
- Archive Context；
- 条件型 Automation；
- 多 Workspace 精细化权限；
- 复杂 Migration Framework。

---

## 9. 验收标准

Delta 的成功不是支持多少模型、多少 Tool 或多少 Agent，而是实际工作是否可靠完成。

| 维度 | 可检查的验收条件 |
| --- | --- |
| 完成 | 一个真实 Task 能从目标推进到可交付 Artifact |
| 可靠 | 任务中断后能恢复，不重复执行高后果副作用 |
| 可控 | 高后果 Action 始终经过正确 Policy / Approval |
| 可验证 | 成功状态由明确 Validation 支撑，而不是模型自行宣布 |
| 可复盘 | 任意 Run 能还原关键执行、审批、失败、恢复和结果 |
| 可证据 | 资料型任务的重要结论可定位到具体 Source |
| 可持续 | Automation 与手动任务共享相同 Runtime 和安全边界 |
| 可改进 | 长期行为变化有证据、评估、接受和撤销机制 |

不设以下数量型 KPI：

- 支持多少 Provider；
- 支持多少模型；
- 支持多少 Tool；
- 支持多少 Connector；
- 支持多少 MCP Server；
- 支持多少 Agent。

这些数字不能证明 Delta 是否真正可靠和有用。

---

## 10. 工程原则

### 10.1 单一职责

一个模块解决一个明确问题。

避免同时承担 Runtime、UI、Storage、Policy 等多个职责。

### 10.2 领域先于目录

先判断能力属于什么职责，再决定放置位置。

本文中的七层能力模型不是仓库目录模板。

### 10.3 复用优先于新增

路线图中的里程碑默认不意味着新增模块。

已有能力应优先通过：

- 复用；
- 收敛；
- 加固；
- 验收；

达到目标。

只有现有实现无法满足 DoD 时才新增代码。

### 10.4 不制造第二套 Runtime

Automation、Persona、Skill、Subagent、Resume 等能力复用同一执行基础。

不允许长期存在多个互不一致的任务执行系统。

### 10.5 不制造第二套权限系统

所有高后果能力进入统一 Policy / Approval。

任何新 Tool、Connector、Automation 或 Agent 都不得拥有绕过入口。

### 10.6 不制造第二套历史系统

运行事实、关键事件、审批、失败和结果应进入统一 Run / Ledger 语义。

不得由不同模块各自维护互不一致的“历史”。

### 10.7 不用 Prompt 代替工程约束

能够通过：

- 类型；
- Policy；
- 状态机；
- Validation；
- Schema；
- 测试；

保证的事情，不仅依赖 Prompt。

### 10.8 明确失败

错误必须：

- 可分类；
- 可记录；
- 可追踪；
- 可恢复或明确终止。

禁止把失败包装为自然语言成功。

### 10.9 先做小、先做实

每个能力先实现满足真实需求的最小形态。

完整能力必须由真实使用证明其必要性，而不是因为“完整架构看起来应该有”。

### 10.10 不以历史实现约束未来方向

已有实现不是永久正确。

当现有能力与产品原则冲突时，应：

1. 明确当前行为；
2. 评估真实使用价值；
3. 决定保留、收敛、替换或删除；
4. 通过迁移和测试完成调整。

不为保留历史结构而制造长期复杂度。

---

## 11. 蓝图维护

本文件描述 Delta 的长期产品边界和阶段性演进方向。

维护时遵循：

- 不记录容易快速过期的周级工期估算；
- 具体开发任务进入 Issue / Milestone；
- 当前代码结构变化更新 `docs/architecture/`；
- 稳定治理规则进入 `docs/governance/`；
- 运维和部署说明进入 `docs/operations/`；
- 上游同步与能力吸收更新 `UPSTREAM.md`；
- 本文只保留具有长期意义的产品原则、能力边界、阶段目标和 DoD。

当实现状态变化时，优先更新“当前能力状态”，不要因为某项能力已经实现就重新设计整份蓝图。

---

## 12. 最终目标

Delta 最终要解决的不是：

> “如何让 AI 回答更多问题？”

而是：

> **如何让 AI 在用户控制之下，可靠、持续、可恢复、可验证地完成真实个人工作。**

如果一项新能力不能明显改善：

- 完成质量；
- 可靠性；
- 可控性；
- 可验证性；
- 可复盘性；
- 个人日常使用价值；

它就不应因为“AI 产品通常都有”而成为 Delta 的优先事项。
