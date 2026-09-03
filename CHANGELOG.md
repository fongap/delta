# Changelog

本项目所有显著变更按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 方式记录，版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

CHANGELOG 只记录用户可感知的变化和重要工程能力变化。

具体实现过程、测试数量、调试记录、依赖锁文件变化和单纯代码整理，应保留在 Pull Request、Commit 和 CI 记录中，不再写入 CHANGELOG。


## [Unreleased]

### 新增 (Added)

- **Reliable Task Runtime** (ADR-005 — DELTA_BLUEPRINT §7.1 "Reliable")
  - **Ledger 词汇表扩展** (`core/ledger.py`)：固定 `KNOWN_EVENT_TYPES` 集合（tool/approval/artifact/validation/side_effect/run.*），并新增 `close()` 以支持测试释放。
  - **`make_mirroring_audit_sink`** (`core/ledger_event.py`)：把现有 `audit_sink` 字典（来自 `core/engine.py:_audit`）在 runscope 命名 run 时镜像为 ledger 事件（`tool.proposed/started/finished/denied`、`approval.requested/granted/denied`）。AuditStore 仍保留为旧数据访问层。
  - **Artifact 领域对象** (`core/artifact.py`)：`Artifact` dataclass（path, name, kind, size, sha256, run_id, incomplete, registered_at），`register_run_artifacts` 在 run 结束时遍历 workspace 计算 sha256，无法读取的文件标记 `incomplete=True`，并向 ledger 发 `artifact.registered` + `artifact.completed`。`TaskRun.artifacts` 从 `list[str]`（按 mtime 猜测）改为 `list[dict]`（结构化），`from_dict` 自动升级旧数据。
  - **Validation 门控** (`core/validation.py`)：`ValidationCriteria`（min/max_artifacts, required_paths, required_substrings, min/max_size, require_complete, csv_required_headers）+ `run_validation` + `gate_status`。`TaskRun.status` 现在可取 `"validation_failed"`（与 `"error"` 区分），验证结果以 `validation.passed/failed` 事件入 ledger。`ScheduledTask.validation_criteria` 字段为任务作者提供"该任务算完成了吗"的确定性合约。
  - **副作用安全 Durable Resume** (`core/idemlog.py`)：`IdempotencyLog` 持久化 `(run_id, tool_call_id, args_sha256, result)`；`core/engine.py:_execute_sync` 在 `idem_log` 注入时先查询（命中则返回 `("replayed", result)`）后提交。`build_engine` 与 manager 注入 `idem_log`；生产路径中 `side-effects.db` 与 `run-events.db` 同目录。
  - **单一 Task / Run 身份收敛**：`TurnEngineAdapter` 接受可选 `run_id`；自动化路径在 `_run_scheduled_task` 中传入 `TaskRun.run_id`，使 TaskStore、ledger、artifact、validation、idemlog 五者共享一个身份（`mgr.run_ledger.runs() == [run.run_id]`）。交互路径仍使用 uuid。
  - **架构文档**：`docs/architecture/adr/ADR-005-reliable-task-runtime.md`；`README.md` 索引同步。

- **Reference Task e2e 验收**（CSV/XLSX→分析→Markdown 报告）：`tests/test_reference_task.py` 覆盖 DELTA_BLUEPRINT §7.1 短期验收 8 条（端到端流 / 高后果经 Approval / Artifact 真实有效 / Validation 判定 / 关键事件可回放 / 同一 ledger run_id / 权限与手动一致 / 已 commit 副作用不重放）。

### P2 — 实用 (DELTA_BLUEPRINT §7.2)

### 新增 (Added)

- **Source / Citation 提升为可定位的一类证据** (ADR-006)
  - **`CitationRange` typed schema** (`core/sources.py`)：判别字段 `kind` ∈ `lines` / `page` / `cells` / `row` / `column` / `sheet` / `message_id` / `custom`；每种 kind 仅保留与该定位语义相关的字段进入 canonical form。
  - **`to_range_dict` / `normalize_cited_ranges`**：序列化前做结构性校验（必填字段、字段类型），失败抛 `ValueError`，保证 run 永远不会留下 UI 无法渲染的 citation。
  - **`SourceStore.add_citation`**：单条 citation 的便捷 API，调用方只需给一个 `CitationRange` 或带 `kind` 的 dict；复用 `mark_cited` 的锁/校验/落盘契约。
  - **`read_file` 自动 cite** (`integrations/tools/files.py`)：`file_tools(workspace, *, source_store=None, run_id=None)` 接受可选钩子；成功读入时 closure 通过 `core/citation.cite` 捕获 `SourceRef` 并以 `lines` 范围落 citation。错误路径（path 越界、文件不存在、读失败）不写任何 citation。
  - **`read_document` 工具** (`integrations/tools/documents.py`)：单工具覆盖 PDF / XLSX / DOCX 三种格式，Markdown / TXT 走 `read_file`。PDF 用 `pypdf` 抽文本（一页一个 block），XLSX 走 `services/server/sheet_preview.py` 的 stdlib zipfile 解析（一个 sheet 一个 block），DOCX 同样走 stdlib zipfile + ElementTree 抽段落（一个 paragraph 一个 block）。每种格式成功读入时按各自的 locator 词汇（`page` / `cells` / `message_id`）自动 cite。摘要视图（`block=None`）只返回 block 列表不写 citation；只有真正读到的位置才进 source ledger。
  - **`core/citation.cite` 共享钩子**：capture_file + add_citation 的统一入口；`run_id` 解析顺序：① 显式参数（自动化预分配）→ ② `core.runscope.current()`（手动 turn 由 adapter 在每轮 set，worker thread 通过 `asyncio.to_thread` 的 context copy 看到）。失败（OSError / 校验错）静默吞掉——reader 已成功，不让审计 hook 拖垮 run。
  - **`source_store` / `run_id` 注入生产路径** (`core/agent.py`, `core/catalog.py`, `services/server/manager.py`)：`build_engine` 接受 `source_store` + `run_id`，`AgentContext` 同步带出；`SessionManager.source_store_for(workspace, run_id)` 按 workspace 缓存 `<workspace>/.delta/sources.json`（与 `run-events.db` / `side-effects.db` 同目录）；手动 session 与自动化 run 都自动接线。
  - **`SourceDTO` 新增 `location` / `cited_ranges`** (`services/server/contracts.py`)：additive 字段，`to_dto` 透出，让 UI 看到文件位置与"哪些 run 引用过、引用了哪些行 / 页"。
