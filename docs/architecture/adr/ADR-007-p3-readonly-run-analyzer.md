# ADR-007 P3 长期「智能」第一刀 — 只读 Run Analyzer

**Status:** Active

## Context

DELTA_BLUEPRINT §7.3 "长期：智能" 把受控学习的治理链写成
`Candidate → Evidence → Evaluation → User Acceptance / Policy → Promotion → Use → Revocation`
（蓝图 §2.7、§7.3）。同时 §133 明确"只有在积累足够真实运行数据之后，才评估
自动 Reflection、Skill Evaluation 和 Failure Memory 的必要性"，§8.9 把自动
Reflection / Skill Evaluator / 自动 Failure Memory / 自动 Preference Promotion
/ 条件型 Automation 全部列入"延后到长期"。

P1（ADR-005）已经把 run 事实全部收进 hash-chained `RunEventLedger`：
`tool.proposed/started/finished/denied`、`approval.requested/granted/denied`、
`artifact.registered/completed`、`validation.started/passed/failed`、
`side_effect.committed/replayed/uncommitted`、`run.started/completed/failed/
interrupted/resumed`。P2（ADR-006）又为每条 run 留下 `SourceRef.cited_ranges`
——"哪个 run 读了哪个 source 的哪一行"的可查询索引。

这些就是 §7.3 "Evidence" 层的全部原始事实。**目前没有 Read API 把它们变成
分析面**：

- 想知道"过去 30 天哪些 automation 失败率上升"得自己写 SQL 扫 `run_events`；
- 想知道"哪些 run 真的去读 PDF/XLSX 了，被 cite 的是哪几页"得 join
  `run_events` + `sources.json`（P2 的 workspace 侧 ledger）；
- 想知道"某条 run 读了多少 source、读失败的 source 多少"得自己解析 ledger
  payload。

也就是说，**Evidence 已经有了，Evidence 的 query 层还没有**。没有 query
层，Evaluation（蓝图 §7.3 第三步）就只能在原始 SQL / 散落脚本里临摹，永远
无法收敛为可被 Reflection / Skill Evaluator 复用的基础。

同时 §8.8 短中期冻结了 Standing Approval / MCP / Subagent / Memory / Skill /
Inbox / Self Wake 的范围扩展——也就是不允许 P3 第一刀顺手扩这些面。§10.6
"ledger 成为 run 叙述的唯一事实来源；AuditStore 变成派生视图"是不可动摇
的：不能为了加分析层而引入第二个事实库。

### D-0 Delta 长期上下文的"项目"边界 = `workspace`

事实库现状（已审计）：

- `SessionRecord.workspace`（`core/sessions.py:13-42`）、`audit_events.workspace`、
  `memories.workspace`、`<workspace>/.delta/sources.json` 都把
  `workspace` 作为一等字段；这是 Delta 当前"项目"的实际锚。
- `core/ledger.py:run_events` 表**没有** `workspace` 列，只在 `payload`
  JSON 里间接携带 `session_id`；`task_runs` / `TaskRun` 同样不携带
  workspace（只在 `scheduled_tasks.data` JSON 里）。
- 全仓**零** `project_id` / `team_id` / `org_id` / `tenant` 概念；"项目"
  概念要从 `workspace` 之上才能生长。

也就是说，本 ADR 的"项目 = workspace"在当前数据模型下**不需要新加列**，
不需要新加概念。Analyzer 第一刀的所有 query 都以 `workspace` 为边界；
后续如果出现"同一项目跨多个 workspace"或"用户级跨项目聚合"的真实需求，
是独立 ADR 的事（见 D-4 后续）。

## Decision

### D-1 P3 第一刀是 **只读 Run Analyzer**：分析基础设施，零行为变更

- 范围限定在**只读投影**（read-only projection）——只从 `RunEventLedger` +
  `SourceStore` + `TaskRun` + `ConversationStore` 现有事实库读，不写任何
  新的"经验 / 候选 / 偏好"表，不动 Skill / Memory / Preference 任何活跃
  状态。
