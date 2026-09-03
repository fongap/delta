# ADR-008 — P1 / P2 / P3 阶段基线说明

- 状态: Active
- 日期: 2026-09-04
- 阶段: §7.1 / §7.2 / §7.3 阶段第一刀完成后

## 背景

按 `DELTA_BLUEPRINT.md` §7.1/§7.2/§7.3 三阶段路径，从 §10.6 第 1 步开始，按 PR 一个一个落地，到本 ADR 落笔时已经合并 12 个 PR（P1 一刀 / P2 源码 + Inbox / P3 Analyzer + workspace 列 + 路径过滤 + Source 完整能力前两件 + Recovery + 条件触发）。

这一刀不是新变更——它是"做完一段，回头看"的快照。下一个接手 agent 应当从此 ADR 知道：

1. 哪些路径已经完成、什么时候做的、为什么做
2. 哪些路径蓝图里写明要延后、为什么延后
3. 下一刀不应该是"再做一个 PR"，而是"在真实使用里验证基线"

## 第一刀落地点（按 §7.x → PR → ADR 三列对照）

| 蓝图标号 | 落地点 | PR | 配套 ADR | 关键设计契约 |
|---|---|---|---|---|
| §7.1 Reliable | Reliable Task Runtime (Ledger 词汇 / Artifact / Validation / IdemLog / 单 run id / Reference Task e2e) | #75 | ADR-005 | 一份 run id 贯穿五处 (TaskStore / Ledger / Artifact / Validation / IdemLog); Side-effect resume 幂等 (args_sha256 + result) |
| §7.2 Source/Citation | CitationRange schema + read_file auto-cite | #76 | ADR-006 | typed locator (lines / page / cells / row / column / sheet / message_id / custom); 失败静默吞 (审计 hook 不能拖垮 read) |
| §7.2 Source/Citation | read_document (PDF / XLSX / DOCX) + source_store/run_id 注入运行时 | #77 | ADR-006 | 三格式共用 core/citation.cite chokepoint; 摘要 view 不写 citation (只在真读到位置时落) |
| §7.2 Source/Citation | Cowork/Ops multi-root read_file cite | #82 | ADR-006 (续) | per-root cite (workspace= 按匹配根写入; 不是 primary); 单根路径不变 |
| §7.2 Source/Citation | scanned PDF image fallback | #83 | ADR-006 (续) | pypdf extract_text 空时 rasterize; cite 仍记 page; pypdfium2 缺失时 graceful degrade |
| §7.2 Inbox 收口 | Automation 异常入 Inbox (run_issue kind) | #87 | ADR-006 (续) | status != ok (error / validation_failed / skipped) 收口; run_id 幂等; session_id = run 自己的线程 |
| §7.3 只读 Analyzer | Analyzer + workspace boundary + 三大 query (timeline / automation_health / source_citation_hits) | #78 | ADR-007 | workspace 第一参数必填; 跨 workspace 抛 WorkspaceMismatchError; 读路径不写 |
| §10.6 step 1 | run_events + task_runs 加 workspace 列 + 复合索引 + 迁移 | #79 | ADR-007 | workspace 不进 hash basis (同 payload+actor+ts 跨 workspace 同 hash); 迁移幂等 (CREATE TABLE / ALTER TABLE try/except) |
| §10.6 step 2 | timeline_for_run 走 SQL 过滤 (workspace= 推到 SQL 走 idx_run_events_workspace) | #81 | ADR-007 (续) | workspace=None 走全 run (向后兼容); 跨 workspace 返回 [] 不抛错 (与"run 不存在"同形, 不泄露) |
| §7.3 §701 Source 完整能力 | per-citation validity (valid / content_changed / out_of_bounds / file_missing / source_gone) | #84 | ADR-006 (续) | 5 reason; status=missing 短路; line kind 有便宜 bound check; 跨 range hit 用 worst-reason roll-up |
| §7.3 §701 Source 完整能力 | mtime fast path (Source 索引失效) | #85 | ADR-006 (续) | capture 记 mtime_ns + size_bytes; check_freshness 三阶检查 (stat → 缓存命中 → sha256); 0 read_bytes 命中; legacy ref 兜底 |
| §4.5 / §7.3 | minimal Recovery Context (10 字段快照 + 会话绑定 + schema 版本) | #86 | ADR-007 (续) | Advisory only (engine resume 暂不读); 旧行反序列化为 None; forward-compat schema 字段保留 |
| §7.3 §734 | 条件型 Automation 触发 (manual / filesystem / inbox) | #88 | ADR-007 (续) | schedule vs trigger 二选一; next_run=None 让 due() 跳过; cooldown + fingerprint 双层去重; 无第二套执行模型 |