- **Automation 收敛结构化守护** (`tests/test_automation_convergence.py`)：廉价 wiring 守门员——自动化与手动 runtime 共用 `_build_task_engine` + manager 的 `idem_log` / `audit_sink`；自动化 run 事件 `type` 必须落在 `KNOWN_EVENT_TYPES`；`TaskRun.run_id == run_ledger.runs()`（身份不分裂）。
- **`read_file` / `read_document` e2e 验收** (`tests/test_read_file_cite_e2e.py`, `tests/test_read_document.py`)：用真 SessionManager + scripted provider 跑过 `_run_scheduled_task`，验证 citation 在 `mgr.source_store_for(workspace)` 落盘、citation 里的 `run_id` 等于 `TaskRun.run_id`（G1 单一身份）。
- **架构文档**：`docs/architecture/adr/ADR-006-p2-source-citation-and-convergence.md`；ADR 索引同步。

#### P2 follow-up A — Cowork/Ops 多根 `read_file` cite 钩子 (ADR-006 续)

- **`integrations/tools/files.py`**：`file_tools` 新增 `roots=` 参数；提供时，`read_file` 接受绝对路径并按任何根解析（与 aisuite 的多根 `read_file` 等价），cite 钩子在文件**实际所在**的根下写入 source ledger（不是主根），所以从附加只读文件夹读出的引用仍以正确的 workspace-relative 路径进入 source store。新 `_make_multiroot_citer` 实现 per-read 根匹配。
- **`core/catalog.py::_files`**：从 aisuite 多根 toolkit 里 drop `read_file`（被我们的 cite-aware 多根版本替代），保留 `read_file_lines`（单独的 aisuite 窗口化读取工具，目前没有 cite 钩子）。`read_file_lines` 仍存在——它满足多根 session 下的另一个用例窗口读取。
- **`tests/test_multiroot_read_file_cite.py`**：10 个新测试覆盖契约——多根路径解析（primary/只读/可写根）、错误路径（路径逃出所有根）、相对路径在多根下回退到主根、cite 钩子按匹配根写入、`_files` capability 暴露我们的 read_file + 保留 aisuite 的 `read_file_lines`。
- **`tests/test_catalog.py`**：未变（`COWORK_TOOLS` 集合里的 `read_file` 与 `read_file_lines` 名字保持不变；read_file 现在是我们的，read_file_lines 仍是 aisuite 的）。

#### P2 follow-up B — scanned PDF image fallback (ADR-006 续)

- **`core/pdf_support.py`**：新增 `rasterize_file(path, page_indices=None, max_pages=...)` —— 把 PDF 文件里的指定页渲染成 PNG data URL（懒 import pypdfium2，返回 `{page_no: png_data_url}`，失败返回 None）。与已有的 `rasterize(data_url)` 互补：那个用在适配链（attachment → model），这个用在 `read_document` 路径。
- **`integrations/tools/documents.py::_read_pdf`**：当 pypdf 的 `extract_text` 返回空串（扫描 PDF——图片页无内嵌文本）时，用 `rasterize_file` 把该页渲染成图片并放进 block 的 `image` 字段，block 打上 `scanned: True` 标记，`text` 占位为 `[scanned page N — no extractable text; page rendered as image]`。纯文本页不变。摘要视图给 scanned block 加 `scanned: True` 标记。
- **`tests/test_scanned_pdf_fallback.py`**：6 个新测试——全扫描 PDF（image + scanned flag + cite 仍记页码）、混合 PDF（只扫空白页，文本页不变）、纯文本 PDF 完全不变。
- 设计契约：scanned page 的 cite 仍记 `page` 类型（页码）；`pypdfium2` 缺失时 graceful degrade（scanned page 只有空 text，无 image）。

### 变更 (Changed)

- **`core/sources.py`**：`mark_cited` 入参在加锁前先经 `normalize_cited_ranges` 校验，避免半写入的 citation 落盘。
- **`integrations/tools/files.py`**：`file_tools` 接受可选 `source_store=` / `run_id=`（向后兼容：不传则行为与 P1 完全一致）。`file_tools` 的内部 `_make_citer` 委托给 `core.citation.cite`，便于与 `read_document` 共享同一 chokepoint。
- **`core/agents/base.py`**：`AgentContext` 新增 `source_store` + `run_id` 字段（可选，向后兼容）。
- **`core/agent.py`**：`build_engine` 接受 `source_store=` / `run_id=`，注入 `AgentContext`。
- **`core/catalog.py`**：`_code_files` 同时挂上 `read_file` 与 `read_document`（单根 workspace 路径）。
- **`services/server/manager.py`**：新增 `source_store_for(workspace, run_id)` 实例方法（按 workspace 缓存 `SourceStore`）。
- **`services/server/manager_sessions.py` / `manager_automations.py`**：手动 session 与自动化 run 都在 `build_engine` 调用时传入 `source_store`；`run_id` 优先取显式值（自动化的 G1 run id），否则由 adapter 在 `runscope` 命名后由 `core.citation.cite` 拉取。
- **`tests/test_catalog.py`**：`CODE_TOOLS` 加入 `read_document`，反映 code 表面能力扩展。

### 移除 (Removed)

- **Inbox 旧 token 解析兼容终止**（`core/inbox_routing.py:_ID_TOKEN`）：由 `\[(?:d|ow|ocw):([0-9a-f]{6,})\]` 收紧为 `\[d:([0-9a-f]{6,})\]`。OpenWorker 时代 rebrand 留下的 `ow` / `ocw` 旧 spell 在 P2 起不再解析为 inbox 回复（写方向自 PR #73 已统一为 `[d:…]`）。`docs/architecture/relay-mode-removal.md` 的 P2 承诺条目更新为"已终止"。任何 P1 之前的旧 approval 需要通过 UI 重新发起。

### P3 — 长期（智能）第一刀 (DELTA_BLUEPRINT §7.3)

### 新增 (Added)

