# 开发治理

本文规定 Delta 的日常开发流程，包括分支、Commit、Pull Request、Review、Merge、文档和变更记录。

产品长期边界以 `docs/DELTA_BLUEPRINT.md` 为准；目标架构以 `docs/architecture/target-architecture.md` 和 ADR-050 为准。

## 主分支

`main` 是稳定集成分支。

所有常规开发从 `main` 创建工作分支，并通过 Pull Request 合入。禁止直接向 `main` 提交开发代码。

## 工作分支

允许：

```text
feat/
fix/
refactor/
ci/
chore/
docs/
test/
release/
```

含义：

- `feat/*`：新增产品能力；
- `fix/*`：缺陷修复；
- `refactor/*`：行为保持型重构 / 迁移；
- `ci/*`：CI/CD；
- `chore/*`：维护；
- `docs/*`：文档；
- `test/*`：测试；
- `release/*`：发布准备。

Delta 不维护单一上游，因此不再保留 `upstream/*` 作为常规分支类型。

一个分支聚焦一个主要目标，避免把产品功能、目录迁移、依赖升级、大规模格式化和 Runtime 重构混在同一个 PR。

## 产品 / 架构分类

开始实现前先判断变更属于：

```text
产品域：日常办公 / 研究分析 / 内容创作

逻辑模块：
Experience / Runtime / Trust / Work /
Capability / Automation / Learning
```

如果是新增能力，优先问：

> 能否作为 Capability / Skill 实现，而不扩张 Runtime、Agent 或常驻服务？

如果是控制状态、权限、事实或生命周期，默认应进入 Rust Authority，而不是新增 Python Manager / Store / Runtime abstraction。

## Commit

推荐：

```text
<type>: <description>
```

例如：

```text
fix: handle truncated model streams
refactor: move session runtime ownership to Rust
docs: define research analysis scope
```

工作分支允许临时 Commit；合入 `main` 时默认 Squash merge。

Commit 记录实现变化，不把 Commit 粒度复制到 CHANGELOG。

## Pull Request

PR 至少说明：

- 修改目标；
- 产品域 / 逻辑模块；
- 主要变化；
- 验证方式；
- 兼容性；
- 安全 / 权限影响；
- 文档影响；
- 是否达到 CHANGELOG 记录标准。

不得包含 API Key、Token、Password、私钥、用户私有数据、私有环境信息或无关大型二进制文件。

### Runtime / Authority PR 额外字段

涉及 R6 Runtime Convergence 时必须写清：

```text
Authority Before
Authority After
Compatibility
Exit Condition
Failure / Rollback
Tests
```

迁移 PR 不得用“以后再删”代替明确 Exit Condition。

## Review

Review 重点：

1. 是否解决真实目标；
2. 是否直接服务日常办公、研究分析或内容创作；
3. 是否优先使用 Capability / Skill，而不是新 Agent / Runtime；
4. 是否引入不必要复杂度；
5. 是否破坏七个长期模块的职责；
6. 是否恢复 Python core authority 或双 Authority；
7. 是否存在安全风险；
8. Worker / Skill / Learning 是否绕过 Trust；
9. 是否有足够测试；
10. 是否需要同步文档；
11. 是否产生兼容性变化；
12. 是否存在更小、更清晰的实现；
13. CHANGELOG 粒度是否是版本级摘要。

需要处理的 Review conversation 合并前必须解决。

## Merge

默认：

```text
Squash merge
```

`main` 应保持线性、清晰、可追溯，不包含 WIP 历史。

原则上禁止：

- 直接 push `main`；
- force push；
- 绕过 Required Check；
- 绕过 Review；
- 为赶进度降低质量 / Trust 门槛。

## 重构

重构以行为保持为默认目标。

大型重构应：

1. 控制范围；
2. 分阶段；
3. 每阶段可验证；
4. 保留 contract / test seam；
5. 不混入无关功能；
6. 同步架构文档；
7. 切换调用方后删除旧 owner / forwarding path。

R6 特别禁止“为了 Rust 纯度重写全部 Office / statistics / media Python”。迁移的是控制权；专业生态优先 Worker 化。

## 产品能力开发

新增能力应按以下优先级：

```text
现有 Capability 组合
    ↓
Skill
    ↓
新增 Capability / Worker
    ↓
扩展 Rust Host / Runtime（仅确有必要）
```

专用 Agent、新 Runtime、新协议、新常驻服务是最后选择。

### 研究分析

涉及统计 / DOE / 序贯试验时，必须区分：

- 确定性计算；
- 方法选择；
- 研究假设；
- 可调整范围；
- 停止规则；
- 用户决定。

序贯设计不得因为看到了当前结果就无记录地重写研究目标或停止标准。

### Learning

Experience / Skill Evaluation 可以演进，但任何学习结果都不得自动：

- 降低审批；
- 提高 Risk grant；
- 扩大 Network / Secrets / File scope；
- 改变核心 Authority。

## CHANGELOG

`CHANGELOG.md` 是版本级变更摘要，不是开发日志。

### 应记录

原则上记录：

- 用户可感知新功能 / 行为变化；
- 重要缺陷修复；
- 公开接口 / 配置语义变化；
- 重要兼容性变化；
- 重要 Runtime / Architecture 能力变化；
- 安全 / 权限变化；
- Release / 运维重要变化；
- 正式移除 / 弃用能力。

### 不应记录

不记录：

- 单个 Commit；
- 实现过程；
- 内部函数 / 文件变化；
- 单独测试文件或测试数量；
- CI pass/fail 数量；
- 调试证据链；
- 普通 lockfile / 格式调整；
- 无行为变化重构；
- 无版本意义依赖更新。

这些信息保留在 PR、Commit、CI、Issue 和 Architecture 文档。

### 分类

只使用 Keep a Changelog：

```text
### 新增 (Added)
### 变更 (Changed)
### 弃用 (Deprecated)
### 移除 (Removed)
### 修复 (Fixed)
### 安全 (Security)
```

### 粒度

一个条目表达一个有意义的版本变化主题，不按 Commit 流水追加。

### Unreleased

未发布但达到标准的变化进入 `## [Unreleased]`。新增前先合并同主题内容，避免重复。

### 已发布历史

已发布版本原则上冻结。除明确事实、版本号 / 日期、分类、损坏链接或重大误导错误外，不为匹配当前结构重写历史。

## 文档同步

以下变化必须同步检查文档：

- 三产品域范围；
- 逻辑模块职责；
- 顶层目录 / 依赖方向；
- Runtime / Authority；
- Python Worker / Rust Host 边界；
- Skill / Learning 治理；
- 模型协议；
- CI / Release / dependency policy；
- 用户配置方式。

文档分类：

- `DELTA_BLUEPRINT.md`：长期产品 / 能力；
- `target-architecture.md`：长期系统目标；
- `repository-layout.md`：当前物理结构；
- `runtime-public-contract.md`：当前公共行为 contract；
- ADR：架构决策与历史事实；
- CHANGELOG：版本级变化。

目标状态必须标明 Target / R6；当前事实不得提前写成已完成。