## 累计工程基线（数字快照）

- 测试: 1417 passed / 4 skipped / 31 pre-existing mcp_oauth ImportError fails (与本路径无关)
- 新增模块: `core/automation/triggers.py` (313 行), `core/recovery.py` (317 行, PR #86)
- `core/analyzer.py` 从 0 行扩到 532 行 (3 大 query + workspace boundary)
- `core/inbox.py` 新增 1 个 kind + 2 个方法
- `core/sources.py` 新增 mtime 缓存 + per-citation 验证
- 13 个独立 ADR (`docs/architecture/adr/`)

## 蓝图 §8.9 / §7.3 明确**延后或条件限定**的项

| 项 | 原因 | 何时再做 |
|---|---|---|
| 自动 Reflection / Skill Evaluator / Failure Memory / 自动 Preference Promotion | §8.9 明确"不允许模型因为一次任务成功就自动改变长期行为"；必须先有 Candidate → Evidence → Evaluation → User Acceptance → Promotion → Use → Revocation 全链路 | 用户接受度建立后；典型场景: 用户手动维护 Preference 4 周后再说 |
| 完整 Source 语义检索 / 分块 / Source Index | §7.3 §701 明确"中期不以建设向量数据库为目标；只有当常规定位和检索已经明显成为瓶颈时，再引入更复杂的分块和 Retrieval" | 行号定位出现遗漏 / 错位 5% 以上的真实 case |
| Plan Critic | §7.3 §746 明确"只有当真实任务证明规划错误已经成为明显失败来源时，再引入独立 Plan Critic" | Reference Task 真实跑 4 周后, 失败原因 30%+ 来自规划错误 |
| Multi-Agent / Subagent | §7.3 §759 明确"只有当任务确实存在可并行子任务 / 独立审查需求 / 明确角色隔离 / 大规模上下文隔离时才扩大" + "Agent 数量不是能力指标" | 有用户真实需要并行子任务（如 "同时审三个 PR"） |
| 完整 Context 进一步深化 (Working/Source/Long-term/Archive 四件) | Recovery Context 是第一刀（已落），其余每件需触碰 compaction / runscope / recovery 多个协同点 | 真实使用中 long context compression 出现实际瓶颈 |

## 下一刀该是什么

**不是另一个 PR**。是：

1. 在 Reference Task (CSV / XLSX → 分析 → Markdown 报告) 上反复跑，至少积累 1 周的真实数据
2. 用 P3 Analyzer (本基线) 看 `automation_health` / `source_citation_hits` / `timeline_for_run` 是否真的能用——如果不能用，回到这一刀修，不向上加
3. 识别真痛点（用户问"上次 run 我 cite 的那段原文现在变了没有"→ 答得出来；用户问"昨天那批自动化哪些失败"→ 答得出来）
4. 真痛点 → 决定下一刀

## 不变

- 短中期冻结面 (§8.8)：Standing Approval / MCP / Subagent / Memory / Skill / Inbox / Self Wake 范围未扩
- 长期行为路径：模型行为 / Skill 权重 / 用户偏好 / Standing Rule / Task Schedule 均**未**自动调整；§7.3 "不允许模型因为一次任务成功就自动改变长期行为" 仍由 0 个写入路径守护
- §7.2 收口：所有 Automation 仍走同一 Task / Run Runtime（无论 schedule 还是 trigger）

## 决策记录

- **决策**: 把 P1/P2/P3 第一刀基线状态写成 ADR；CHANGELOG 顶层加一个表
- **理由**: 下一个接手 agent 不知道"现在在哪"。基线写明后, 任何"再加一个 PR"都必须先回答"为什么这个不在 ADR-008 的延后清单里"
- **后果**:
  - 正面：基线有 README, 不再是"读 12 个 PR 才能知道状态"
  - 风险：基线可能被读成"完成"——但实际只是"第一刀完成", §7.3 长期仍有大量空间
  - 缓解：本 ADR 顶部明确"第一刀" + "下一刀不是 PR, 是真实使用"