- **只读 Run Analyzer (ADR-007)**：§7.3 受控学习链 `Candidate → Evidence → Evaluation → User Acceptance / Policy → Promotion → Use → Revocation` 的 **Evidence 步 query 层**。仅从 `RunEventLedger` + `SourceStore` + `TaskRun` 现有事实库读，不写任何"经验 / 候选 / 失败记忆 / 偏好 / Skill 候选"表，不动 Skill / Memory / Preference 任何活跃状态，不引入第二个事实库（§10.6 "ledger 是单一事实来源"不被破）。
  - **`core/analyzer.py`**：`Analyzer` 类 + 三个 query（`timeline_for_run` / `automation_health` / `source_citation_hits`）+ 模块级薄包装。所有 query 第一参数 `workspace: str` 必填，**不允许**"全局 fetch + 客户端过滤"——per-workspace 边界是 D-4 强约束；同一 workspace 内支持跨 session / 跨 TaskRun 聚合。
  - **`TimelineEntry`**：给定 `run_id` + `workspace`，按 `seq` 把 ledger 事件还原为有序端到端时间线（不丢任何事件类型，封闭词汇表是 `KNOWN_EVENT_TYPES`）。
  - **`AutomationHealth`**：跨 N 个 run 聚合 `TaskRun.status` + `validation.passed/failed` 计数 + 平均时长；`failure_reasons` 来自 `run.failed` / `validation.failed` / `tool.finished{status: "error"}` payload；`run_error_counts` 单独承载 `TaskRun.error` 顶层标签以避免与 ledger 事件双重计数。跨 workspace 的 task 抛 `WorkspaceMismatchError`，**不**做静默跨 workspace 聚合。
  - **`SourceCitationHit`**：把 `SourceRef.cited_ranges` 与 ledger `tool.finished{tool ∈ {read_file, read_document}}` 关联，给出"这条 citation 来自哪一次 read"，未匹配的 citation `ledger_payload=None`。
- **`timeline_for_run` ledger 端 workspace 过滤 (ADR-007 §10.6 step 2)**：`Analyzer.timeline_for_run(run_id, *, workspace=None)` 新增可选 `workspace` kwarg。传入时过滤推到 SQL 层（`WHERE run_id = ? AND workspace = ?`），走 `idx_run_events_workspace (workspace, run_id, seq)` 复合索引而非全表读后 Python 过滤。跨 workspace 的 run 返回 `[]`（与"run 不存在"同形，不泄露 run 是否存在）；`workspace=None` 返回完整 run（legacy pre-migration 数据或显式跨 workspace 意图）。`RunEventLedger.events_in_workspace(run_id, workspace)` 是新的底层 read API，空串 sentinel 匹配 NULL 行（legacy 行）。Analyzer **不**自动把 `self.workspace` 注入 `workspace=`——避免掩盖调用方错误。
- **`run_events` / `task_runs` 加 `workspace` 列（ADR-007 §10.6 路径第一步）**：
  - 两张主表加 `workspace TEXT` 列；迁移沿用 `core/conversations.py:121-140` 的 `try / except sqlite3.OperationalError` 模式——fresh DB 通过 `CREATE TABLE` 拿到列，legacy DB 通过 `ALTER TABLE` 拿到列一次后被 `OperationalError` 吞掉，幂等可重入。
  - `RunEventLedger.append(..., workspace: str | None = None)` 接受可选 kwarg；`RunEventLedger.events(run_id)` 返回的 dict 现在带 `workspace` 键。
  - `TaskRun.workspace: str = ""` 字段，`to_dict` / `from_dict` 双向兼容（legacy 缺字段落回默认 `""`）。
  - 关键契约：`workspace` **不**进 hash basis——同一 payload + actor + ts + 不同 workspace 的两行有相同 hash；legacy chain 在迁移后仍能 `verify()` 通过。这允许 §10.6 路径"先扩列再升 Analyzer"无需重算历史 hash。
  - 新增 `idx_run_events_workspace` / `idx_runs_workspace` 索引让 per-workspace 查询走索引而非全表扫描。
  - 接入点：`TurnEngineAdapter._track` 的 `run.started` 写入 `self.workspace_path`；`register_run_artifacts` 的 `artifact.registered` / `artifact.completed` 写入 `workspace=`；`IdempotencyLog.commit` 透传 `workspace=`；`services/server/manager_automations.py` 的 4 个 `add_run` 站点从 `task.workspace` 带入；`finalize_manual_run` 反向回填旧行（task 在的 workspace 写回 run）。
- **架构文档**：`docs/architecture/adr/ADR-007-p3-readonly-run-analyzer.md`；ADR 索引同步。后续 P3 工作（自动 Reflection / Skill Evaluator / 自动 Failure Memory / 自动 Preference Promotion / 条件型 Automation / Source 语义检索 / 跨项目聚合）**全部**为独立 ADR 评估，不在本 PR 范围。

#### P3 §7.3 Source 完整能力 — per-citation 有效性检查 (ADR-006 续)

- **`SourceStore.validate_citation(ref_id, run_id, range_obj)`** (`core/sources.py`)：回答 "UI 现在滚动到引用的行/页/单元格, 还能不能落到 run 当时看到的内容?" 返回 dict: `{valid, status, reason, current_sha256?, current_line_count?}`。
  - `invalid` 原因：`content_changed`（文件 sha256 变了）/ `out_of_bounds`（文件 current 但 range 越界 — 被截断或 windowed read 的 start_line 已过 EOF）/ `file_missing`（status 已是 missing）/ `source_gone`（ref 本身被删了，区别于 file_missing）/ `valid`。
  - 文件已 missing 时短路（不再触碰文件系统）；line kind 有便宜 bound check（数 `\n` 即可），其他 kind（page / cells / message_id / custom）只校验 status。
- **`Analyzer.SourceCitationHit.validity`**：每个 hit 附带 per-citation 验证结果；多 range 的 hit 用 worst-reason roll-up（file_missing > out_of_bounds > content_changed > valid），UI 每对 (source, run) 只看到一个信号。
- **`tests/test_source_citation_validity.py`**：13 个新测试覆盖契约——valid / out_of_bounds（file truncated）/ content_changed / file_missing（reflexive + status pre-missing）/ source_gone / 非 lines kind / Analyzer hit 携带 validity / worst-reason roll-up / to_dict 序列化 / 模块级 wrapper 透传。

#### P3 §7.3 Source 完整能力 — 索引失效检测 (mtime fast path, ADR-006 续)

