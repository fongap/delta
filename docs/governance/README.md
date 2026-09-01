# Delta 治理

本目录保存 Delta 当前有效的长期治理规则。

治理文档用于规定项目持续遵循的开发、质量、依赖和发布要求，不记录一次性整改过程、临时操作步骤或已经完成的历史任务。

## 文档

* [`development-policy.md`](development-policy.md)：分支、Commit、Pull Request、Review 和 Merge 规则
* [`quality-policy.md`](quality-policy.md)：CI、测试、质量门禁和安全要求
* [`release-policy.md`](release-policy.md)：版本、Tag、构建和 Release 规则
* [`dependency-policy.md`](dependency-policy.md)：依赖升级、Dependabot 和自动合并规则

仓库和系统结构见：

```text
docs/architecture/
```

具体维护和平台操作说明见：

```text
docs/operations/
```

## 文档原则

Delta 文档遵循以下规则：

1. 正文以中文为主。
2. 文件名使用英文 `kebab-case`。
3. 代码、命令、路径、配置字段、协议名和 Git/GitHub 固有名称保持英文。
4. 文件名描述长期职责，不描述某次工作状态。
5. 一个文件只承担一个明确主题。
6. 当前已经失效、后续不再使用的文档直接删除。
7. 历史变更通过 Git commit、Pull Request、Release 和 `CHANGELOG.md` 追溯。
8. 现有规则发生变化时直接修改原文件，不创建重复版本。

禁止使用类似：

```text
final-report.md
latest-policy.md
new-governance.md
development-policy-v2.md
misc.md
```

这类无法长期表达职责的名称。

## 维护原则

治理规则应保持简洁、稳定和可执行。

如果某条要求已经能够通过 CI、Ruleset 或自动化强制执行，文档应说明其目的和约束，不重复维护大段操作细节。

如果某项内容只是平台配置或具体操作步骤，应放入 `docs/operations/`，而不是继续堆入本目录。