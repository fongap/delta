# 质量治理

本文规定 Delta 的持续集成、测试、质量门禁和安全检查要求。

## 基本原则

Delta 的质量控制应尽量通过自动化验证完成。

人工 Review 用于判断设计、边界和风险，不替代 CI。

任何变更都不应通过降低既有质量标准来换取合并。

## Required Check

`ci-required` 是 `main` 的稳定 Required Check。

内部 CI job 可以调整，但应尽量保持 `ci-required` 名称稳定，避免 GitHub Ruleset 因内部 job 重命名而失效。

如果新增、删除或重命名 CI job，必须同步检查 `ci-required` 的依赖关系。

## CI 范围

根据变更范围，CI 应覆盖：

* Python tests
* TypeScript tests
* Frontend e2e
* Rust build / test
* Ruff
* Pyright
* format
* Clippy
* dependency / security scan
* repository layout
* packaging validation

不要求所有变更都执行完全相同的检查，但涉及核心运行时、构建、打包或跨模块边界的变更不得跳过关键验证。

## `layout-check`

`layout-check` 用于保护仓库结构和路径一致性。

至少应检查：

* 非法顶层目录
* 已废弃路径重新出现
* 关键目录职责边界
* 影响构建和发布的路径残留

不得为了让 CI 通过而持续扩大豁免范围。

如果检查规则与当前架构不一致，应修改规则本身，而不是保留历史兼容路径。

## 历史路径

当前有效的：

* 代码
* 工作流
* 配置
* 测试
* 架构文档
* 治理文档
* 运维文档

都应使用当前规范路径。

已经删除或迁移的旧路径只应存在于 Git 历史、Pull Request 或 `CHANGELOG.md` 等历史记录中。

不得将整个当前文档目录设置为历史路径豁免区。

## 测试原则

新增功能应覆盖核心行为。

修复缺陷时，应尽量增加能够复现该问题的回归测试。

重构应优先证明行为保持不变。

不得通过以下方式掩盖真实失败：

* 删除正常测试
* 无依据增加 `skip`
* 扩大 ignore 范围
* 降低断言强度
* 删除关键测试路径
* 仅依赖手工验证替代已有自动化测试

## CI 失败分类

出现 CI 失败时，应先判断属于哪一类：

1. 本次变更引入
2. 已存在问题
3. 环境问题
4. 外部服务问题
5. 依赖或工具链变化

如果认定为既存或外部问题，应提供能够支持该判断的证据。

“本地可以运行”不能作为绕过 CI 的依据。

## 安全检查

任何代码、文档、配置和测试都不得提交真实敏感信息，包括：

* API Key
* Access Token
* Password
* Private Key
* PFX
* 用户私有数据
* 内部服务凭据

敏感值必须通过 GitHub Secrets、运行时安全存储或其他受控方式提供。

示例配置必须使用明显的占位值。

## 依赖安全

依赖变更应检查：

* 已知漏洞
* License
* Breaking change
* Runtime 行为变化
* Build toolchain 变化

高风险依赖升级不得仅因为 Dependabot 自动创建 PR 就直接合并。

具体规则见：

```text
docs/governance/dependency-policy.md
```

## 合并要求

进入 `main` 前至少应满足：

1. `ci-required` 通过
2. 关键测试通过
3. 不存在已知未处理的安全问题
4. 没有通过放宽检查掩盖失败
5. 相关文档已同步
6. 变更范围与 Pull Request 描述一致

如果某项质量要求暂时无法满足，应先明确原因和影响，再决定是否继续；不能把临时例外默认为长期规则。