- **`SourceRef` 新增 `mtime_ns` / `size_bytes` 字段** (`core/sources.py`)：capture 时记下，check 时比对；未变的文件根本不算 sha256。
- **`SourceStore.capture_file`**：现在缓存 `(mtime_ns, size_bytes)`。同一 path 重新 capture 命中相同 fingerprint 时也刷新缓存。
- **`SourceStore.check_freshness`**：重写走 mtime fast path —— `_classify_against_disk(ref, p)` 静态方法实现三阶检查（stat → 缓存匹配 → sha256）。稳定文件 0 次 `read_bytes`。
- **`SourceStore.reindex_stale(force=False)`**：新增定向失效检查入口（mtime 缓存命中跳过 sha256；`force=True` 强制全量重算）。返回真正 drift 的 ref 列表。
- **`tests/test_source_index_stale.py`**：10 个新测试覆盖契约——`read_bytes` 调用计数（mtime fast path 真零读）、change 仍被检测、missing 仍被检测、`reindex_stale` 准确、force 模式全量重算、mtime 缓存刷新、legacy ref（无 mtime 缓存）总是走 sha256。
- **行为变更**：`test_check_freshness_async_matches_sync` 的写大小从 1 字节改为 64 字节 —— Windows 上 same-length overwrite 可能不更新 mtime（fast path 据此正确判定 current，与 "真实无改动" 语义一致）。
- **legacy 兼容**：旧 `sources.json` 没有 `mtime_ns`/`size_bytes` 字段时反序列化为 `None`，`_classify_against_disk` 走 sha256 兜底路径，行为与 P2 一致。

#### P3 §7.3 Context 完整能力 — 最小 Recovery Context (§4.5)

- **`core/recovery.py`**：新模块。`RecoverySnapshot` dataclass（10 字段：schema / snapshot_at / run_id / session_id / phase / pending_tool_call / pending_inbox_item_id / last_event_seq / todo_summary / recent_artifacts / error）+ `RecoveryStore`（per-session JSON sidecar，write/get/clear/latest）。
  - Phase 枚举：`running` / `awaiting_approval` / `awaiting_question` / `awaiting_directory` / `awaiting_plan`。
  - 契约：snapshot 是 **advisory** — engine resume 不读它；用于 UI "这个 session 卡在哪里" + 跨 session "things awaiting attention"。
  - 验证：`session_id` 必填；非 running phase 必须有 `run_id`（不能在没 run 的情况下等审批）。
  - Forward compat：schema 版本字段；future-schema entries 在 sidecar 中保留（不被旧 reader 覆盖），在内存中跳过（不被旧 reader 误读）。
- **`core/sessions.py`**：`SessionRecord` 新增 `recovery: dict | None` 字段（snapshot 的 denormalized 副本）。
- **`core/conversations.py`**：SQLite `sessions` 表新增 `recovery TEXT` 列（CREATE TABLE + ALTER TABLE 迁移）；`save()` / `load()` 读写该列；`_load_recovery(row)` helper 处理 corrupt JSON 的 graceful degrade（snapshot 是 advisory，坏的不影响 session 加载）。
- **`tests/test_recovery_context.py`**：17 个新测试——to_dict/from_dict roundtrip / forward compat（future-schema refuse + unknown fields drop）/ validation（phase / run_id / session_id）/ store write-get-clear / store reload / latest 排序 / future-schema 保留 / SessionRecord SQLite roundtrip / legacy row None / corrupt JSON degrade。

### 不变（In scope but unchanged）

- 短中期冻结面（§8.8）：Standing Approval / MCP / Subagent / Memory / Skill / Inbox / Self Wake 范围未扩。
- 长期行为不变：模型行为、Skill 权重、用户偏好、Standing Rule、Task Schedule 均**未**自动调整；§7.3 "不允许模型因为一次任务成功就自动改变长期行为" 仍由 0 个写入路径守护。

### P1 — 可靠 (DELTA_BLUEPRINT §7.1)

### 变更 (Changed)

- **TaskRun**：`status` 增加 `"validation_failed"` 枚举值；`artifacts: list[str]` → `artifacts: list[dict]`（带 sha256/incomplete/registered_at；旧数据通过 `from_dict` 自动升级）。
- **ScheduledTask**：新增可选 `validation_criteria: dict` 字段（持久化为 dict；engine 启动时转换为 `ValidationCriteria`）。
- **`services/server/manager_automations.py`**：`_run_scheduled_task` 在执行后调 `register_run_artifacts`，再调 `run_validation` 决定 `run.status`（不再无条件 `= "ok"`）；`notify_on_completion` 仅在 `status == "ok"` 时触发。
- **`core/runtime.py`**：adapter 接受 `run_id=` 覆盖以供自动化调用方使用。
- **`services/server/manager.py`**：实例化 `self.idem_log` (IdempotencyLog) 与 `self.audit_sink` (mirroring 闭包)，`audit_sink` 在 build_engine 调用中替换 `audit_store.append`。
- **`services/server/manager_contract.py`**：声明 `idem_log: IdempotencyLog` 与 `_bind_runtime(..., *, run_id=None)`。

### P0 — Post-Decoupling 收尾

### 移除 (Removed)

- **OpenWorker Cloud 运行时依赖** (ADR-004)
  - 彻底删除 `integrations/cloud.py` (690 行): Auth0 PKCE 登录、/v1/me、managed OAuth broker、telemetry、persona gallery、GitHub installation token mint、relay routing、cloud disconnect。
  - 删除 `packages/config.py` 5 个 `cloud_*` 字段 (`cloud_base_url`, `cloud_auth_domain`, `cloud_client_id`, `cloud_audience`, `cloud_relay_ws_url`)；默认值指向 `api.openworker.com` / `opencoworker.us.auth0.com` 已移除。
  - 删除 `services/server/app.py` 12 个 cloud routes: `/v1/cloud/status`, `/v1/cloud/telemetry`, `/v1/cloud/login`, `/v1/cloud/logout`, `/auth/callback`, `/v1/connectors/*/connect-managed`, `/oauth/callback`, `/v1/cloud/gallery/*`。
  - 删除 `services/server/manager_events.py` telemetry (`emit_session_created`, `install_id`, `telemetry_enabled`)。
  - 删除 `services/server/manager_gateway.py` 对 `cloud.fresh_access_token` / `cloud.github_installation_token` / `cloud_relay_ws_url` 的 wiring；relay token 现默认空串。
  - 删除 `apps/desktop/src/components/GalleryModal.tsx`、`CloudSignIn.tsx`、所有 cloud sign-in / telemetry / managed OAuth UI 引用 (127 处)。
  - 删除 `tests/test_cloud.py`, `tests/test_cloud_server.py`, `tests/test_slack_workspaces.py`, 3 个 e2e spec (`cloud.spec.ts`, `cloud-status-pending.spec.ts`, `sidebar-account.spec.ts`)。
  - 删除 `.github/workflows/upstream-sync.yml` (自动镜像 OpenWorker 上游)；`UPSTREAM.md` 重写为"独立项目，不再同步"。
  - 删除 `pyright-report.before.json` 历史基线。

