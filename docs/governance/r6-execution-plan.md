# R6 Execution Plan

> 本文是 Delta 当前唯一的 **R6 执行状态与阶段计划**。它不重新定义产品或目标架构。
>
> 产品边界见 `../DELTA_BLUEPRINT.md`；目标架构见 `../architecture/target-architecture.md`；长期开发与命名规则见 `development-policy.md`。

## 1. 当前状态

R6：**IN PROGRESS**。

R5.1 已完成 Runtime correctness 与 Human Control 基线。R6 的目标是把 persistent product control plane 收敛到 Rust，让 TypeScript 直接使用 Rust Runtime，同时把 Python / PowerShell / Shell 保留为受控 Worker / Script。

### 已落地

PR #203 已完成第一批关键 on-ramp：

- Rust `RuntimeHost` 进入 `core/runtime-native`；
- RuntimeHost 支持 `run / resume / retry / steer / follow-up / cancel / switch_model / truncate` 等核心动作；
- Runtime event 可通过稳定 envelope 传给前端；
- Tauri Desktop 嵌入 Rust Runtime，不再启动 Python `delta-server` sidecar 作为主路径；
- TypeScript 在 Tauri 模式通过 Commands / Events 直接连接 Rust；
- Python HTTP/WS 只保留为 browser / compatibility / remaining migration surface；
- R6 architecture guard 已进入 CI，当前仍处于迁移期模式。

### 仍待完成

- Python `SessionManager` / `manager_*` application orchestration；
- Python `TurnEngine` 与完整 agent loop authority；
- Python Provider Router / SDK compatibility fallback；
- Automation / Inbox / Connector / Skill / Memory 等 application wiring；
- Capability Workerization 与 `aisuite` runtime abstraction 清理；
- Python application backend 与相关 packaging 最终退出；
- architecture guard 切换为 hard enforcement。

## 2. R6 原则

R6 只解决两类问题：

1. **产品减法**：一个 Delta + Skills / Capabilities；
2. **控制面收敛**：核心 Runtime / Trust / Work / application authority 进入 Rust，TS 直接使用 Rust host。

R6 不与完整 Office、DOE、Media、Learning 大扩建绑定。

每个 Authority PR 必须写清：

```text
Authority Before
Authority After
Compatibility
Exit Condition
Failure / Rollback
Tests
```

一个领域只允许一个 Authority。迁移完成后删除旧 owner / forwarding path。

## 3. 阶段状态

| 阶段 | 状态 | 当前说明 |
| --- | --- | --- |
| R6.0 Baseline / Product Cleanup | Partial | 文档和产品边界已冻结；历史 Coding / Persona / compatibility surface 仍需清理 |
| R6.1 Rust Runtime Host | **Landed / Continue hardening** | #203 已建立真实 RuntimeHost；后续继续吸收 application / agent-loop authority |
| R6.2 TypeScript Direct IPC | **Landed for Tauri main path** | #203 已完成 Desktop direct IPC；browser compatibility 仍可走 Python |
| R6.3 Application Control Plane | Pending / Next | Session / Workspace / Settings / Secrets / Artifact / Automation / Connector 等 orchestration 迁 Rust |
| R6.4 Agent Loop / Provider Hard-Cut | Pending | Context / model / tool continuation / provider routing / SDK fallback 收口 |
| R6.5 Capability Workerization | Pending | Python / PS / Shell 统一进入 Rust-supervised Capability boundary |
| R6.6 Python Backend Removal | Pending | 删除 persistent FastAPI / SessionManager / TurnEngine / provider fallback / sidecar packaging |
| R6.7 Hard-Cut Audit | Pending | CI guard enforce，确认无 Python core authority / no forwarding main path |

## 4. 下一阶段：R6.3 Application Control Plane

建议顺序：

```text
Session / Workspace metadata
→ Settings / Secrets
→ Source / Artifact / Validation wiring
→ Approval / Inbox
→ Scheduler / Automation
→ Connector / MCP boundary
→ Skill / Memory application wiring
```

