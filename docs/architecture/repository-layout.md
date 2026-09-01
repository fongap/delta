# 仓库目录规范

Delta 按系统职责组织代码。

目录名称应描述当前职责，不描述上游来源、历史阶段或兼容时代。

## 顶层目录

| 目录              | 职责                                 |
| --------------- | ---------------------------------- |
| `apps/`         | 用户可直接运行的应用                         |
| `core/`         | Agent runtime、工作流、状态、记忆、权限等核心能力    |
| `providers/`    | 模型协议、Provider 适配、模型能力和路由           |
| `integrations/` | Connector、MCP、Skill、Tool、Web 等外部能力 |
| `services/`     | 可独立运行的后台服务                         |
| `packages/`     | 跨模块共享的基础能力                         |
| `resources/`    | 品牌、截图等非代码资源                        |
| `packaging/`    | 构建、打包和发布相关内容                       |
| `tests/`        | Python/runtime 测试及测试专用资源           |
| `docs/`         | 架构、治理和运维文档                         |
| `scripts/`      | 仓库维护和验证脚本                          |
| `.github/`      | GitHub 配置和自动化                      |

## `apps/`

`apps/` 保存用户直接使用的应用。

当前包括：

```text
apps/desktop/
apps/tui/
```

### `apps/desktop/`

负责：

* React UI
* Tauri
* Desktop platform integration
* 用户交互
* Provider 配置界面
* Connector 展示界面

业务核心逻辑不得长期沉积在 Desktop UI 层。

### `apps/tui/`

负责终端交互界面。

TUI 应通过既有核心边界使用 Delta 能力，不复制核心运行时逻辑。

## `core/`

`core/` 负责 Delta 核心运行时。

包括：

* Agent
* Workflow
* Task execution
* State
* Memory
* Permission
* Persona
* Approval

`core/` 可以依赖：

```text
providers/
integrations/
packages/
```

`core/` 不应依赖具体 UI 实现。

## `providers/`

`providers/` 负责模型服务和协议适配。

包括：

* Provider registry
* Model discovery
* Capability metadata
* Protocol adaptation
* Routing-related provider logic

Provider 的配置界面仍属于：

```text
apps/desktop/
```

不得把 UI 逻辑放入 `providers/`。

## `integrations/`

`integrations/` 负责 Delta 与外部能力的集成。

包括：

* Connector
* MCP
* Skill
* Tool
* Web access
* 第三方平台集成

外部系统协议实现与 UI 展示应保持分离。

例如：

* Connector 协议和调用逻辑属于 `integrations/`
* Connector 界面属于 `apps/desktop/`

## `services/`

`services/` 保存可以独立运行的后台服务。

当前包括：

```text
services/server/
services/stt/
```

### `services/server/`

作为应用访问 Delta 核心能力的 HTTP/API 边界。

不应在该层复制 `core/` 中已经存在的业务逻辑。

### `services/stt/`

负责本地 Speech-to-Text 服务。

应保持为独立服务边界。

## `packages/`

`packages/` 保存真正具有跨模块共享价值的基础能力。

例如：

* Configuration
* Secrets
* Persistence
* Sanitization
* i18n
* 跨应用基础设施

不得把 `packages/` 变成无法分类代码的收容目录。

如果某个模块只服务于单一领域，应优先放入对应领域目录，而不是默认放入 `packages/`。

## `resources/`

`resources/` 只保存非代码资源。

例如：

```text
resources/brand/
resources/screenshots/
```

`resources/brand/` 是 Delta 品牌资源的唯一来源。

生成后的图标可以位于实际使用目录，但源品牌资产不得多处独立维护。

## `packaging/`

`packaging/` 只负责构建、打包和发行。

包括：

* Portable
* Sidecar packaging
* Installer
* Release artifact
* Build script

不得把业务运行时代码放入 `packaging/`。

## `tests/`

Python/runtime 测试统一位于：

```text
tests/
```

测试专用内容也应位于测试边界内，例如：

* Fake
* Fixture
* Test helper
* Mock service

生产包中不得长期保留测试专用实现。

Frontend 测试可以与对应模块共置。

例如：

```text
apps/desktop/src/**/*.test.*
packages/i18n/**/*.test.*
```

Frontend e2e 位于：

```text
apps/desktop/tests/
```

## `docs/`

文档按职责分为：

```text
docs/
├─ architecture/
├─ governance/
└─ operations/
```

### `docs/architecture/`

保存当前有效的系统和代码结构说明。

用于回答：

* 系统如何划分
* 模块职责是什么
* 为什么采用当前结构
* 依赖边界如何定义

### `docs/governance/`

保存长期有效的项目治理规则。

包括：

* 开发治理
* 质量治理
* 发布治理
* 依赖治理

不保存：

* 一次性整改报告
* 已完成迁移记录
* 临时操作步骤
* 已失效历史说明

### `docs/operations/`

保存具体维护和平台操作说明。

例如：

* GitHub repository settings
* Windows code signing
* 后续确有必要保留的运维操作文档

这里描述“如何操作”，不承担长期治理原则的定义。

## `scripts/`

`scripts/` 保存仓库维护和验证脚本。

例如：

* Repository validation
* Layout check
* Legacy path check
* Brand asset check
* Build helper

脚本应服务于明确的仓库维护目标。

不得把业务运行时逻辑长期放在 `scripts/`。

## `.github/`

`.github/` 保存 GitHub 平台相关配置。

例如：

```text
.github/workflows/
.github/dependabot.yml
```

Workflow 负责自动化执行。

治理原则应记录在：

```text
docs/governance/
```

具体 GitHub 平台设置应记录在：

```text
docs/operations/
```

## 依赖边界

总体依赖方向应保持清晰。

推荐关系：

```text
apps
  ↓
services
  ↓
core
 ↙   ↘
providers integrations
    ↓
  packages
```

实际依赖不要求机械符合单一树形结构，但必须避免：

* UI 反向成为核心运行时依赖
* Provider 依赖具体 UI
* Integration 直接控制 Desktop 展示
* Packaging 承载业务逻辑
* Test helper 被生产代码依赖

## 禁止恢复的旧顶层目录

以下旧顶层目录不得重新出现：

```text
surfaces/
coworker/
src/
stt/
assets/
crates/
```

这些目录已经被当前职责结构取代。

不得为了兼容旧路径重新建立空目录、代理目录或长期兼容层。

## 避免模糊顶层目录

原则上不得新增缺乏明确职责的顶层目录，例如：

```text
common/
misc/
helpers/
utils/
shared/
base/
legacy/
```

新增模块必须优先归入现有职责边界。

如果确实无法合理归类，应先重新评估架构职责，而不是直接增加一个模糊目录。

## 目录变更

涉及以下内容时，视为架构变更：

* 新增顶层目录
* 删除顶层目录
* 模块职责迁移
* 关键依赖方向变化
* 大规模路径调整

此类变更必须：

1. 说明调整原因
2. 明确新职责边界
3. 完成相关代码迁移
4. 更新本文档
5. 更新相关 CI 检查
6. 清理旧路径引用
7. 不长期保留无必要兼容结构

## 当前结构优先

仓库规范只描述当前有效结构。

已经淘汰的目录和迁移过程不继续保存在当前架构文档中。

历史需要追溯时，应通过 Git commit、Pull Request 和 `CHANGELOG.md` 查询。