### 新增 (Added)

- **Managed Capability Ports** (`integrations/managed/`)
  - `OAuthBroker` / `NullOAuthBroker` (begin/exchange/refresh/disconnect)
  - `RelayTransport` / `NullRelayTransport` (open/recv/close)
  - `GitHubAppBroker` / `NullGitHubAppBroker` (get_installation_token/clear)
  - `ExternalIdentityProvider` / `NullIdentityProvider` (verify_assertion)
  - `ExternalIdentity` dataclass (issuer/subject/display_name)
  - `ManagedConfig` (enabled=False, base_url="", device_token="", relay_ws_url="")
  - `ManagedUnavailableError` (用户可见 "managed relay is unavailable because no managed service is configured")
- **Federation Boundary** (`integrations/federation/openworker/`)
  - 占位适配器: `OpenWorkerOAuthBroker`, `OpenWorkerRelayTransport`, `OpenWorkerGitHubAppBroker` (全部 `NotImplementedError`)
  - README 明确: 删除此目录即完全移除 OpenWorker 支持，零影响
- **架构文档**: `docs/architecture/hub-federation-boundary.md` (明确 OpenWorker 仅为可选适配器，Native Device Token 为基座)

### 变更 (Changed)

- `packages/config.py`: 仅保留 `cloud_relay_ws_url` (默认空)，删除 4 个 cloud 字段
- `integrations/connectors/relay_client.py`: `TokenProvider` 重命名为 `HubTokenProvider` 概念，默认空串
- `integrations/connectors/integration_github.py`: relay 模式返回 "managed relay unavailable" 而非调用 cloud
- 所有 connector detail 组件: 移除 cloud sign-in 检查，仅保留 manual connect

### 移除 (Removed)

- **OpenWorker / Cloud 运行时残留收尾**
  - `packages/config.py`: 删除最后一个遗留 `cloud_*` 字段 `cloud_relay_ws_url`；`manager_gateway.py` 直接读取 `integrations.managed.ManagedConfig.relay_ws_url`。
  - `integrations/connectors/adapters.py`: 删除"sign in and set cloud_relay_ws_url"日志。
  - `apps/desktop/src/api.ts`: 删除 `DELETED: All OpenWorker Cloud backend routes...` 注释块、`CloudSignIn` 引用、`cloud/gallery` 注释、Connector 类型中 `cloud sign-in` 描述。
  - `services/server/manager_inbox.py`: 删除 `slack_status` / `github_status` 中的 `signed_in: False` 字段。
  - `apps/desktop/src/{api.ts,features/connectors/components/{ConnectorsList,SlackDetail,GithubDetail}.tsx}`: 删除 `signed_in` 运行时 guard。
  - `apps/desktop/tests/e2e/fixtures.ts`: 删除 `/v1/connectors/*/connect-managed` / `/v1/cloud/gallery*` mock handler；`hubspotState.nextAccess` 字段删除；`signed_in: true` 假数据删除。
  - `services/server/app.py`: 删除 `/v1/personas/install` 中的 `gallery_slug` 分支及错误消息。
  - `apps/desktop/src/api.ts`: `installPersona` 形参删除 `gallery_slug`。
  - `tests/test_server.py`: 删除"managed connect 路由已返回 404"断言。
  - `apps/desktop/src/components/Onboarding.tsx` + `App.tsx`: 删除 `finish("gallery")` 与 `next === "gallery"` 已死代码。
  - 38 个 `gallery.*` i18n 键（仅 persona gallery 副本）整块删除于 `packages/i18n/{en,zh}.ts`。
  - 7 个 `connectors.{signInCloud,signInCloudFirst,requiresCloudSignIn,checkingCloudSignIn,healthSignInNeeded,healthSignInPaused,addAccountComingSoon_legacy}` 及 3 个 `nav.{signInCloud,cloudOneClickNote,accountWithEmail}` i18n 键删除。
  - 11 个 `sessionIntro.{task.*,prompt.*,start,configure}` + `app.tryATask` i18n 键删除（首页示例卡片收尾）。
  - 2 个 `providers.{customProviderCard,customProviderCardSub}` i18n 键删除（"自定义服务商"大卡片 → "添加服务商"链接）。
- **Federation 整目录删除**：`integrations/federation/openworker/` 全部 5 个文件（仅 `NotImplementedError` 占位）；`docs/architecture/hub-federation-boundary.md` 改写为 Federation = "开放、供应商无关、可选"边界，OpenWorker 降级为若干潜在适配对象之一。
- **诊断产物清理**：`pyright-report*.json`（7 个）+ `ruff-report.json` 共 8 个本地扫描报告从仓库删除；`.gitignore` 新增 `pyright-report*.json` / `ruff-report*.json` 规则。
- **首页示例卡片**：`apps/desktop/src/components/SessionIntro.tsx` 整文件简化为单 `<h1 class="greeting">"我能帮您做点什么？"</h1>`；`App.tsx` 的 code/chat idle hero 的 `SUGGESTIONS` 数组与 `.suggestions` 区块删除；相应 CSS（`.task-card`, `.task-dot`, `.task-card-act`, `.intro-tasks`, `.intro-addfolder`, `.suggestions`, `.suggest*`）整段删除。
- **首页 e2e 测试**：`apps/desktop/tests/e2e/session-intro.spec.ts`（3 个用例）整文件删除（任务卡片已不存在）。
- **Dead CSS**：`styles.css` 中 `boot-pulse` keyframes / `.thinking-live` animation / `.waiting-spinner` rotation 删除。
  - **Dead TS comments**：`integrations/connectors/{relay_client,catalog_copy,descriptors}.py` 与 `services/server/manager_events.py` 中 "OpenWorker Cloud" 历史引用全部更新为"managed service / future Federation Adapter"中性格式。
