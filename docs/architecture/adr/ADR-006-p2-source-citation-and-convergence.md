# ADR-006 P2 实用 — Source / Citation 与运行收敛

**Status:** Active

## Context

DELTA_BLUEPRINT §7.1 "Reliable"（ADR-005）交付了统一、可验证、可恢复的运行
时：单一 Task / Run 身份、可回放的 ledger、Artifact sha256、Validation 门控
和副作用安全 resume。Reference Task（`tests/test_reference_task.py`）证实 8/8
短期验收可以由端到端流水线承接。

§7.2 "实用" 把目光从"系统能不能可靠跑"转向"系统能否稳定参与日常办公"。
P2 范围不引入新能力，而把已有基础设施收敛到日常工作真正会触达的几个面：

- **Source / Citation**：本地资料能进入 Source，run 能被定位回具体位置
  （页码、段落、单元格、行号、消息 id）。这是 §7.2 中期验收的第一条。
- **Automation 收敛**：Automation 必须复用普通任务的统一执行路径（ADR-005
  承诺的不变式），不能形成第二套执行模型。
- **Inbox 收敛**：Inbox 是统一的待处理事项入口；P1 relay-mode-removal 已
  把写方向收敛到 `[d:…]`，P2 关闭 parse 方向的 `ow` / `ocw` 兼容。
- **Preference / Skill**：保留用户显式维护，不升级为新能力。

本 ADR 不动 Preference / Skill / Connector / MCP（§7.2 中不要求），仅做
Source / Citation 与运行收敛的 DoD 锁定。

## Decision

### D-1 Source / Citation 提升为可定位的一类证据

- `core/sources.py` 新增 `CitationRange` typed schema：判别字段 `kind`
  ∈ `lines` / `page` / `cells` / `row` / `column` / `sheet` / `message_id` /
  `custom`。每种 kind 仅保留与该定位语义相关的字段进入 canonical form，
  不相关字段在 `to_range_dict` 阶段被剥离。
- `to_range_dict` / `normalize_cited_ranges` 在序列化前完成结构性校验
  （必填字段、字段类型）。校验失败抛 `ValueError` —— 在持久化前拒绝，
  保证 run 永远不会留下 UI 无法渲染的 citation。
- `SourceStore.mark_cited` / `add_citation` 在加锁后落盘前先校验范围；
  校验失败的入参会让 `cited_ranges` 完全不变（半写入保护）。
- `SourceDTO` 新增 `location` 与 `cited_ranges`（additive；`extra="allow"`
  的 `ContractModel` 已允许 UI 端不解析新字段）。`to_dto` 把这两项透出，
  UI 不再只能看到 12 字符指纹前缀 + 文件名。
- `integrations/tools/files.py:file_tools` 接受可选的
  `source_store=` / `run_id=`：成功读入时通过 `core/citation.cite` 钩子
  捕获 `SourceRef` 并落 `lines` 类型的 citation，把"run 实际读了哪些
  行"写入 Source ledger。错误路径（path 越界、文件不存在、读失败）不写
  任何 citation —— 一条没真正读到的内容不能进入审计。
- `integrations/tools/documents.py:read_document` 单工具覆盖 PDF /
  XLSX / DOCX（Markdown / TXT 走 `read_file`）。PDF 用 `pypdf` 抽文本
  （一页一个 block），XLSX 复用 `services/server/sheet_preview.py`
  的 stdlib zipfile 解析（一个 sheet 一个 block），DOCX 同样走 stdlib
  + ElementTree 抽段落（一个 paragraph 一个 block）。每种格式成功
  读入时按各自的 locator 词汇（`page` / `cells` / `message_id`）自动
  cite。摘要视图（`block=None`）只返回 block 列表不写 citation —— 只
  有真正读到的位置才进 source ledger。
- `core/citation.cite` 是 capture + cite 的统一入口，失败（OSError /
  校验错）静默吞掉：`run_id` 解析顺序为 ① 显式参数 → ②
  `core.runscope.current()`（手动 turn 由 adapter 在每轮 set，
  worker thread 通过 `asyncio.to_thread` 的 context copy 看到）。
- 生产路径注入（`core/agent.py` / `core/catalog.py` /
  `services/server/manager.py`）：`build_engine` 接受 `source_store` +
  `run_id`，`AgentContext` 同步带出；`SessionManager.source_store_for(workspace)`
  按 workspace 缓存 `<workspace>/.delta/sources.json`（与
  `run-events.db` / `side-effects.db` 同目录）。手动 session 与
  自动化 run 都自动接线。Code 表面（`code_files` capability）同时挂
  上 `read_file` 与 `read_document`；Cowork / Ops 仍走 aisuite
  多根 `read_file`，它们的 cite 钩入是后续工作。

### D-2 Automation 不允许形成第二套执行模型

§7.2 列出四条不变式："不能绕过 Approval / 不能放宽 Workspace Boundary /
不能拥有额外隐藏权限 / 失败后必须形成可追踪状态"。这些由 ADR-005 已经
满足；P2 把它们从 Reference Task 验收的隐含结论升级为结构化守护测试
（`tests/test_automation_convergence.py`）：

- 自动化与手动 runtime 共用 `_build_task_engine` 工厂 + manager 实例的
  `idem_log` + `audit_sink`（任何第二份 instance 都是回归）。
- 自动化 run 发出的所有事件 `type` 必须落在 `KNOWN_EVENT_TYPES` 内
  （任何新写但未声明的事件类型都是 vocabulary 漂移）。
