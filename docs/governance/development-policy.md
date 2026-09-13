# 开发治理

本文规定 Delta 的日常开发、架构边界、命名、文档与迁移治理。

产品长期边界以 `docs/DELTA_BLUEPRINT.md` 为准；目标架构以 `docs/architecture/target-architecture.md` 和 ADR-050 为准；当前 R6 执行状态以 `docs/governance/r6-execution-plan.md` 为准。

## 1. 长期标准

所有架构与开发决策优先满足：

```text
长期 | 稳定 | 高效 | 安全 | 敏捷
```

不要为了架构形式、语言纯度、品牌统一或目录美观制造不必要复杂度。

## 2. 产品与模块分类

一级产品域只有：

```text
日常办公
研究分析
内容创作
```

长期逻辑职责：

```text
Experience
Runtime
Trust
Work
Capability
Automation
Learning
```

这些是职责边界，不要求机械拆成独立 crate / package / service。

新增能力优先顺序：

```text
现有 Capability 组合
→ Skill
→ 新 Capability / Worker
→ 扩展 Rust Host / Runtime（仅确有必要）
→ 新 Agent / 新常驻服务（最后选择）
```

## 3. 命名治理

> **独立边界用 `delta-*`，内部职责用语义名；品牌化用于识别产品边界，不用于包装每一个内部模块。**

只有形成明确独立边界时才使用 `delta-*` / `@delta/*`，例如：

- 独立 executable；
- 独立 crate / package 且有公共 API；
- 独立版本兼容边界；
- 独立跨语言协议；
- 独立 SDK；
- 独立发布 / 分发单元。

推荐：

```text
Delta / Delta.exe
delta-runtime
delta-protocol
delta-capability
delta-worker
delta-worker-py    # 仅确实形成 Python SDK 时
delta-cli          # 仅保留独立 CLI 时
delta-testkit      # 仅形成稳定公共 testkit 时
@delta/*           # 仅形成独立 TS workspace package 时
```

内部职责继续使用：

```text
session
run
context
provider
policy
approval
ledger
source
artifact
validation
memory
experience
skill
```

除非真实形成独立发布 / 版本 / 协议 / 进程边界，不创建 `delta-trust`、`delta-work`、`delta-learning`、`delta-artifact` 等品牌包装模块。

命名细则见 `docs/architecture/repository-layout.md`。

## 4. 语言与 Authority

核心产品语言目标：

```text
Rust + TypeScript
```

- TypeScript：Experience / UI；
- Rust：Runtime、Trust、Work、Automation、Learning authority、Capability Host；
- Python / PowerShell / Shell：受控 Worker / Script。

Worker 不得拥有 Session、Run State、Policy / Approval、core DB、Artifact formal state、Secrets authority 或 model/provider control plane。

一个领域只能有一个 Authority。完成迁移后必须删除旧 owner / forwarding path，不能长期保留双主控。

## 5. R6 治理

R6 正在执行，不再描述为“后续阶段”。PR #203 已完成 RuntimeHost 与 Tauri direct IPC 的第一批落地。

当前剩余方向：

```text
Application Control Plane
→ Agent Loop / Provider Hard-Cut
→ Capability Workerization
→ Persistent Python Backend Removal
→ Hard-Cut Audit
```

每个 Runtime / Authority PR 必须写清：

```text
Authority Before
Authority After
Compatibility
Exit Condition
Failure / Rollback
Tests
```

R6 迁移的是控制权和进程拓扑，不为了 Rust 纯度重写全部 Office / Statistics / Media Python 生态。

## 6. 研究分析与 Learning

统计 / DOE / 序贯试验必须区分：

- 确定性计算；
- 方法选择；
- 研究假设；
- 可调整范围；
- 停止规则；
- 用户决定。

序贯设计不得因看到当前结果就无记录地重写研究目标或停止标准。

Learning 可以改善 Preference、Experience、Skill、Workflow、Template，但不能自动降低 Approval、提高 Risk grant、扩大 Network / Secrets / File scope 或改变核心 Authority。

## 7. 分支与 Commit

所有常规开发从 `main` 创建工作分支，通过 Pull Request 合入。

推荐分支：

```text
feat/*
fix/*
refactor/*
ci/*
chore/*
docs/*
test/*
release/*
```

Commit 推荐：

```text
<type>: <description>
```

合入 `main` 默认 Squash merge。

## 8. Pull Request

PR 至少说明：

- 修改目标；
- 产品域 / 逻辑职责；
- 主要变化；
- 验证方式；
- 兼容性；
- 安全 / 权限影响；
- 文档影响；
- 是否达到 CHANGELOG 标准。

Review 重点：

1. 是否解决真实目标；
2. 是否服务三产品域之一；
3. 是否优先用 Capability / Skill；
4. 是否引入不必要复杂度；
5. Authority 是否清晰；
6. 是否恢复 Python core authority 或双 Authority；
7. Worker / Skill / Learning 是否绕过 Trust；
8. 命名是否符合独立边界规则；
9. 测试与文档是否同步。

## 9. 文档治理

当前 active 文档只保留明确职责：

- `DELTA_BLUEPRINT.md`：产品长期边界；
- `target-architecture.md`：长期系统目标；
- `repository-layout.md`：当前物理结构与命名；
- `runtime-public-contract.md`：稳定 Runtime / Human Control contract；
- `capability-abi.md`：Capability / Worker 边界；
- `r6-execution-plan.md`：R6 当前状态与阶段 Exit Condition；
- ADR：历史架构决策；
- CHANGELOG：版本级变化；
- audits：审计证据；
- operations：平台操作。

已完成且不再定义当前状态的一次性迁移说明应删除，历史通过 Git / PR / ADR / CHANGELOG 保留。

不创建 `latest`、`final`、`v2`、`new` 等当前文档副本。

## 10. CHANGELOG

记录用户可感知变化、重要兼容性 / Runtime / Security / Release 变化；不记录单个 Commit、调试过程、测试数量或普通内部重构。

已发布版本原则上冻结。

## 11. 依赖与发布

依赖规则见 `dependency-policy.md`，质量门禁见 `quality-policy.md`，发布规则见 `release-policy.md`。

新依赖必须说明属于核心 Rust / TypeScript 产品还是受控 Worker 生态，不能反向固化 Python 控制面。