# Delta 蓝图

> Delta 的长期产品、能力和架构边界。
>
> 本文回答“Delta 最终要成为什么”。当前实现状态以 ADR、`runtime-public-contract.md` 和 CHANGELOG 为准；长期系统模块和语言职责见 `docs/architecture/target-architecture.md`。

---

## 1. 产品定位

Delta 是一个本地优先的个人工作 AI Agent。

一级产品方向固定为：

```text
日常办公 | 研究分析 | 内容创作
```

它不是单纯聊天客户端，也不是模型聚合器，更不是 Coding Agent。

Delta 的目标是把真实工作推进为可检查、可恢复、可验证、可追溯、可继续改进的成果：

```text
Goal
  ↓
Context / Sources
  ↓
Plan / Research Design
  ↓
Execution
  ↓
Policy / Approval
  ↓
Validation
  ↓
Artifact
  ↓
Ledger / Experience
  ↓
Completion / Resume / Learning
```

核心判断不是“模型回答得像不像”，而是：

> **事情是否真正完成，完成过程是否可信，以及有效方法能否被安全复用。**

---

## 2. 三项核心能力

### 2.1 日常办公

范围包括：

- DOCX / XLSX / PPTX / PDF / Markdown 等文档与文件；
- 文档创建、修改、整理、比较和格式转换；
- 表格读取、整理、计算和交付；
- PDF 阅读、提取、比较和处理；
- 邮件、消息、会议材料和资料整理；
- 批量文件处理和轻量自动化；
- 正式办公成果的预览、验证和版本管理。

目标闭环：

```text
读取 → 理解 → 修改 / 创建 → 验证 → 预览 → 交付
```

日常办公不意味着建设完整 Office 套件；Delta 优先完成任务和交付成果，而不是复制 Word、Excel 或 PowerPoint 的全部编辑器能力。

### 2.2 研究分析

“研究分析”取代旧一级名称“数据分析”。

原因是 Delta 的目标已经超出对现有数据做统计，而是覆盖：

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

最低能力范围：

#### 数据准备与探索

- 数据导入、字段和类型检查；
- 完整性检查；
- 缺失值、异常值和重复数据；
- 描述统计；
- 分布、趋势和探索性可视化。

#### 统计推断与建模

- 参数 / 非参数检验；
- t test / ANOVA / 卡方；
- 相关与线性 / 多元回归；
- 模型诊断、残差和假设前提；
- 置信区间、效应量和实际意义解释；
- 时间趋势和分组比较；
- 任务确有需要时扩展更专业模型。

#### 试验设计 DOE

- 研究问题和响应变量定义；
- 因素、水平与范围；
- 样本量 / 功效；
- 随机化与区组；
- 全因子 / 部分析因；
- 筛选设计；
- 响应面；
- 最优设计；
- 主效应、交互作用和模型诊断。

#### 序贯试验设计

序贯设计从第一版研究分析能力模型中就必须存在，不作为后期高级附加项。

至少支持：

- 首轮最小信息设计；
- 中期分析；
- 当前已知 / 未知判断；
- 模型更新；
- 信息缺口识别；
- 下一批试验推荐；
- 因素范围收缩 / 扩展；
- 允许时引入新因素；
- 预先声明的可调整范围；
- 停止规则；
- 最终确认设计。

每一轮都要能够回答：

```text
现在知道了什么？
还有什么不确定？
下一组试验最值得做什么？
什么时候可以停止？
```

系统可以提出建议，但不得在没有明确人类控制的情况下自动改变研究目标、扩大权限或悄悄重写停止标准。

#### 研究报告

最终交付应能够形成：

- 数据说明；
- 方法和设计；
- 图表；
- 统计结果；
- 结果解释；
- 不确定性与局限；
- 结论；
- 后续试验建议；
- 正式 DOCX / PDF / XLSX 等成果。

### 2.3 内容创作

