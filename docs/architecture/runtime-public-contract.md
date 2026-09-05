# Runtime Public Contract

> 本文档规定 v0.3.2 之后，Delta Python Runtime（`core/` + `services/server/`）对外**不可随意变动**的稳定契约。
> 当 Rust Core 迁移到来时，权威写入者由 Python 改为 Rust，但本契约的字段名、事件类型、HTTP 端点语义必须保持稳定。
> 任何破坏性变更必须先经 ADR。

## 1. 范围

### 包含

- `core/idemlog.py` 公开 API；
- `core/recovery.py` 公开 API；
- `core/artifact.py` 公开 API；
- `core/validation.py` 公开 API；
- `core/sources.py` 公开 API；
- `core/analyzer.py` 公开 query；
- `core/ledger.py` 事件类型集合；
- `services/server/{manager.py, app.py, manager_*.py}` 对外 HTTP 端点。

### 不包含

- 内部实现细节（如 sqlite schema column 顺序）；
- 私有方法（下划线前缀）；
- 测试 fixtures 字段；
- 文档 markdown 排版。

## 2. Core 领域对象

### 2.1 Task

- 身份：`ScheduledTask.id` (string)；
- 名称 / 描述 / 触发方式（`schedule` 或 `trigger` 二选一）；
- 关联 `agent="code"|"cowork"`；
- 关联 `validation_criteria: ValidationCriteria`。

### 2.2 Run

- 身份：`run_id` (string, uuid)；
- 与 Task 一一对应或由交互路径独立生成；
- 状态机：见 §2.4 SideEffect 状态 + ledger events。

### 2.3 RunEvent (Ledger)

- 来源：`core/ledger.py` `KNOWN_EVENT_TYPES` 固定集合；
- 字段：`run_id` / `type` / `seq` / `ts` / `actor` / `payload` / `workspace`；
- 已知类型：
  - `tool.proposed` / `tool.started` / `tool.finished` / `tool.denied`
  - `approval.requested` / `approval.granted` / `approval.denied`
  - `artifact.registered` / `artifact.completed`
  - `validation.passed` / `validation.failed`
  - `side_effect.planned` / `side_effect.committed` / `side_effect.failed` / `side_effect.uncertain` / `side_effect.uncommitted`（legacy alias）
  - `run.started` / `run.resumed` / `run.completed` / `run.failed`

> `run.resumed` 与 `run.started` 必须明确区分：resume 路径**不得**发 `run.started`。

### 2.4 SideEffect

- 状态机（`core/idemlog.py` `SideEffectState`）：
  - `Planned` → `Executing` → `Committed` | `Failed` | `Uncertain`；
- 操作身份：`operation_id(run_id, tool_call_id) = sha256(...).hexdigest()`；
- 查询：
  - `uncommitted_for_run(run_id)`；
  - `uncertain_for_run(run_id)`；
  - `committed_for_run(run_id)`；
- Uncertain 一经写盘，**不得**由 engine 自动 replay，必须经 `POST /v1/runs/{run_id}/side-effects/{tool_call_id}/resolve`。

### 2.5 Artifact

- 显式注册：`register_artifact(workspace, path, run_id, ledger, kind_classifier)`；
- 写工具（`write_file` / `replace_in_file` / `apply_patch` / `apply_unified_diff`）commit 后由 engine 自动调用一次；
- 字段：`path` / `name` / `kind` / `size` / `sha256` / `run_id` / `incomplete` / `registered_at`。

### 2.6 Validation

- 入口：`run_validation(artifacts, criteria, *, workspace, valid_citation_count)`；
- 字段（`ValidationCriteria`）：
  - `min_artifacts` / `max_artifacts`；
  - `required_paths` / `required_substrings`；
  - `min_size` / `max_size`；
  - `require_complete`；
  - `csv_required_headers`；
  - `require_citations` / `min_valid_citations`（v0.3.2 新增）。
- 失败结果以 `validation.failed` 入 ledger，任务状态变为 `validation_failed`。

### 2.7 Recovery

- 快照：`RecoveryStore(base / "recovery-snapshots.json")`；
- 写入时机：4 个 Inbox asker（approval / question / directory / plan）pending 时；
- 字段：`schema` / `snapshot_at` / `run_id` / `session_id` / `phase` / `pending_tool_call` / `pending_inbox_item_id` / `last_event_seq` / `todo_summary` / `recent_artifacts` / `error`；
- 契约：snapshot 是 **advisory**，engine resume 暂不读它。

### 2.8 Source / Citation

- `core/sources.py` `Source` / `SourceStore` / `CitationRange`；
- 5 类 validity：`valid` / `content_changed` / `out_of_bounds` / `file_missing` / `source_gone`；
- `_VALIDITY_RANK` 决定跨 range hit 时的 worst-reason roll-up；
- `core/analyzer.py` `source_citation_hits(source_id)` 按 source 维度计数（不接受 `workspace` / `run_id` 形参）；
- per-run 有效引用计数由 `services/server/manager_automations.py` `_count_valid_citations` 提供（迭代 `src.all()` + 过滤 `run_id`）。

## 3. HTTP 端点

> 路径与字段名是契约的一部分；JSON 字段顺序不是。

| 路径 | 方法 | 用途 |
|---|---|---|
| `/v1/runs/{run_id}/side-effects` | GET | 列出该 run 所有 side effect（含 uncommitted / uncertain / committed） |
| `/v1/runs/{run_id}/side-effects/{tool_call_id}/resolve` | POST | 用户/Operator 显式解决 uncertain |
| `/v1/runs/{run_id}/detail` | GET | 聚合 timeline / artifacts / validation / side_effects / citations / recovery |
| `/v1/subscriptions` | GET | 列出当前可订阅的事件源 |

## 4. 不变量

1. **一份 run_id 贯穿五处**：TaskStore / Ledger / Artifact / Validation / IdemLog / Source store 全部使用同一 `run_id`。
2. **Worker 不得直接修改 core 状态**（来自 ADR-009 §2）：Capability Worker 只能输出 staging，由 Rust Core（迁移前是 Python Server）做正式登记。
3. **不确定副作用不自动 replay**：`Uncertain` 状态需 Operator 显式 `resolve`。
4. **恢复边界 = 已提交事件**：`run.resumed` 不会发在比 `last_event_seq` 更早的位置。
5. **mtime fast path 是启发式**：per-citation 强校验由 SHA256 验证承担；mtime 仅是性能捷径。

## 5. 变更流程

- **非破坏性**（新增字段 / 新增端点 / 新增事件类型）：直接 PR，但必须在 changelog 列出。
- **破坏性**（删除字段 / 改字段类型 / 改状态机语义）：先写 ADR 改本文件，再写实现。
- **跨领域**（如 SideEffect 状态机变化影响 Inbox）：先在 §2 列对照表里更新接口，再实现。
