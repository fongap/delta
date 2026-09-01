# GitHub 仓库设置

本文记录 Delta 推荐的 GitHub 仓库级配置。

这里保存的是长期有效的仓库设置要求，不记录某次临时操作过程。

## `main` 保护

`main` 应通过 GitHub Ruleset 进行保护。

建议启用：

* Require a pull request before merging
* Require status checks to pass before merging
* Require conversation resolution before merging
* Require linear history
* Block force pushes
* Restrict deletions

## Required Check

`main` 的稳定 Required Check 使用：

```text
ci-required
```

不建议把大量内部 matrix job 单独设为 Required Check。

原因是内部 CI job 可能随着：

* 文件目录调整
* 测试矩阵变化
* Workflow 重构
* Job 重命名

而发生变化。

`ci-required` 应作为稳定聚合门禁。

## Pull Request

所有进入 `main` 的变更都应通过 Pull Request。

不得把普通开发流程建立在直接 push `main` 的基础上。

需要处理的 Review conversation 应在合并前完成。

## Merge

推荐只允许：

```text
Squash merge
```

这样可以：

* 保持 `main` 线性
* 减少 WIP Commit
* 避免无意义 Merge Commit
* 让一个 PR 对应一个主要历史变更

工作分支内部 Commit 不需要刻意保持完全整洁。

## Branch 删除

短期工作分支在 PR 合并后可以自动删除。

例如：

```text
feat/*
fix/*
refactor/*
docs/*
ci/*
test/*
chore/*
```

长期分支不得自动删除。

当前长期分支包括：

```text
main
upstream-openworker
```

新增长期分支时，应先明确其长期职责。

## `upstream-openworker`

`upstream-openworker` 是 OpenWorker 上游镜像分支。

该分支：

* 由自动化同步
* 不包含 Delta 自有 Commit
* 不直接作为产品发布分支
* 不自动合入 `main`

上游吸收规则见：

```text
UPSTREAM.md
```

## Release Tag

正式版本 Tag 使用：

```text
v*
```

例如：

```text
v0.3.0
v1.0.0
```

正式 Release Tag 应防止：

* 删除
* 强制移动
* 非授权修改

已经公开发布的 Tag 原则上不得重新指向其他 Commit。

## GitHub Actions 权限

Workflow 权限遵循最小权限原则。

默认优先使用：

```yaml
permissions:
  contents: read
```

确实需要写权限的 Workflow 应单独声明。

例如：

```text
.github/workflows/release.yml
.github/workflows/upstream-sync.yml
```

不得为了简化配置而给所有 Workflow 全局写权限。

## Release Workflow

`release.yml` 只负责发布相关行为。

它可以在确有需要时申请：

```yaml
contents: write
```

写权限应限制在实际发布任务中。

Release 规则见：

```text
docs/governance/release-policy.md
```

## Upstream Sync Workflow

`upstream-sync.yml` 只负责维护：

```text
upstream-openworker
```

它不得直接修改：

```text
main
```

也不得自动把上游内容合入 Delta 产品分支。

## Dependabot

`.github/dependabot.yml` 负责定义：

* 更新哪些依赖
* 检查频率
* 分组规则
* 版本更新范围

它不负责执行 PR 合并。

Dependabot 自动合并应由独立 Workflow 根据明确策略执行。

详细规则见：

```text
docs/governance/dependency-policy.md
```

## Auto Merge

如果仓库启用了 GitHub Auto Merge，应确保：

1. Repository Auto Merge 功能已启用
2. Ruleset 允许目标操作
3. Required Check 已通过
4. PR 符合自动合并策略

普通功能 PR 不应因为 CI 通过就自动合入。

Auto Merge 主要用于已经明确界定的低风险自动化场景，例如符合策略的 Dependabot 更新。

## Conversation Resolution

建议启用：

```text
Require conversation resolution before merging
```

这样 Review 中提出的问题必须明确处理后才能合并。

已经失效或不再适用的 Review thread 应明确 resolve，而不是长期悬置。

## Ruleset Bypass

默认不配置宽泛 Bypass。

如果确有自动化账号或 Release actor 需要 Bypass，应：

* 明确用途
* 限定范围
* 使用最小权限
* 避免对普通开发流程开放

个人维护方便不应成为长期 Bypass 的理由。

## Actions Secrets

Secret 只允许存放于 GitHub Secrets 或受控 Environment 中。

不得：

* 写入 Workflow YAML
* 写入仓库配置
* 写入文档真实值
* 写入测试 fixture
* 输出到 CI Log

示例只能使用明显的占位符。

## Fork Pull Request

来自外部 Fork 的 Pull Request 不应默认获得仓库 Secret。

涉及敏感 Workflow 时，应注意：

* Fork 权限边界
* `pull_request` 与 `pull_request_target` 的差异
* 不在不可信代码上直接暴露 Secret

如无明确需要，优先使用权限更保守的实现。

## 定期检查

以下变化发生后，应重新检查本文档和 GitHub 实际设置是否一致：

* Required Check 调整
* Merge strategy 调整
* Ruleset 调整
* Release workflow 调整
* Upstream sync 调整
* Dependabot 策略调整
* Actions permission 调整
* 新增长期分支

文档和 GitHub 实际配置不一致时，应以明确重新确认后的治理规则为准，并同步修正文档或仓库设置。