内容创作面向个人和自媒体完整生产链，而不只是“AI 写文章”。

```text
选题
  ↓
资料研究 / 事实核查
  ↓
结构 / 脚本
  ↓
写作 / 编辑
  ↓
图文 / 图片 / 视频资产
  ↓
多平台适配
  ↓
发布准备
  ↓
效果数据
  ↓
复盘 / 下一轮优化
```

能力包括：

#### 选题与研究

- 热点和主题发现；
- 受众判断；
- 标题与系列策划；
- 内容日历；
- 网页和资料研究；
- 来源、事实和引用管理。

#### 文本

- 长文、短文、公众号、博客、社交媒体；
- 口播稿、视频脚本和说明文字；
- 编辑、压缩、扩写和风格统一；
- 一稿多发和平台化改写。

#### 图文与图片

- 图文笔记；
- 信息卡片；
- 长图；
- 封面；
- 信息图；
- 数据图解；
- 图片生成、编辑、裁切和尺寸适配。

#### 视频

- 短视频脚本；
- 分镜；
- 字幕稿；
- 图文转视频；
- 轻量 AI 视频生成 / 处理；
- 图片转视频；
- 素材组织；
- 封面、标题和发布素材包。

Delta 负责工作流、来源、资产、版本和成果管理。专业图片 / 视频模型、编辑器和生成服务可以作为受控 Capability 接入，核心 Runtime 不建设重型媒体编辑器。

#### 发布与复盘

- Markdown / HTML / DOCX 等交付；
- 多平台格式适配；
- 摘要、标签和发布信息；
- 内容效果数据整理；
- 阅读、互动、传播和选题复盘。

---

## 3. 产品边界

### 3.1 不做 Coding Agent

Delta 不以软件开发作为产品方向，不建设：

- IDE；
- 大型代码仓库理解产品；
- Git / Pull Request 产品工作流；
- 代码审查平台；
- 面向软件工程的独立 Agent Runtime。

已有 Coding 专用产品面后续按真实依赖逐步缩小、隔离或移除。

### 3.2 保留 Scripting

为完成三类工作，Delta 可以编写、执行、检查和修正：

- Python；
- PowerShell；
- Shell。

典型用途：

- 统计分析和绘图；
- Excel / CSV / PDF / DOCX 处理；
- Office 自动化；
- 文件批处理；
- 图片和音视频处理；
- 可复用的小型任务脚本。

> **Coding 不做产品，Scripting 保留为一级执行能力。**

### 3.3 模型协议只保留两类

```text
OpenAI-compatible
Anthropic-compatible
```

不因为具体厂商新增第三类原生协议。

Provider 聚合、多 Key、权重、fallback、额度和复杂路由继续外置给兼容 Endpoint / AI Gateway。

### 3.4 一个主 Agent

三个产品域不对应三个永久 Agent。

默认形态是一个 Delta 主 Agent，通过 Skills / Capabilities 组合完成任务。

只有真实使用证据证明独立 Agent 具有不可替代的权限、上下文或生命周期价值时，才考虑增加；不能把“多 Agent 数量”当能力增长指标。

---

## 4. 产品原则

### 4.1 本地优先

核心运行时、工作状态、密钥、审批、Sources、Artifacts、Memory、Skills 和 Learning state 默认保存在本地或用户明确控制的位置。

网络是能力，不是本地工作系统的前置依赖。

### 4.2 成果优先

对话只是交互方式。真实目标是 Deliverable。

典型成果包括：

- 文档 / 表格 / PDF / 演示；
- 数据集和图表；
- DOE / 序贯设计方案；
- 正式研究报告；
- 图文、图片、视频和内容素材；
- 可继续执行的任务状态。

### 4.3 人掌握后果

高后果动作统一通过 Trust / Policy / Approval。

模型、Skill、Worker、Automation 和 Learning 都不能天然获得所有权限。

### 4.4 可验证