- **用户可见 OpenWorker 产品身份残留**
  - `integrations/mcp/oauth.py`: DCR `CLIENT_NAME` 由 `"OpenWorker"` 改为 `"Delta"`（OAuth 同意页与 MCP 服务器看到的 client_name）。
  - `apps/desktop/tests/e2e/fixtures.ts`: 顶层 persona 列表的 `cowork` 节点 `name` 字段由 `"OpenWorker"` 改为 `"Delta"`；`PERSONA_DETAIL` 同步更新。
  - `apps/desktop/src/components/Sidebar.test.tsx`: `{ id: "cowork", name: "OpenWorker" }` 测试 fixture 改为 `"Delta"`。
  - `apps/desktop/src/personaScope.ts`: 注释中"API + tests keep 'OpenWorker' / 'Ops Delta'"与实际不符，更新为"keep 'Delta' / 'Ops Delta'"。
  - `apps/desktop/tests/e2e/nav-collapse.spec.ts`: 注释"the 'OpenWorker' persona group header"更新为"the 'Delta' persona group header"。
  - `tests/test_connectors.py`: 测试 fixture `account: "rohit@openworker.com"` 改为 `"rohit@example.com"`。

#### 新增 (Added)

- **Federation 边界的最终语义**：`docs/architecture/hub-federation-boundary.md` 重写为"Delta 是独立、本地优先运行时；Federation 是可选开放边界；OpenWorker 只是潜在外部适配对象之一"。
- **ADR-004 附录 A**：`docs/architecture/adr/ADR-004-openworker-decouple-hub-boundary.md` 追加"附录 A：P0 后置清理（2026-09）"，记录 Federation 边界与 `cloud_relay_ws_url` 的进一步收紧；历史决策 D-1 至 D-9 保留不改写。
- **`mode: "relay"` 运行时路径清理前置承诺**：`docs/architecture/relay-mode-removal.md` 新文件，记录 P0 之后必须由 P1 阶段清理的 `mode: "relay"` 死路径、Slack/GitHub relay 适配器、Inbox `[ow:…]` 回复 token 写入方向。**当前 P0 范围内仅保证**：production 路由不会写入 `mode: "relay"`、`managed_connect_*` 三个函数已是 unreachable code、所有 161 个单元测试与 73 个 Python 测试通过。P1 必须删除 `relay_client.py` / `adapters.py` relay 分支 / `setup.py` 三个 `managed_connect_*` 函数 / `github_installs.py` 的 `managed_connect_install` / UI relay 分支 / 配套测试。

#### 变更 (Changed)

- **Connector 状态字段收敛**：`SlackStatus` / `GithubStatus` 删除 `signed_in` 字段；`connect-managed` mock 行为从"返回 404"升级为"彻底不挂 mock handler"。E2E fixture 不再返回 `signed_in: true` 假数据 — 反映真实 runtime。
- **首页**：首屏只保留 `intro` 内的 greeting + 任务输入区；code/chat idle hero 同样只保留 greeting。
- **"思考中"动画降噪**：
  - `WaitingForAgent`（`App.tsx`）：删除旋转的 `.waiting-spinner` 圆环；改为静态文字 "思考中" + 3 个固定点（`thinking-dots`）opacity 循环（1.2s，无 scale、无 rotation、无 layout shift，使用 `--faint` 灰蓝，无高饱和）。
  - `ThinkingBlock`（`Transcript.tsx`）：删除 `.thinking-live` 的 `boot-pulse`（含 `scale(0.96)→scale(1.04)`）；live 分支同样改为 3 个点的 opacity 循环；文字 "思考中…" 改为 "思考中"。
  - `prefers-reduced-motion: reduce` 下：3 个点保持 opacity: 0.6 静态。
  - CSS keyframes: 删除 `@keyframes spin` 的 `waiting-spinner` 用户（保留为 `.spinner` 步骤指示器）；删除 `boot-pulse`。
- **模型设置页**：
  - "自定义服务商" 大卡片删除（`ManageTabs.tsx`），改为安静的一行 "添加服务商 ›" 链接。
  - 卡片副文 "添加 OpenAI 兼容端点或原生协议。别名将成为模型前缀（别名:模型）。" 删除。
  - "自定义服务商" → "添加服务商"（更自然的产品语言）。
  - Onboarding 同步：同样的"自定义服务商"大卡片 → "添加提供商 ›"链接；`openNewCustom()` 唤起 `ProviderForm`。
  - 已配置服务商（`ProviderCards`）成为视觉主体；新建 / 编辑 / 删除 / 模型选择能力完全保留。
  - E2E 测试 `custom-provider.spec.ts` 与 `settings.spec.ts` 同步更新（点击 "Add provider" 链接再展开表单）。
- `apps/desktop/tests/e2e/nav-collapse.spec.ts`: 注释与 persona 名称同步。

### 变更 (Changed)

- **仓库治理体系**


### 变更 (Changed)

- **Provider 路由与凭据模型**
  - Provider 从厂商名称绑定收敛为仅按 OpenAI-compatible 或 Anthropic Messages 协议和 endpoint/profile 路由。
  - OpenAI 与 Anthropic 支持显式 API 地址和对应凭据。
  - 自定义 endpoint 不再继承无关官方服务的 API Key，避免不同服务之间的凭据串用。
  - 本地模型服务、Gemini-compatible 网关等通过自定义 OpenAI-compatible endpoint 接入，不再拥有独立运行时分支。

- **STT 网络与依赖栈**
  - 升级 `ureq`、`sha2`、`cpal` 等 Rust 依赖并完成 API 兼容适配。
  - 保留模型下载取消、超时保护、代理回退和 SHA-256 校验能力。
  - Rust 最低工具链要求随相关依赖升级同步调整。

- **依赖治理**
  - 调整 Python、npm、Rust 和 GitHub Actions 的 Dependabot 策略。
  - 生产依赖继续独立评估，开发依赖可按规则分组。
  - Major 升级和组合升级保持人工审核，避免自动升级掩盖兼容性问题。

- **仓库维护规则**
  - `CONTRIBUTING.md`、`UPSTREAM.md`、架构、治理和运维文档与当前仓库结构重新对齐。
  - 收紧历史路径检查，旧路径仅允许存在于必要历史记录。
  - 品牌资源检查脚本和开发环境说明按当前目录职责重新整理。