- 自动化 run 的 `TaskRun.run_id` 必须等于 `run_ledger.runs()` 中唯一
  一项 —— 身份不分裂是 ADR-005 的 G1 承诺，结构化测试守住。

Reference Task（`tests/test_reference_task.py`）继续做端到端断言；
`test_automation_convergence.py` 是便宜的 wiring 守门员。

### D-3 Inbox 写方向解析收敛

- `core/inbox_routing.py:_ID_TOKEN` 由 `\[(?:d|ow|ocw):([0-9a-f]{6,})\]`
  收紧为 `\[d:([0-9a-f]{6,})\]`。OpenWorker 时代 rebrand 留下的
  `ow` / `ocw` 旧 spell 在 P2 终止解析。
- 历史 API（`InboxStore` 的 add / resolve 协议、`deliver` 的 `[d:…]`
  嵌入方向）保持不变 —— 写方向自 PR #73 已统一为 `[d:…]`。
- `docs/architecture/relay-mode-removal.md` 的 P2 承诺条目更新为"已
  终止"。

## 后果

### 正面

- **P2 中期验收第一项可机械化验收**：Source / Citation 校验在
  `core/sources.py` 即可写最小测试，UI 集成只需消费 `SourceDTO` 的新
  字段，不再需要"猜哪个文件"型启发。
- **多种常用文件格式进入统一 Source 处理路径**：`read_file`（text /
  log / md / 代码）+ `read_document`（PDF / XLSX / DOCX）都通过
  `core/citation.cite` 走同一钩子，Source ledger 是单条真相来源。
- **Inbox 解析代码少一个分支**：`resolve_from_reply` 不再承担"两种
  旧 spell 都要识别"的合约，回归矩阵更小。
- **Automation 走线可视**：`tests/test_automation_convergence.py`
  让 §7.2 中"Automation 不允许形成第二套执行模型"有明确的失败信号，
  不再需要跑完整 Reference Task 才能感知 wiring 漂移。

### 负面 / 风险

- **Inbox 解析收紧是破坏性变更**：用户在 P1 之前收到的 Slack /
  Telegram 消息可能仍带 `[ow:…]` 或 `[ocw:…]` 标记；P2 起这些
  回复会被路由为新消息而不会解析为 inbox 回复。这是 relay-mode-removal
  文档中"可由 P2 决定是否终止"的承诺条款，没有"软开关"——任何旧
  approval 的解决必须通过 UI 重新发起。
- **Citation schema 收紧**：新 `CitationRange` 不再接受 `{"page": 2}`
  这种无 `kind` 的 citation dict（之前 `mark_cited` 的隐式契约），
  reader 调用方需迁移到 typed 调用或带 `kind` 的 dict。
- **多根 workspace 暂未覆盖**：`_code_files` capability 拿到了
  `read_document`；`_files`（cowork/ops 的多根变体）当前仍走 aisuite
  `read_file`，不自动 cite。后续扩展需要先把 `read_file` 多根支持
  + cite 钩入补齐。
- **`read_document` 受 Pypdf 抽取质量限制**：扫描 PDF 走 pypdf 抽出来
  是空字符串（"合法空内容"），citation 仍记录 page number，UI 可后续
  调 `pdf_support.rasterize` 给出图像版。

### 后续

- Cowork / Ops 多根 `read_file` 的 cite 钩入（先把 `read_file` 改成支持
  多根 roots，再接 `core/citation.cite`）。
- 扫描 PDF 的图像回退：在 `read_document` 抽不出文本时，UI 可在
  渲染层自动转 `pdf_support.rasterize` 出图。
- 解析收紧后若需要回滚，可由独立 ADR 重新引入兼容解析。

## 实施引用

- `core/sources.py` — `CitationRange` / `to_range_dict` / `normalize_cited_ranges`
  / `SourceStore.add_citation` / `to_dto` 扩展
- `core/citation.py` — capture+cite 共享 chokepoint（runscope fallback）
- `services/server/contracts.py:92` — `SourceDTO` 新增 `location` /
  `cited_ranges`
- `core/agent.py` / `core/agents/base.py` / `core/catalog.py` —
  `source_store` + `run_id` 注入 AgentContext → capability → reader
- `services/server/manager.py` — `source_store_for(workspace)` 按 workspace
  缓存 `SourceStore`
- `services/server/manager_automations.py` / `manager_sessions.py` —
  自动化 / 手动 session 都接线
- `integrations/tools/files.py:file_tools` — `source_store=` / `run_id=`
  closure 钩子（`read_file`）
- `integrations/tools/documents.py:document_tools` — `read_document` 单工具
  覆盖 PDF / XLSX / DOCX
- `core/inbox_routing.py:_ID_TOKEN` — 仅解析 `[d:…]`
- `tests/test_source_citation.py` — CitationRange + SourceStore + DTO + Inbox
  解析收紧的回归测试
- `tests/test_automation_convergence.py` — §7.2 automation 收敛结构化
  守护测试
- `tests/test_read_file_cite_e2e.py` — `read_file` 真实 SessionManager
  e2e（citations 落 `source_store_for(workspace)`，run_id = TaskRun.run_id）
- `tests/test_read_document.py` — `read_document` 3 种格式 +
  error paths + 真实 SessionManager e2e
- `docs/architecture/relay-mode-removal.md` — 关闭 P2 承诺条目