- 提供一个**进程内只读 API**（`core/analyzer.py` + 子模块），覆盖三类查询
  ——**所有 query 都以 `workspace` 为边界**（per-workspace，不做跨 workspace
  聚合；详见 D-4）：
  - **Run 端到端还原**：`timeline_for_run(run_id, workspace)`：给定
    `run_id` + `workspace`（用于回查 session / 上下文），把 ledger 事件
    按 `seq` 还原为 `TimelineEntry`（proposed / started / finished /
    approved / artifact / validation / side-effect 节点）。
  - **Automation 健康**：`automation_health(task_id, workspace, window=N)`：
    跨 N 个 run 聚合 `TaskRun.status` + `validation.passed/failed` 计数，
    给出"近 K 次运行的失败率 / 平均时长 / 常见失败原因 top-N"——只读，
    不调任何调度。
  - **Source / Citation 命中**：`source_citation_hits(workspace, source_id=...)`：
    把 `SourceRef.cited_ranges` 与 ledger `tool.finished{tool:
    read_file|read_document}` 关联，给出"某 source 被多少 run 读过、
    被 cite 的行号分布、最近一次被读的时间"——这是 §7.3 "Source 完整
    能力"（分块 / 语义检索 / 冲突 / 时效）的最低基础。
- 暴露方式：纯 Python 函数（`from core.analyzer import ...`），**不**通过
  TUI / WebSocket / REST 暴露；UI 集成是后续 PR 的事，本 ADR 不动 UI。
  这保证 D-1 的本本份份："不为了加分析面而扩大 long-term 行为"。
- **API 形状保留可扩展到"项目"维度**：所有 query 第一参数都是
  `workspace: str`（per-workspace 范围），不写成"全局 fetch + 客户端
  过滤"。这样如果未来需要 "项目"维度的跨 workspace 聚合，只需要引入
  `project_id` 概念 + 在 query 签名上叠加 `project_id=None` 即可，**不
  改语义**。本 ADR 不引入 `project_id`，但不让 API 形状堵死这个方向。

### D-2 严格不自动推广、不触发学习、不动 Skill / Memory

- **不**写 `Experience` / `Failure Memory` / `Reflection` / `Skill Candidate` /
  `Preference Candidate` 任何状态机。这些是 §7.3 受控学习链的产物，必须等
  真实使用数据 + 后续独立 ADR。本 ADR 不为它们铺路，只铺"证据可查"。
- **不**动 `effective_skills` / `MemorySettingsStore.user_rules` /
  `IdempotencyLog` / `AuditStore` / `TaskStore` / `ScheduledTask.last_status`
  任何一个写入路径。
- **不**加新的事件类型；分析层用 `KNOWN_EVENT_TYPES`（`core/ledger.py:33`）
  现有的封闭词汇表，漏给 `tool.<stage>` 兜底的也要复用。
- **不**为 Analyzer 加任何"用户级"或"项目级"的新字段、不动 `run_events`
  / `task_runs` / `sessions` 三张主表的 schema；如果 query 需要 workspace
  这一维数据（`run_events` / `task_runs` 还没有），就**从 `payload` /
  `data` JSON 字段读**，承认"现在不够好"，等 §10.6 路径先扩列再升索引。

### D-3 复用优先于新增 + 不复制事实库

- `RunEventLedger.events(run_id)`（`core/ledger.py`）就是 run 还原的输入，
  Analyzer 不维护自己的事件缓存。
- `SourceStore.list()` + `SourceRef.cited_ranges`（`core/sources.py:97`）
  就是 source / citation 命中的输入，Analyzer 不维护自己的 source 缓存。
- `TaskRun` 行（`core/automation/models.py:226`）就是 automation 健康的输入，
  Analyzer 通过 `AutomationStore.list_runs(task_id, …)` 取数。
- `ConversationStore.list(workspace=...)`（`core/conversations.py:506`）就是
  同 workspace 下跨 session 的入口；Analyzer 不为"跨会话连续性"复制
  自己的 messages 缓存。
- §10.6 "ledger 是单一事实来源" 不可破；如果某天发现事实确实不够，**先**
  在 ADR-005 词汇表里加事件类型 / 在主表加 workspace 列，**后**在
  Analyzer 里消费——不绕开。

