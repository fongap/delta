# 参与 Delta 开发

本文件说明 Delta 的基本开发流程。

完整治理规则见：

```text
docs/governance/
```

仓库结构见：

```text
docs/architecture/repository-layout.md
```

## 分支

所有开发均从 `main` 创建工作分支。

允许使用：

```text
feat/*
fix/*
refactor/*
ci/*
chore/*
docs/*
test/*
release/*
upstream/*
```

各前缀含义和使用规则见：

```text
docs/governance/development-policy.md
```

## Commit

推荐格式：

```text
<type>: <description>
```

例如：

```text
fix: handle truncated model streams
docs: update repository governance
refactor: split provider routing
```

工作分支允许存在临时 Commit。

合入 `main` 时默认使用 Squash merge，保持主分支历史清晰。

## Pull Request

所有进入 `main` 的变更必须通过 Pull Request。

合并前至少应满足：

1. `ci-required` 通过
2. 需要处理的 Review conversation 已解决
3. 不包含真实 Token、Secret、用户私有数据或内部敏感信息
4. 相关测试已完成
5. 受影响文档已同步更新
6. 符合当前仓库目录和模块职责要求

## CI

`ci-required` 是 `main` 的稳定 Required Check。

`layout-check` 用于保护仓库结构和路径一致性。

不得通过删除测试、降低断言、扩大豁免或放宽既有规则来规避真实 CI 失败。

质量要求见：

```text
docs/governance/quality-policy.md
```

## 上游

OpenWorker 上游代码不得自动合入 `main`。

上游变化必须经过评估、验证并通过 Pull Request 选择性吸收。

详见：

```text
UPSTREAM.md
```

## 依赖

依赖升级应遵循：

```text
docs/governance/dependency-policy.md
```

Major 升级不得自动合并。

低风险依赖更新是否允许自动合并，应由明确的 Dependabot 和 CI 策略决定。

## 发布

正式 Release 只能基于 `main` 创建。

版本 Tag 使用：

```text
vMAJOR.MINOR.PATCH
```

例如：

```text
v0.3.0
v1.0.0
```

完整发布规则见：

```text
docs/governance/release-policy.md
```

## 文档

正文以中文为主。

以下内容保持英文：

* 代码标识
* 文件名和路径
* 命令
* 配置字段
* 协议名称
* Git/GitHub 固有术语

文件名应使用英文 `kebab-case`，并表达长期职责。

不再使用 `final`、`latest`、`new`、`v2` 等阶段性名称维护当前有效文档。