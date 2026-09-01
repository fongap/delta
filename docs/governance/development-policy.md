# 开发治理

本文规定 Delta 的日常开发流程，包括分支、Commit、Pull Request、Review、Merge 和变更记录。

## 主分支

`main` 是 Delta 的稳定集成分支。

所有常规开发均从 `main` 创建工作分支，并通过 Pull Request 合入。

禁止直接向 `main` 提交开发代码。

## 工作分支

允许使用以下前缀：

```text
feat/
fix/
refactor/
ci/
chore/
docs/
test/
release/
upstream/
```

含义如下：

* `feat/*`：新增功能
* `fix/*`：缺陷修复
* `refactor/*`：重构
* `ci/*`：CI/CD 调整
* `chore/*`：维护性工作
* `docs/*`：文档修改
* `test/*`：测试调整
* `release/*`：发布准备
* `upstream/*`：上游同步或吸收

一个分支应聚焦一个主要目标。

避免在同一个 Pull Request 中混入大量无关的：

* 功能开发
* 目录迁移
* 依赖升级
* 大规模格式化
* 架构重构

## Commit

推荐格式：

```text
<type>: <description>
```

常用 `type`：

```text
feat
fix
refactor
docs
test
ci
chore
build
release
```

例如：

```text
fix: handle truncated model streams
refactor: split provider routing
docs: update governance rules
```

工作分支允许存在临时 Commit，但合入 `main` 时应通过 Squash merge 保持主分支历史清晰。

Commit 记录具体实现变化；不要把 Commit 粒度直接复制到 `CHANGELOG.md`。

## Pull Request

所有进入 `main` 的变更必须通过 Pull Request。

Pull Request 应至少说明：

* 修改目标
* 主要变化
* 验证方式
* 是否影响兼容性
* 是否影响安全或权限
* 是否需要同步更新文档
* 是否属于需要记录到 `CHANGELOG.md` 的版本级变化

不是每个 Pull Request 都需要修改 `CHANGELOG.md`。

Pull Request 应判断本次变化是否达到版本记录标准；如果多个 Commit 或 Pull Request 属于同一个变化主题，应在 `[Unreleased]` 中合并描述，而不是逐条记录。

Pull Request 不得包含：

* API Key
* Access Token
* Password
* 私钥
* 用户私有数据
* 私有域名或内部环境信息
* 与本次变更无关的大型二进制文件

## Review

Review 重点检查：

1. 是否真正解决目标问题
2. 是否引入不必要复杂度
3. 是否破坏模块职责边界
4. 是否存在安全风险
5. 是否有足够测试
6. 是否需要更新文档
7. 是否产生兼容性变化
8. 是否存在更小、更清晰的实现方式
9. 是否正确判断了 `CHANGELOG.md` 的更新需求
10. `CHANGELOG.md` 是否保持版本级摘要，而非开发流水账

需要处理的 Review conversation 应在合并前解决。

## Merge

默认使用：

```text
Squash merge
```

`main` 应保持：

* 线性
* 清晰
* 可追溯
* 不包含 WIP 历史

原则上禁止：

* 直接 push 到 `main`
* force push
* 绕过 Required Check
* 绕过未解决的 Review
* 为赶进度降低既有质量门槛

## 重构

重构应以行为保持为基本目标。

大型重构应：

1. 控制范围
2. 分阶段实施
3. 每个阶段保持可验证
4. 尽量保留稳定测试 seam
5. 避免同时引入无关功能
6. 同步更新受影响的架构文档

如果变更改变了用户行为、公开接口或配置语义，应按功能变更处理，而不能仅以 `refactor` 名义合入。

## CHANGELOG

`CHANGELOG.md` 是版本级变更摘要，不是开发日志。

其目标是让用户和维护者快速理解：

* 一个版本增加了什么
* 哪些行为发生了变化
* 修复了哪些重要问题
* 是否存在兼容性、安全或迁移影响

### 应记录

以下变化原则上应记录到 `[Unreleased]`：

* 用户可感知的新功能
* 用户可感知的行为变化
* 重要缺陷修复
* 公开接口或配置语义变化
* 重要兼容性变化
* 重要架构或 Runtime 能力变化
* 安全边界或权限模型变化
* Release 或运维行为的重要变化
* 已正式移除或弃用的能力

### 不应记录

以下内容原则上不进入 `CHANGELOG.md`：

* 单个 Commit
* 实现过程
* 类、函数、变量等内部代码变化
* 单独测试文件
* 测试数量
* CI pass/fail 数量
* 调试过程
* 问题排查证据链
* 本地开发环境调整
* Lockfile 普通变化
* 纯格式调整
* 无行为变化的代码整理
* 无行为变化的普通重构
* 无版本意义的依赖更新
* 仅用于验证的临时工作

这些信息应保留在：

* Pull Request
* Commit
* CI
* Issue
* Architecture 文档

而不是重复进入 `CHANGELOG.md`。

### 分类

仅使用 Keep a Changelog 的标准分类：

```text
### 新增 (Added)
### 变更 (Changed)
### 弃用 (Deprecated)
### 移除 (Removed)
### 修复 (Fixed)
### 安全 (Security)
```

没有对应内容的分类可以省略。

不要新增：

```text
### Tests
### Notes
### Internal
### Maintenance
```

等自定义分类。

### 粒度

一个 Changelog 条目应表达一个有意义的变化主题。

多个 Commit 或 Pull Request 如果共同完成同一能力，应合并为一个条目。

推荐：

```markdown
- **Provider 路由与凭据模型**
  - 自定义 endpoint 不再继承无关官方 Provider 的 API Key。
  - Provider 按实际协议和 endpoint/profile 路由。
```

不推荐：

```markdown
- 修改 `providers/registry.py`
- 修改 `_build_openai()`
- 新增 17 个测试
- 修复测试失败
- CI 1285 passed
```

### 时间和实现细节

版本发布日期记录在：

```text
## [X.Y.Z] - YYYY-MM-DD
```

条目内部不再增加小时级时间标题，例如：

```text
#### 2026-08-25 21:27
```

也不使用：

```text
P0
P1
P2
```

等开发阶段标记作为 Changelog 结构。

### Unreleased

所有尚未正式发布但达到记录标准的变化进入：

```text
## [Unreleased]
```

新增条目前，应先检查现有 `[Unreleased]`：

* 已存在同主题条目时，应合并
* 不应重复记录同一变化
* 不应按 Commit 顺序持续追加流水账

正式发布时，再将相关内容归入对应版本。

### 已发布历史

已发布版本原则上冻结。

除以下情况外，不重新修改：

* 明确事实错误
* 错误版本号或日期
* 明显分类错误
* 损坏链接
* 会导致用户误解的重大表述错误

不得为了匹配当前目录、模块名称或实现方式，改写当时真实的历史事实。

## 文档同步

以下变更必须同步检查文档：

* 顶层目录变化
* 模块职责变化
* 开发流程变化
* CI 规则变化
* Release 流程变化
* 上游同步策略变化
* 用户配置方式变化
* `CHANGELOG.md` 维护规则变化

文档应随代码一起更新，不把明显过期内容留给后续处理。