### D-4 边界：per-workspace，不做跨 workspace 聚合

- **本 ADR 严格不做跨 workspace 聚合**。Analyzer 的每个 query 都把
  `workspace` 当作必填边界，过滤后才取数；不存在"列出所有 workspace 的
  全局健康"这种 API。
- 原因：当前数据模型里 `workspace` 已经是 Delta 的项目锚，跨 workspace
  聚合需要先有"哪些 workspace 算同一个项目"这个事实，而这个事实
  （`project_id` / `team_id` / 标签…）目前**不存在**。在没有这个事实
  之前做"跨 workspace 汇总"会得到无意义的虚高 / 漏报。
- **可扩展但不实现**：query 签名全部以 `workspace: str` 为第一参数，
  未来如果引入 `project_id` 概念（独立 ADR），只需叠加
  `project_id: str | None = None` 默认参数 + 内部做 lookup，不改
  现有调用方。
- 单 query 内**支持跨 session / 跨 TaskRun**：Automation 健康聚合
  本来就跨 `TaskRun`（一个 `task_id` 多次运行）；`timeline_for_run`
  接受 `run_id` + `workspace` 拿到对应 session 的全部事件，**不要求
  只能围绕单个 conversation**。也就是 D-1 的"项目内跨会话、跨
  TaskRun 连续分析"已经实现——只是范围仍然 = 一个 workspace。

### D-5 守护：Analyzer 自身可测，不靠真实长历史

- 用 `tests/conftest.py` 的 `_isolated_state_dir` + scripted provider 模式
  （同 `tests/test_reference_task.py`、`tests/test_automation_convergence.py`）
  写 3 类测试：
  - `tests/test_run_analyzer_timeline.py`（run 还原 + payload 解析）
  - `tests/test_run_analyzer_automation.py`（多 run 聚合 + 失败率）
  - `tests/test_run_analyzer_source.py`（source citation 命中 + 排序）
- **不**断言"长历史下某指标收敛"——那是 P3 后续 Evaluation 阶段的事，跨 §8.9。
- 任何"未来 Reflection / Skill Evaluator 需要"的可读信号都在这些测试里被
  验证一次，避免重复堆样例。
- 增加 1 个测试文件守住"不变成 single-conversation-only"的形状契约：
  `tests/test_run_analyzer_workspace_boundary.py` —— 断言每个 query 都
  接受 `workspace` 必填参数；构造两个 workspace 的事件，验证 query
  不会跨越。

## Consequences

### 正面

- **为 §7.3 治理链的 Evidence 步提供可复用 query**：未来 Reflection /
  Skill Evaluator / 自动 Failure Memory 不用各自再写 SQL / 散落脚本，可
  以 `from core.analyzer import …` 起步。这是 §7.3 "按真实使用价值决定
  优先级"的最浅落地：先把 query 做好，不动长期行为。
- **项目级边界正确**：所有 query 以 `workspace` 为必填边界，与 Delta
  现有事实库（`SessionRecord.workspace` / `audit_events.workspace` /
  `memories.workspace`）保持一致；同 workspace 内支持跨 session /
  跨 TaskRun 聚合。
- **API 形状为未来 project 维度预留**：`workspace: str` 作为第一参数
  而不是"全局 fetch + 客户端过滤"，未来叠加 `project_id` 不需要改
  现有调用方。
- **零长期行为变更**：不写候选表 / 不动 Skill / Memory / Preference 任何
  活跃状态，符合 §7.3 "不允许模型因为一次任务成功就自动改变长期行为"。
- **复用已有事实库**：Analyzer 不引入第二个事件库 / 第二个 source 库，
  §10.6 单一事实来源不被破。
- **可视化复盘解锁**：UI 可以直接消费 Analyzer 跑出 run 时间线、automation
  健康、source 命中，§9 验收表的"可复盘 / 可证据"立刻变厚（无需新数据）。
- **不增加运行时依赖**（`pypdf` 已是 P2 runtime；Analyzer 本身只 stdlib +
  已有 `core.*` 模块）。

### 负面 / 风险

