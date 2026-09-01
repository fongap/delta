# 开发治理

本文规定 Delta 的日常开发流程，包括分支、Commit、Pull Request、Review 和 Merge。

## 主分支

`main` 是 Delta 的稳定集成分支。

所有常规开发均从 `main` 创建工作分支，并通过 Pull Request 合入。

禁止直接向 `main` 提交开发代码。

## 工作分支

允许使用以下前缀：

```text id="r8v1qm"
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

```text id="ss1m2g"
<type>: <description>
```

常用 `type`：

```text id="wa3j3g"
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

```text id="9vbtvj"
fix: handle truncated model streams
refactor: split provider routing
docs: update governance rules
```

工作分支允许存在临时 Commit，但合入 `main` 时应通过 squash 保持主分支历史清晰。

## Pull Request

所有进入 `main` 的变更必须通过 Pull Request。

Pull Request 应至少说明：

* 修改目标
* 主要变化
* 验证方式
* 是否影响兼容性
* 是否影响安全或权限
* 是否需要同步更新文档

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

需要处理的 review conversation 应在合并前解决。

## Merge

默认使用：

```text id="3gnh1b"
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

## 文档同步

以下变更必须同步检查文档：

* 顶层目录变化
* 模块职责变化
* 开发流程变化
* CI 规则变化
* Release 流程变化
* 上游同步策略变化
* 用户配置方式变化

文档应随代码一起更新，不把明显过期内容留给后续处理。