“模型认为完成”不等于完成。

尽可能使用确定性事实检查：

- Artifact 存在；
- 格式有效；
- 数据结构正确；
- 必需字段 / 章节存在；
- 统计前提和输出可追踪；
- Citation 可定位；
- 预定义 Validation 通过。

### 4.5 可恢复

长任务可以暂停、等待、取消、重启和继续。

恢复不能重放已经提交的高后果副作用，也不能把 uncertain 动作当作成功。

### 4.6 可复盘

Delta 应能够回答：

- 用户目标是什么；
- 使用了哪些资料；
- 设计和方法是什么；
- 调用了哪些模型、工具和脚本；
- 哪些动作需要审批；
- 哪些数据和试验产生了结论；
- 哪一步失败或被 Steering；
- 最终交付什么；
- 如何验证。

### 4.7 可进化但不可自扩权

允许改善方法，不允许自动扩大权限。

```text
能力可以进化
权限不能自行进化
```

---

## 5. 长期系统模块

Delta 不再使用旧的 Workspace / Orchestration / Learning / Source / Context / Execution / Storage 七层模型作为目标架构。

长期逻辑模块按稳定职责重新划分为：

| 模块 | 长期职责 | 主要语言 |
| --- | --- | --- |
| **Experience** | UI、进度、Steering、Approval、预览、编辑、设置 | TypeScript |
| **Runtime** | Session / Run / Turn、Agent loop、Context、Model、Tool dispatch、Resume | Rust |
| **Trust** | Policy、Risk、Approval、Ledger、Idempotency、Secrets、Sandbox | Rust |
| **Work** | Workspace、Source、Citation、Artifact、Version、Validation、Provenance | Rust |
| **Capability** | Tool / Skill / Worker / Office / Research / Media / MCP / Connector capability | Rust Host + Workers |
| **Automation** | Scheduler、Wake、Inbox、Notifications、Background / inbound trigger | Rust authority |
| **Learning** | Memory、Preference、Experience、Failure Memory、Skill Evaluation / Promotion | Rust authority |

这仍然是七个模块，但数字不是设计目标。以后如果真实职责证明需要合并或拆分，可以通过 ADR 调整。

关键是：

> **按状态所有权、决策权、安全隔离、变化速度和独立演进能力划模块。**

完整定义见 `docs/architecture/target-architecture.md`。

---

## 6. Skill 与 Capability

### 6.1 Capability

Capability 是执行能力。

可能来自：

- Rust Native Tool；
- Python / PowerShell / Shell Worker；
- MCP；
- Connector；
- 外部图片 / 视频 / Office / Research 服务。

Capability 必须服从统一 Run、Trust、Workspace Boundary、Artifact 和 Validation。

### 6.2 Skill

Skill 是主要扩展单位：

```text
Skill
├── metadata / version
├── instructions
├── workflow
├── required capabilities
├── permissions
├── validation
├── templates
└── optional scripts
```

例如：

- 稳定性趋势研究；
- 序贯处方优化；
- Excel 月报；
- 公众号技术文章；
- 五图图文笔记；
- 短视频素材包。

新增重复工作优先考虑 Skill，不优先增加 Runtime、常驻服务或专用 Agent。

---

## 7. Learning 与自我进化

学习闭环：

```text
Run
 ↓
Artifact / Validation
 ↓
用户修改 / 采用 / 拒绝
 ↓
Experience
 ↓
Pattern / Failure Memory
 ↓
Skill Candidate
 ↓
Replay / Evaluation
 ↓
Promote / Reject / Rollback
```

可以学习：

- 用户偏好及其原因；
- 哪种工作方法成功；
- 哪种方法失败；
- 可靠脚本和模板；
- 报告结构；
- 工具选择；
- Skill 版本效果。

不允许 Learning 自动修改：

- Runtime authority；
- Policy / Approval；
- Risk level；
- Secrets access；
- Network / Sandbox boundary；
- Ledger / Validation authority。

