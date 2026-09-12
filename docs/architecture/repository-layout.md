# 仓库目录规范

Delta 按系统职责组织代码。

> 本文只描述**当前有效的物理仓库结构**。长期逻辑模块、语言职责和 R6 目标拓扑见 [`target-architecture.md`](target-architecture.md)。
>
> Experience / Runtime / Trust / Work / Capability / Automation / Learning 是逻辑职责，不要求当前立即创建同名顶层目录、crate 或进程。

目录名称应描述当前职责，不描述上游来源、历史阶段或兼容时代。

## 顶层目录

| 目录 | 当前职责 |
| --- | --- |
| `apps/` | 用户可直接运行的应用 |
| `core/` | 当前 Agent/runtime、状态、权限、记忆以及 Rust Core 迁移实现 |
| `providers/` | 当前模型协议、Provider 适配、模型能力和路由兼容层 |
| `integrations/` | Connector、MCP、Skill、Tool、Web 等外部能力 |
| `services/` | 当前可独立运行的后台服务；部分属于 R6 前过渡结构 |
| `packages/` | 跨模块共享的基础能力和 Rust/Python bridge |
| `resources/` | 品牌、截图等非代码资源 |
| `packaging/` | 构建、打包和发布相关内容 |
| `tests/` | 当前 Python / Rust bridge / runtime 测试及测试资源 |
| `docs/` | 产品、架构、治理、审计和运维文档 |
| `scripts/` | 仓库维护和验证脚本 |
| `.github/` | GitHub 配置和自动化 |

## `apps/`

`apps/` 保存用户直接使用的应用。

当前包括：

```text
apps/desktop/
apps/tui/
```

### `apps/desktop/`

当前负责：

- React / TypeScript UI；
- Tauri host；
- Desktop platform integration；
- 用户交互；
- Provider / Connector / Skill / Automation 等设置和展示。

业务 Authority 不得沉积在 TypeScript UI。

目标架构中，Desktop 通过 Tauri Commands / Events 与 Rust Runtime 直接交互；当前 HTTP/WS → Python sidecar 链属于 R6 前的过渡实现。

### `apps/tui/`

负责终端交互界面。

TUI 应通过稳定 Runtime / Capability contract 使用 Delta 能力，不复制核心运行时逻辑。

## `core/`

`core/` 当前包含 Python Agent/runtime 与逐步 Rust-authoritative 的核心领域。

历史和当前职责包括：

- Agent / Runtime glue；
- Session / Run / workflow；
- Memory；
- Permission / Approval；
- Automation state glue；
- Artifact / Citation / Validation facade；
- Rust Core bridge。

Rust 原生实现位于：

```text
core/runtime-native/
```

R1–R5.1 已逐步把 trusted state、execution decision、provider transport/decision 和 runtime correctness 迁到 Rust。

R6 期间的规则：

- 不再向 Python `core/` 新增长期 Manager / Store / Runtime Authority；
- 新控制面默认实现于 Rust；
- Python 中与 Office / Research / Media 相关的专业执行逻辑最终应 Worker 化；
- Python facade 在调用方完成迁移后删除，不长期保留转发链。

当前仍不为了目标架构提前创建顶层 `crates/` 或七个模块目录。物理拆分应在依赖边界稳定后通过独立架构变更完成。

## `providers/`

`providers/` 当前保存模型协议和 Python compatibility / profile surface。

模型产品边界固定为：

```text
OpenAI-compatible
Anthropic-compatible
```

R5 已把 provider transport 和主要 decision authority 迁到 Rust。R6 的目标不是扩大 Provider 数量，而是删除不再需要的 Python forwarding / SDK fallback 控制路径并把最终 Runtime Host 收敛到 Rust。

Provider 的配置界面属于 `apps/desktop/`。

不得把复杂多 Provider 调度、额度、权重和模型市场做进 Delta；这些能力外置给兼容 Endpoint / AI Gateway。

## `integrations/`

`integrations/` 当前负责外部能力和工具生态：

- Connector；
- MCP；
- Skill；
- Tool；
- Web access；
- 第三方平台集成。

长期看，这些能力属于 Capability / Automation 边界。

约束：

- Integration 不拥有 Runtime / Trust / Work Authority；
- Skill 不直接获得额外权限；
- MCP / Connector 不绕过 Policy / Approval / Ledger / Validation；
- Python capability 可以保留，但应通过受控 Capability ABI，而不是依赖 Python Server 内部状态。

## `services/`

`services/` 当前保存独立后台服务：

```text
services/server/
services/stt/
```

### `services/server/`

当前是 Desktop / browser 访问 Python application layer 的 HTTP/WS 边界，并承载 SessionManager 等过渡编排。

这是 **R6 迁移对象**，不是长期目标模块。

从 R5.1 后开始：

- 不向该层新增新的长期业务 Authority；
- 新控制逻辑优先 Rust；
- TypeScript 调用逐步迁往 Tauri → Rust；
- 当 Session / Runtime / Automation / IPC 完成 Rust hard-cut 后删除常驻 `delta-server` 主控职责。