### 移除 (Removed)

- **不支持的 Provider 实现**
  - 移除原生 Gemini、AWS Bedrock、Google Vertex 与 Ollama 独立 Provider/协议，以及对应 SDK 依赖、配置和模型目录。
  - Pyright 改为零错误直接门禁，不再使用 errorCount baseline。


### 修复 (Fixed)

- **非 ASCII Artifact 路径**
  - 修复 Markdown Artifact 链接经过 URI 编码后，中文等非 ASCII 文件名无法正确匹配、打开的问题。
  - 非法转义或文件名中的普通 `%` 保持兼容处理。

- **Python 依赖冲突**
  - 调整 Provider SDK 与 `websockets` 等依赖版本约束，解决不同 SDK 对依赖版本要求不一致造成的 CI 安装失败。

- **STT 兼容性**
  - 修复 `cpal` 新版本下麦克风采集接口变化导致的编译问题。
  - 修复 `sha2` 新版本摘要结果不再支持旧格式化方式的问题。


## [0.3.0] - 2026-08-31

### 新增 (Added)

- **工具按需注入**
  - 模型调用不再默认携带全部 Tool Schema。
  - 根据 persona、当前消息和本轮工具使用情况选择必要工具，降低请求体和上下文开销。
  - 核心人在回路、计划和 Skill 能力始终保留。
  - 当工具选择不足时可自动回退到完整工具集，避免工具裁剪导致任务中断。

- **请求可观测性**
  - 增加模型请求日志，记录 Provider、Model、消息规模、Tool 数量、请求体积、上下文估算、TTFT、总耗时和调用结果等关键指标。
  - 观测记录失败不会影响正常模型调用。

- **Endpoint 能力画像**
  - OpenAI-compatible endpoint 不再默认假设支持全部标准参数。
  - 支持记录不同 endpoint 对 `stream_options`、reasoning、并行 Tool Call 和上下文窗口等能力。
  - 对已经确认不支持的参数，后续请求可主动跳过，减少重复失败和无意义重试。

- **工具 Schema 上下文预算**
  - 上下文预算开始同时考虑 Messages 与 Tool Schema。
  - 接近上下文上限时优先缩减工具集合，再根据需要触发消息压缩。


### 变更 (Changed)

- **模型调用负载**
  - 模型调用从“每轮完整 Tool Schema”调整为按任务需要加载，降低无关上下文占用。
  - Explorer 等需要完整工具能力的执行路径仍可保持完整注入。

- **兼容端点参数处理**
  - 参数兼容策略从单纯失败后重试，调整为“显式配置 + 已学习能力 + 失败兜底”的组合方式。


## [0.2.2] - 2026-08-27

### 新增 (Added)

- **Execution Gateway**
  - 建立统一的工具执行安全入口。
  - 对本地写入、外部副作用、高风险操作实施分级审批。
  - 高风险外部操作不再能够通过普通 session 授权或 blanket permission 绕过审批。
  - 无人值守任务在无法获得必要授权时按 fail-closed 处理。

- **资源范围守卫**
  - 带磁盘副作用的工具调用会在执行入口重新验证可信根目录和可写范围。
  - 越界写入不会因上游分类或已有授权而自动放行。

- **Run Event Ledger**
  - 后台进程启动、终止等生命周期事件进入运行记录。
  - 运行记录增加更明确的执行隔离信息，便于后续审计和复盘。

- **Source Layer v1**
  - 文件来源成为可持久化、可追踪的来源记录。
  - 通过内容指纹识别来源变化、缺失和重复捕获。
  - 为后续引用、证据链和来源新鲜度检查建立基础。

- **敏感数据统一脱敏**
  - Audit 与 Run Ledger 共用统一的递归脱敏策略。
  - API Key、Token、Authorization、Cookie、签名参数和敏感正文等信息统一处理。


### 变更 (Changed)

- **Provider 设置体验**
  - 服务商设置按照路由标识、协议、API 地址、API Key、连接测试的顺序重新组织。
  - API 地址从隐藏的高级配置调整为主要配置项。
  - 模型列表、默认模型、显示状态、手动添加模型和危险操作重新整理。
  - 默认模型不能被直接隐藏或删除，避免产生无效配置。

- **RuntimePort 架构**
  - Server 业务层逐步退出对具体 `TurnEngine` 实现的直接依赖。
  - Session 和 Automation 等路径统一通过 Runtime Port 驱动运行时能力。

- **SessionManager 拆分**
  - 原大型 SessionManager 按 Workspace、Session、Event、Connector、Provider、Automation、Artifact 等职责拆分。
  - 对外接口保持兼容。

- **性能边界**
  - 大文件读取避免重复全文件扫描。
  - PDF 预览改为按视口虚拟化。
  - 用户停止生成时主动关闭上游 Provider Stream，减少无效后台下载。

- **自动更新入口**
  - 在正式 updater feed 尚未具备完整发布链路时隐藏无效的自动更新入口。


### 修复 (Fixed)

- **流式响应截断**
  - OpenAI-compatible 流式响应如果在没有合法完成标记的情况下结束，将明确判定为截断。
  - 不再把只收到少量字符的残缺响应误认为完整回答。
  - 用户主动停止生成不会被误判为服务端截断。

- **模型发送按钮**
  - 修复发送按钮与回车发送条件不一致导致按钮无法点击的问题。

- **Excel 日期预览**
  - XLSX 预览能够识别 Excel 日期、时间和日期时间格式。
  - 日期型单元格不再显示原始 Excel 序列号。

- **Provider 默认模型一致性**
  - 删除当前默认模型时改为明确拒绝，避免默认模型指向不存在的配置。

- **安全授权边界**
  - 修正外部消息发送等 L3 操作的 standing rule 和审批行为，避免 session 级授权错误扩散到高风险操作。


### 安全 (Security)

- **Sidecar Token 隔离**
  - Sidecar 根 Token 从 WebView 渲染进程退出。
  - Tauri 本地反向代理负责 REST Header 和 WebSocket 子协议注入。
  - Web 内容不再直接持有 Sidecar Token。

- **HTML Artifact 隔离**
  - HTML Artifact 预览使用更严格的 sandbox。
  - 关闭暴露给页面脚本的全局 Tauri Bridge，降低不受信 Artifact 影响桌面权限的风险。