Skill 的自动晋级必须有证据、Evaluation 和明确治理；高后果领域默认需要用户确认。

---

## 8. 语言与进程边界

### 8.1 核心产品语言

目标只保留：

```text
Rust + TypeScript
```

TypeScript = Experience。

Rust = Runtime + Trust + Work + Automation + Learning authority + Capability Host。

### 8.2 Python / PowerShell / Shell

这些是任务执行语言，不是 Delta 核心架构 Authority。

Worker 必须满足：

- scoped input；
- scoped output / staging；
- timeout / cancel；
- process supervision；
- least privilege；
- network grant；
- structured result；
- 无核心数据库写权限；
- 无 Session / Run / Approval authority。

### 8.3 模块化单体

核心默认是一个本地 Rust Runtime，不因为七个逻辑模块创建七个进程。

没有真实隔离、崩溃容错或生态要求时，不引入本地微服务。

---

## 9. 工作事实模型

短中期不引入未经真实任务证明的 WorkItem 抽象。

继续围绕稳定对象：

```text
Workspace
  ├── Session
  │    ├── Messages
  │    └── Runs
  ├── Sources / Citations
  └── Artifacts / Versions / Validation
```

基本语义：

- **Session**：工作上下文；
- **Run**：一次可恢复、可审计执行；
- **Source**：工作依据；
- **Citation**：结论与证据位置关系；
- **Artifact**：工作成果；
- **Validation**：成果和执行事实的确定性检查；
- **Ledger**：运行事实。

只有真实使用反复证明 Session 上方需要新的长期工作对象时，再新增抽象。

---

## 10. R5.1 后的收敛方向

R5.1 完成 Runtime correctness 和 Human Control 基础，不等于最终语言 / 进程架构已经完成。

后续 R6 固定目标：

```text
1. 产品减法：删除 Coding 产品面
2. 冻结 Python 控制面新增
3. Rust Runtime Host
4. TypeScript → Rust direct IPC
5. Session / Scheduler / Application Control Plane 迁移
6. Agent Loop 迁移
7. Python Office / Research / Media Worker 化
8. 删除常驻 Python Backend 和重复 Provider / facade
9. Rust + TypeScript hard-cut
```

R6 期间不顺手：

- 新造 WorkItem；
- 建复杂 Multi-Agent；
- 大改 UI 设计系统；
- 新增第三模型协议；
- 扩 Coding 产品；
- 同时把三个产品域全部功能做满。

迁移目标是减少控制面和重复实现，不是机械提高 Rust 行数占比。

---

## 11. 开发过滤器

任何新能力进入主线前必须回答：

1. 它直接改善**日常办公、研究分析或内容创作**中的哪一个真实工作环节？
2. 它属于七个长期模块中的哪一个？
3. 谁拥有状态和最终 Authority？
4. 是否可以作为 Skill / Capability，而不扩张 Runtime？
5. 是否增加了不必要的新进程、新服务、新协议或新 Agent？
6. 是否绕过 Trust / Work / Validation？
7. 能否恢复、审计、验证和回滚？

如果不能明确回答，就不进入产品主线。

---

## 12. 长期成功标准

Delta 的成功不按：

- Agent 数量；
- Provider 数量；
- Skill 数量；
- Rust 百分比；
- 自动化数量；

判断。

真正指标是：

- 三类工作是否能可靠完成；
- 研究分析是否能够从问题和设计走到报告，而不只处理已有数据；
- 用户是否可以实时修正长期任务；
- Artifact 是否真实、可验证、可追溯；
- 高后果动作是否始终由人掌握；
- 系统能否从真实使用中形成更有效的 Skills，同时不自动扩大权限；
- 架构是否长期保持简单、稳定和可维护。

> **长期、稳定、高效、安全、敏捷，是 Delta 所有产品和架构选择的共同过滤器。**