原则：

- 已有 Rust authority 的领域只迁 orchestration，不重写事实模型；
- 每迁一个领域，立即删除对应 Python owner / facade；
- 不为了拆层先创建大量 crate；
- Memory / Skill 先保持行为，不把完整 Learning promotion engine 混进本阶段。

Exit Condition：Python `SessionManager` 不再是 application control plane 总入口。

## 5. R6.4 Agent Loop / Provider Hard-Cut

迁移：

```text
context
→ model invocation
→ tool schema / selection
→ trust decision
→ tool execution
→ result continuation
→ compaction
→ retry / steering
```

同时完成：

- OpenAI-compatible / Anthropic-compatible message conversion；
- Python ProviderRouter decision 迁 Rust；
- 删除 Python OpenAI / Anthropic SDK compatibility fallback。

Exit Condition：正常 Desktop turn、tool call、streaming、retry、steer、cancel、resume 都由 Rust RuntimeHost 驱动；Python 不再调用模型 Provider。

## 6. R6.5 Capability Workerization

统一 Worker contract 至少包含：

```text
job id
workspace scope
input hashes
arguments
permission / network grants
timeout / cancel
progress
typed result
staged artifacts
stderr / diagnostics
```

Worker 不可直接写 core DB、Secrets、Run state、Approval 或 Artifact formal state。

Office / Research / Statistics / OCR / Media 专业 Python 能力可以保留，但必须通过 Capability ABI。

未来能力 namespace 可包括：

```text
research.statistics.*
research.doe.*
research.doe.sequential-*
office.*
content.graphic.*
content.image.*
content.video.*
```

## 7. R6.6 Python Backend Removal

删除 / 替换：

- persistent FastAPI / Uvicorn application server；
- `delta-server` application entry；
- Python SessionManager / TurnEngine authority；
- Python Provider SDK / Router control plane；
- localhost forwarding 主路径；
- 仅服务旧 Runtime 的 `aisuite` 依赖；
- 不再需要的 sidecar packaging / smoke。

Exit Condition：没有 Python application backend 时，Delta 核心 UI、Session、Run、模型调用、Tool orchestration、Automation 和 trusted work flow 仍能运行；只有明确调用 Python Worker 的任务才需要 Python worker runtime。

## 8. R6.7 Hard-Cut

最终检查：

```text
Core product = Rust + TypeScript
Python / PowerShell / Shell = controlled workers only
No persistent Python application backend
No TS → Python → Rust main path
No Python model-provider authority
No Python Run / Session / Policy / Artifact authority
No Coding product surface
```

R6 architecture guard 切换为永久 hard gate。

## 9. R6 期间不绑定的工作

原则上放到 Runtime hard-cut 后深化：

- 完整 Office 编辑器；
- 完整统计 / DOE / 序贯试验 / 研究报告能力；
- 图文 / 图片 / 视频生产链；
- 完整 Learning promotion engine；
- 新 Persona / Multi-Agent 平台；
- 第三模型协议；
- 新 Workflow Engine；
- 大规模 UI 重设计。

R6 期间只允许为迁移验收补 representative fixtures / capabilities。

## 10. 命名约束

R6 不因为“Rust 化”就把内部职责拆成大量 `delta-*` crate。

> **独立边界用 `delta-*`，内部职责用语义名。**

只有形成独立 API / protocol / version / executable / SDK / distribution boundary 时，才提升为 `delta-runtime`、`delta-protocol`、`delta-capability`、`delta-worker` 等独立名称。

## 11. 完成标准

R6 完成不以 Rust 行数、Python 文件数或 crate 数判断。

完成标准是：

```text
TypeScript Experience
        ↓
Rust Runtime Host
        ↓
Trust / Work / Capability / Automation / Learning
        ↓
Controlled Worker / External
```

并且三产品域现有真实工作不因迁移退化。