- **ACL 降级检测**
  - SecretStore 和 Workspace Trust 写入后验证实际文件权限。
  - ACL 保护失败时产生显式降级标记和警告，而不是静默继续。

- **CI 供应链硬化**
  - 第三方 GitHub Actions 固定到不可变 Commit SHA。
  - 增加 Python、npm 和 Rust 依赖安全公告检查。
  - Ruff 和 Pyright 建立静态分析回归门，防止新增技术债。


## [0.2.1] - 2026-08-24

### 变更 (Changed)

- **GitHub Actions 发布链路**
  - Windows Portable Release 收敛为从 `main` 手动触发。
  - 发布前统一检查 Python、npm、Tauri、Portable Launcher 和 CHANGELOG 版本一致性。
  - Release 先创建 Draft，上传资产并重新下载验证后再正式发布。
  - 重复执行可继续未发布 Draft；版本、Tag 或 Commit 冲突时安全终止。

- **发布版本统一**
  - Python、Desktop、Tauri 和 Portable Launcher 的版本号进入统一发布检查流程。


### 修复 (Fixed)

- **异步事件循环阻塞**
  - OAuth Token 刷新、Telegram 等同步网络操作移出主异步事件循环，避免网络异常导致整个 Server 暂停响应。

- **Automation 重叠执行**
  - 修正定时任务 overlap guard 在并发边界下可能同时派生重复任务的问题。


### 移除 (Removed)

- **旧 MSVC Portable 构建脚本**
  - 删除包含本机绝对路径且已经被当前 Portable 构建流程取代的旧辅助脚本。


## [0.2.0] - 2026-08-24

### 新增 (Added)

- **Windows Portable 发布基线**
  - 建立以 `Delta-Windows-Portable.zip` 和 SHA-256 文件为核心的 Windows Portable 发布方式。
  - Portable 数据保存在应用目录下，可整体移动，不依赖固定安装路径。
  - ZIP 统一使用单个 `Delta/` 顶层目录。
  - 构建过程增加绝对路径泄漏扫描和实际启动 Smoke Test。

- **UI Runtime Contract v1**
  - 建立 Desktop UI 与 Server 之间的核心 DTO 和事件契约。
  - Session 与应用级 WebSocket 事件统一使用版本化包络。
  - Health、HTTP Error 和核心 Runtime Event 建立明确兼容边界。
  - WebSocket 异常断开支持自动重连，并通过 sequence 抑制重复事件。

- **会话级思考深度**
  - 每个 Session 可以独立设置默认、低、高、最大等 reasoning effort。
  - 设置持久化，并在下一次模型调用中实际生效。

- **自定义模型服务商**
  - 模型设置支持用户自行添加 Provider。
  - 可以选择兼容协议、配置 API 地址与 API Key，并拉取可用模型。
  - 自定义 Provider 配置可以持久化并参与正常模型路由。

- **消息编辑与撤回**
  - 已发送的人类消息可以编辑。
  - 编辑时回退该消息之后的对话历史，并允许重新发送。

- **多语言本地语音输入**
  - 本地 Whisper 模型由英语专用版本切换为多语言版本。
  - 支持自动识别中文等非英语语音。


### 变更 (Changed)

- **无预设模型**
  - Delta 不再内置默认 Provider 或默认 Model。
  - 首次使用由用户自行配置模型服务商和默认模型。

- **Provider 设置**
  - 自定义 Provider 成为模型配置的主要入口。
  - Provider Alias、协议、API 地址和模型列表的职责进一步明确。

- **默认工作区**
  - 默认受信任工作区从旧 OpenWorker 路径调整为 Delta 自有路径。
  - 不对既有用户数据进行自动迁移。

- **内部 Server 命名**
  - Desktop Sidecar 进程和发布产物逐步统一为 `delta-server`。
  - 为兼容既有 Cloud/Runtime 协议，部分 OpenWorker/Coworker 协议标识暂时保留。

- **Windows Portable Launcher**
  - 根 `Delta.exe` 收敛为 Bootstrapper。
  - 负责环境和数据目录初始化后启动 Desktop，GUI 与 Sidecar 生命周期继续由 Desktop 管理。

- **主题与界面**
  - 原生窗口标题栏跟随应用明暗主题。
  - 调整侧边栏、搜索入口、Composer、模型设置、消息元数据和空状态等桌面交互。
  - 完善中文界面与 i18n 文案。


### 修复 (Fixed)

- **Portable PowerShell 构建**
  - 修复 PowerShell 5.1 将 PyInstaller/Tauri 输出到 stderr 的正常日志误判为构建失败的问题。

- **Portable 路径兼容**
  - 修复 Windows `AppData` 等路径可能被搜索排除规则误判的问题。
  - Portable 在空格、中文和特殊字符路径下保持可运行。

- **聊天布局**
  - 修复长文本、URL、代码块和表格导致主聊天区域被撑宽的问题。
  - 代码块和宽表格仅在自身区域横向滚动。

- **Reasoning 状态持久化**
  - 修复会话重载或 WebSocket 重连后 reasoning effort 回到默认值的问题。

- **附件处理**
  - Picker、拖放和粘贴统一附件验证规则。
  - 对类型不支持、大小超限、数量超限、重复和读取失败给出明确反馈。

- **Provider 错误展示**
  - 可重试 Provider 错误改为更清晰的本地化摘要。
  - 原始基础设施错误默认折叠但仍可查看和复制。

- **模型与设置交互**
  - 修复自定义 Provider 表单加载、深色模式下 Select 可读性、Provider 状态展示和相关文案问题。

- **Desktop 原生交互**
  - 修复窗口拖拽区抢占按钮事件导致侧栏、搜索等操作无法点击的问题。
  - 修复原生标题栏首次显示时可能短暂出现错误主题的问题。

- **可访问性**
  - 调整浅色与深色主题中的次要文本颜色，使核心界面对比度达到 WCAG AA 要求。


### 移除 (Removed)

- **旧发布链路**
  - 移除不再使用的 MSI、NSIS、macOS DMG 和旧 updater 发布脚本及相关资产。
  - 当前发布链路收敛到 Windows Portable。

- **旧设计和构建资产**
  - 清理未使用的 UI 原型、重复 Logo、旧构建日志、缓存和中间产物。