- **Evidence 投影的语义边界在迭代中可能漂移**：本 ADR 把 run 还原 / automation
  健康 / source 命中三类 query 写死；如果未来需要 "approval 通过率" 或
  "connector 维度聚合"，需要新增 query；本 ADR 不预测这些。
- **不触发学习 = 不能从这次落地获得自动改进行为**：这是 §7.3 治理链的
  强制要求，不是缺陷——一次"成功"不该改长期行为。后续 Reflection / 自动
  Failure Memory 必须由独立 ADR 评估并接受 user 接受（§7.3 第四步）才
  能落到代码。
- **per-workspace 边界本身是当前数据模型的产物**：本 ADR 把
  "Delta 长期上下文 = 项目 = workspace" 视为既定事实，**不**为这个等式
  提供新论证。如果未来出现"同一项目多 workspace"或"用户级跨项目"
  需求，需要独立 ADR 引入 `project_id` 概念并在主表加列；本 ADR 不
  预留这条路径之外的任何聚合。
- **`run_events` / `task_runs` 表没有 `workspace` 列**：Analyzer 暂时
  从 `payload` / `data` JSON 读，承认"现在不够好"。这是 §10.6 路径
  应该后续补的事（先扩列，再在 Analyzer 里消费），本 ADR 不去扩列。

### 后续（不在本 ADR 范围）

- **P3 第二刀候选（独立 ADR 评估）**：
  - 自动 Failure Memory（在 Analyzer 之上加"分类 + 持久化"，仍需
    user-accept）。
  - 人工可触发的 Reflection CLI（让用户用 Analyzer 数据生成
    "这次我为什么失败 / 这次我为什么成功"草稿，仍 user-accept 后才能
    升级）。
  - Skill Evaluator（用 Analyzer 的 run 端到端还原 + automation 健康
    评估每条 skill 的实际效果，仍 user-accept）。
- **"项目"维度的扩展（独立 ADR 评估）**：
  - `run_events` / `task_runs` 主表加 `workspace` / `project_id` 列（§10.6
    单一事实来源路径，先扩列再升 Analyzer）。
  - 引入 `project_id` 概念（用户级 / 团队级），Analyzer 签名叠加
    `project_id: str | None = None`。
  - 跨项目 / 跨 workspace 的 Source conflict detection / freshness 汇总。
- **§7.3 "Source 完整能力"**：大文档分块 / 语义检索 / 冲突 / 时效 / 图片
  音频，是 P3 独立 work stream，不在 Analyzer 第一刀。
- **§7.3 "Context 完整能力"**：Long-term / Archive Context 高级检索与
  压缩，是 P3 独立 work stream。
- **§7.3 "Plan Critic" / "Multi-Agent 扩展"**：蓝图 §8.9 / §7.3 后段明确
  延后。

## 实施引用

- `core/analyzer.py`（新增）— 只读 projection 入口（`timeline_for_run` /
  `automation_health` / `source_citation_hits`），所有 query 第一参数
  `workspace: str`
- `core/ledger.py:33` — `KNOWN_EVENT_TYPES` 封闭词汇表
- `core/ledger.py:69` — `RunEventLedger.events(run_id)` / `verify(run_id)`
  （`workspace` 暂时从 payload JSON 读）
- `core/sources.py:97` — `SourceRef.cited_ranges` + `SourceStore.list()`
  （`SourceStore` 已按 workspace 实例化）
- `core/automation/models.py:226` — `TaskRun`（automation 健康聚合；
  `workspace` 暂时从 `scheduled_tasks.data` JSON 读）
- `core/automation/store.py` — `AutomationStore.list_runs(task_id, …)`
- `core/conversations.py:506` — `ConversationStore.list(workspace=…)`
  （同 workspace 跨 session 入口；D-3 复用，不复制）
- `tests/test_run_analyzer_timeline.py`（新增）
- `tests/test_run_analyzer_automation.py`（新增）
- `tests/test_run_analyzer_source.py`（新增）
- `tests/test_run_analyzer_workspace_boundary.py`（新增）— 守护
  `workspace: str` 必填 + 跨 workspace 不串
- `CHANGELOG.md` — P3 长期第一刀条目
