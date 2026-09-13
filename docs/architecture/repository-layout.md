# 仓库目录规范

Delta 按系统职责组织代码。

> 本文只描述**当前有效的物理仓库结构与命名规则**。长期逻辑模块和目标依赖方向见 [`target-architecture.md`](target-architecture.md)。

Experience / Runtime / Trust / Work / Capability / Automation / Learning 是逻辑职责，不要求机械创建同名顶层目录、crate 或进程。

## 命名总原则

> **独立边界用 `delta-*`，内部职责用语义名；品牌化用于识别产品边界，不用于包装每一个内部模块。**

`delta-*` 表示“这是 Delta 的一个独立边界”；无品牌前缀的语义名表示“这是边界内部的职责”。

### 可以使用 `delta-*` 的情况

只有形成明确独立边界时才使用，例如满足至少一项：

- 独立可执行程序；
- 独立 crate / package，且存在明确公共 API；
- 独立版本兼容边界；
- 独立跨语言协议；
- 独立 SDK；
- 独立发布 / 分发单元。

推荐命名：

| 层级 | 建议名称 | 用途 |
| --- | --- | --- |
| 桌面主程序 | `Delta` / `Delta.exe` | 用户直接运行 |
| Rust 核心 crate | `delta-runtime` | 核心 Runtime 独立边界 |
| IPC / 公共协议 | `delta-protocol` | TS ↔ Rust contract / event / DTO |
| Capability SDK / ABI | `delta-capability` | Capability contract / manifest / request / result |
| Worker 启动器 | `delta-worker` | Rust 管理的 Worker runner |
| Python Worker SDK | `delta-worker-py` | 仅在形成独立 Python SDK 时存在 |
| CLI | `delta-cli` | 仅在保留独立 CLI 产品边界时存在 |
| 测试工具 | `delta-testkit` | 仅在形成稳定公共测试工具时存在 |
| TS workspace package | `@delta/*` | 仅在真正拆成独立 package 时使用 |

### 内部职责不品牌化

以下内部职责保持语义名：

```text
runtime/
  session/
  run/
  context/
  provider/
  tool/

trust/
  policy/
  approval/
  ledger/
  secrets/

work/
  source/
  citation/
  artifact/
  validation/

learning/
  memory/
  experience/
  skill/
```

除非未来真实形成独立发布、版本、协议或进程边界，否则不要创建：

```text
delta-trust
delta-work
delta-learning
delta-approval
delta-artifact
```

## 当前顶层目录

| 目录 | 当前职责 |
| --- | --- |
| `apps/` | 用户可运行应用 |
| `core/` | 当前 Runtime / Agent glue 与 Rust Runtime 实现 |
| `providers/` | 当前 Provider compatibility / profile surface |
| `integrations/` | Connector、MCP、Skill、Tool、Web 等外部能力 |
| `services/` | 尚未完全退出的后台兼容服务及独立能力 |
| `packages/` | 跨模块基础能力和迁移 bridge |
| `resources/` | 品牌、截图等非代码资源 |
| `packaging/` | 构建、打包、发布 |
| `tests/` | 跨语言与兼容测试 |
| `docs/` | 产品、架构、治理、审计、运维文档 |
| `scripts/` | 仓库维护和验证脚本 |
| `.github/` | GitHub 配置与自动化 |

## `apps/desktop/`

当前 Desktop 已通过 Tauri Commands / Events 直接连接 in-process Rust RuntimeHost。PR #203 已移除 Tauri 主路径对 Python `delta-server` sidecar、localhost reverse proxy 和 bundled sidecar resource 的依赖。

浏览器开发和部分兼容路径仍可能使用 Python server；这不再是 Desktop 主产品路径。

TypeScript 只负责 Experience / UI，不拥有业务 Authority。

## `core/`

`core/runtime-native/` 是当前 Rust Runtime 实现和 trusted authority 的主要承载位置。

R6 期间：

- 新控制面默认进入 Rust；
- Python `TurnEngine` / application glue 属于迁移对象；
- Python Office / Research / Media 专业能力优先 Worker 化；
- 调用方完成迁移后删除 facade，不长期保留纯转发链。

物理拆 crate 以真实边界为依据，不为了目标架构图提前拆分。

## `providers/`

模型边界固定为：

```text
OpenAI-compatible
Anthropic-compatible
```

R6 目标是删除不再需要的 Python Provider control / SDK fallback，而不是扩展更多 Provider family。复杂多 Provider 调度外置给兼容 Endpoint / AI Gateway。

## `integrations/`

长期属于 Capability / Automation 边界。

约束：

- Integration 不拥有 Runtime / Trust / Work Authority；
- Skill 不直接获得额外权限；
- MCP / Connector 不绕过 Policy / Approval / Ledger / Validation；
- Python capability 通过受控 Capability ABI 使用。

## `services/`

`services/server/` 仍保留 Python application compatibility surface，但不再是 Tauri Desktop 主 Runtime 入口。它属于 R6 后续迁移和删除对象。

`services/stt/` 属于可独立隔离的 Capability；是否独立进程取决于真实资源与生态需求。

## `packages/`

只保存真正跨模块共享且职责明确的基础能力和迁移 bridge。不得成为 `common/` / `utils/` 式收容目录。

## `docs/`

```text
docs/
├── DELTA_BLUEPRINT.md
├── architecture/
├── audits/
├── governance/
└── operations/
```

权威入口：

- 产品：`DELTA_BLUEPRINT.md`；
- 目标架构：`architecture/target-architecture.md`；
- 当前 Runtime contract：`architecture/runtime-public-contract.md`；
- Capability ABI：`architecture/capability-abi.md`；
- 当前结构与命名：本文；
- R6 当前执行：`governance/r6-execution-plan.md`；
- 历史决策：`architecture/adr/`；
- 审计证据：`audits/`。

已完成且不再定义当前架构的一次性迁移说明不留在 active architecture 目录；历史通过 Git / PR / ADR / CHANGELOG 追溯。

## 目标依赖方向

```text
Experience (TypeScript)
        ↓
Runtime (Rust)
   ┌────┼────┐
   ▼    ▼    ▼
 Trust Work Capability
   │           │
   ├──────┐    ▼
   ▼      ▼  Worker / External
Automation Learning
```

## 禁止模糊目录

原则上不得新增：

```text
common/
misc/
helpers/
utils/
shared/
base/
legacy/
```

如果代码无法归类，先检查职责和 Authority，而不是先造新容器。

## 目录 / 命名变更

以下视为架构治理变更：

- 新增 / 删除顶层目录；
- 新增独立 `delta-*` crate / package / executable；
- 模块职责迁移；
- 关键依赖方向变化；
- Python Backend 物理退出；
- 新增长期 Worker 根目录；
- 大规模路径调整。

必须说明：为什么是独立边界、Authority 属于谁、兼容与迁移方式、CI / 文档如何同步。

## 当前结构优先

本文只记录当前有效物理事实与命名规则。目标结构不能通过提前建空目录或大量 `delta-*` 包装伪装成已实现。