### `services/stt/`

负责本地 Speech-to-Text 能力。

长期应作为 Capability / Worker 看待。它是否继续独立进程由真实资源隔离和生态依赖决定，而不是因为“模块独立”就必须服务化。

## `packages/`

`packages/` 保存当前跨模块共享基础能力和 bridge。

例如：

- Configuration；
- Secrets；
- Persistence helper；
- Sanitization；
- i18n；
- `DeltaCoreClient` 等迁移期 bridge。

不得把 `packages/` 变成无法分类代码的收容目录。

R6 期间 bridge 只允许缩小或提供必要迁移 seam，不得形成新的长期 TS → Python → Rust 转发层。

## `resources/`

`resources/` 只保存非代码资源，例如：

```text
resources/brand/
resources/screenshots/
```

`resources/brand/` 是 Delta 品牌源资源的唯一维护位置。

## `packaging/`

`packaging/` 只负责构建、打包和发行：

- Portable；
- 当前 Sidecar packaging；
- Installer；
- Release artifact；
- Build script。

不得放业务运行时逻辑。

当前 Python sidecar packaging 属于 R6 前实现事实；Rust + TypeScript hard-cut 后应删除不再需要的 Python Server 打包链。

## `tests/`

当前 Python/runtime/跨语言测试统一位于：

```text
tests/
```

Rust 单元测试与 crate 共置，Frontend test 与前端模块共置。

测试专用 Fake / Fixture / Mock 不得成为生产依赖。

R6 期间测试应逐步从“Python 实现行为”转为“Runtime / Capability contract 行为”，使 Python Backend 可以被移除而不丢失产品语义基线。

## `docs/`

当前文档职责：

```text
docs/
├── DELTA_BLUEPRINT.md
├── architecture/
├── audits/
├── governance/
└── operations/
```

### `docs/DELTA_BLUEPRINT.md`

产品长期边界、三项核心能力、Skill / Learning 原则和长期成功标准。

### `docs/architecture/`

保存当前有效系统结构和目标架构：

- `target-architecture.md`：长期目标模块与语言边界；
- `repository-layout.md`：当前物理目录；
- `runtime-public-contract.md`：当前稳定 Runtime contract；
- `capability-abi.md`：Rust ↔ Worker / Adapter 执行边界；
- `adr/`：架构决策历史。

### `docs/audits/`

保存仍有工程价值的审计闭环证据。审计文档不是产品目标定义来源。

### `docs/governance/`

保存长期开发、质量、发布、依赖和迁移治理。

### `docs/operations/`

保存具体维护和平台操作说明，不承担长期产品或架构定义。

## `scripts/`

`scripts/` 保存仓库维护和验证脚本，例如 layout、legacy path、brand、build helper。

业务任务脚本不应因为“也是脚本”就长期放在这里；未来 Capability Worker / Skill script 应进入其明确能力边界。

## `.github/`

`.github/` 保存 GitHub 平台配置和 workflow。

治理原则写入 `docs/governance/`，具体平台操作写入 `docs/operations/`。

## 当前依赖方向

当前仓库仍处于 R6 前过渡形态，大致为：

```text
TypeScript Desktop
      ↓
Tauri / local proxy
      ↓
Python server / application layer
      ↓
Python facades / capability glue
      ↓
Rust delta_core authority
```

这不是长期目标。

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

目标要求：

- UI 不成为业务 Authority；
- Worker 不拥有 Runtime；
- Learning 不拥有 Trust；
- Automation 不绕过 Runtime；
- Provider / Integration 不反向控制 Desktop；
- Packaging 不承载业务逻辑。

## 禁止恢复的旧顶层目录

以下旧顶层目录当前不得重新出现：

```text
surfaces/
coworker/
src/
stt/
assets/
crates/
```

其中 `crates/` 的禁止是**当前物理仓库治理规则**，不是“Rust 永远不能多 crate”的产品原则。若 R6 后真实依赖边界证明需要调整顶层 Rust 布局，应通过独立 ADR + layout migration 修改本文和 CI，而不是提前创建空结构。

不得为了兼容旧路径建立长期代理目录。

## 避免模糊顶层目录

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

若真实能力无法归类，应先检查目标七模块的职责，再决定是否需要物理结构变化。

## 目录变更

以下视为架构变更：

- 新增 / 删除顶层目录；
- 模块职责迁移；
- 关键依赖方向变化；
- Python Backend 物理退出；
- 新增长期 Worker 根目录；
- 大规模路径调整。

必须：

1. 说明调整原因；
2. 明确当前与目标职责；
3. 完成相关迁移；
4. 更新本文；
5. 更新 CI；
6. 清理旧路径引用；
7. 不长期保留无必要 compatibility structure。

## 当前结构优先

本文只描述当前有效物理结构。

目标架构不应通过“提前建目录”伪装成已实现；已经淘汰的迁移过程通过 Git、PR、ADR 和 CHANGELOG 追溯。