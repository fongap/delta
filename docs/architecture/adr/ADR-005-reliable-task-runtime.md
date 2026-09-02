# ADR-005 Reliable Task Runtime

**Status:** Active

## Context

Delta 7.1 "Reliable" 阶段（`docs/DELTA_BLUEPRINT.md`）要求把已有能力收敛成统一、可验证、可恢复的工作闭环。基础设施已经具备：

- `RuntimePort` / `TurnEngineAdapter` — 应用程序与运行时的窄边界（`core/runtime.py:33`）
- `RunEventLedger` — 哈希链式、追加式事实记录（`core/ledger.py:35`，ADR-001）
- `TaskStore` / `ScheduledTask` / `TaskRun` — 自动化持久化（`core/automation/models.py`）
- `PermissionEngine` + `Gateway` + `RiskLevel` — 风险分类与执行门控（`core/permissions.py`，`core/gateway.py`，ADR-002）
- `AuditStore` — 工具/审批叙述（`core/audit.py`）
- `runscope` — 活动 run 的环境标识（`core/runscope.py`）
- `Inbox` — 待处理事项入口

**当前缺口**（7.1 阶段不可妥协）：

1. **没有验证门控** — 无论结果如何，`run.status = "ok"`。"Validation 未通过 → Task 不得进入成功状态" 结构性不可实现。
2. **Artifact 是 `list[str]`，按 mtime 猜测** — 没有 sha256/id 引用，没有不完整写入检测（"Artifact 写入不完整 → 不误报完成" 不可实现）。
3. **两套历史系统并存** — `RunEventLedger` 仅记录 `run.*` 与进程事件；`AuditStore` 保留工具/审批叙述；`request_log` 单独追踪模型调用。"Run 关键执行可回放" 需要缝合多个存储。
4. **Durable Resume 仅覆盖 prompt 挂起，不覆盖副作用一致性** — 副作用完成后、结果消息持久化前崩溃 → `resume()` 会重放该调用（"Tool 已产生副作用但 Run 未结束 → 恢复后不重复执行" 不可实现）。
5. **中途中断的 run 不会重新出现** — 冷启动后 `recover_stale()` 仅标记 `run.interrupted`，但 Inbox 与 UI 不会向用户呈现需要 resume 的 run。
6. **没有结构化的 Task / Run 身份统一** — 自动化 `TaskRun.run_id` 与每轮生成的 uuid4 没有 join key；TaskStore、Ledger、session transcript 三者记录的是同一次运行但身份不同。

## Decision

### WS1 — Ledger 词汇表扩展（ADR-001 增量）

在 `RunEventLedger` 中新增事件类型并把工具/审批叙述接入：

- `tool.proposed` / `tool.started` / `tool.finished` / `tool.denied`（来自现有 `audit_sink`）
- `approval.requested` / `approval.granted` / `approval.denied`
- `artifact.registered` / `artifact.completed`（sha256、size、kind）
- `validation.started` / `validation.passed` / `validation.failed`
- `side_effect.committed` / `side_effect.replayed`
- `run.resumed`

实现：`core/ledger.py` 增加 `KNOWN_EVENT_TYPES` 集合；`core/engine.py` `_audit` 方法同时写 ledger 与 audit；`AuditStore` 降级为旧数据访问层（保留以保持向后兼容）。

### WS2 — Artifact 领域对象

新增 `core/artifact.py`：

- `Artifact` dataclass：`path`、`name`、`kind`、`size`、`sha256`、`modified_at`、`run_id`、`incomplete: bool`
- 注册时机：写文件工具的 `_audit("finished", status="ok")` 后立即计算 sha256 并注册到 ledger（`artifact.registered`）
- 不完整检测：文件存在但 sha256 计算抛 IO 错误 → 标记 `incomplete=True` → `artifact.completed` 推迟
- `TaskRun.artifacts: list[str]` 替换为 `list[Artifact]`，所有读取路径迁移

### WS3 — 验证门控

新增 `core/validation.py`：

- `ValidationCriteria`：`{"min_rows": int, "must_contain": list[str], "schema": dict, ...}`
- `ValidationResult`：`{"ok": bool, "checks": list[dict], "evidence": dict}`
- 任务提交时声明 criteria；run 结束时 `TaskRun.status` 由 `validation.passed` 事件决定
- 失败时 `status = "validation_failed"`，与 `error` 区别
- Inbox 不允许关闭未通过验证的 run

### WS4 — 副作用安全的 Durable Resume

新增 `core/idemlog.py`：

- `IdempotencyLog` — 每次产生副作用的工具调用（写文件、shell、网络请求、审批执行）在 commit 前记录 `(run_id, tool_call_id, sha256(args))`
- `resume()` 在重启后回放前：检查幂等日志，已 commit 的调用跳过执行，直接用持久化的 result
- `recover_stale()` 同时把未 commit 但已记录副作用的 run 标记为 `side_effect.uncommitted`，并把这种 run 加入 Inbox 的 "需要 resume" 列表
- 实现工具层：`_audit("started", status="ok")` + `side_effect.committed` 写入幂等日志

### WS5 — 收敛到单一 Task / Run 身份

- `TurnEngineAdapter._track` 接受外部 `run_id`（可选），自动化路径显式传入 `TaskRun.run_id`，交互路径继续使用 uuid4 但在 ledger 落盘时与 session_id 关联
- `RunEventLedger.events(run_id, *, session_id=None)` 增加 session_id 过滤
- `AuditStore.list` 与 `RunEventLedger.events` 都能通过 run_id 互相跳转

## Consequences

- **正面**：6 个缺口全部得到结构化处理；7.1 阶段验收条件可执行；副作用安全 + 不完整 Artifact 检测 + 验证门控 = run 可以宣告"完成"
- **正面**：ledger 成为 run 叙述的唯一事实来源；AuditStore 变成派生视图
- **代价**：现有 `TaskRun.artifacts: list[str]` 接口变更；所有读取 `artifacts` 字段的代码迁移
- **代价**：幂等日志引入每次副作用调用的额外写开销（仅对 L1+ 写，不影响只读路径）
- **约束**：不变更现有的 `RuntimePort` 接口；新增能力以可选参数形式注入到 `build_engine`
- **约束**：ADR-001 的哈希链契约不变；新增事件类型必须可被 `verify()` 验证
- **约束**：Inbox 不被扩张为消息中心；"需要 resume" 的 run 复用 Inbox 现有审批/问题事件通道
