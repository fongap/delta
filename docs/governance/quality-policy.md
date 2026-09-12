# 质量治理

本文规定 Delta 的持续集成、测试、质量门禁和安全检查要求。

质量目标统一服务于：

```text
长期 | 稳定 | 高效 | 安全 | 敏捷
```

## 基本原则

Delta 的质量控制应尽量通过自动化验证完成。

人工 Review 用于判断产品边界、模块职责、Authority、安全和风险，不替代 CI。

任何变更都不应通过降低既有质量标准换取合并。

## Required Check

`ci-required` 是 `main` 的稳定 Required Check。

内部 CI job 可以演进，但应尽量保持 `ci-required` 名称稳定，避免 Ruleset 因内部 job 重命名失效。

新增、删除或重命名 CI job 时必须同步检查 `ci-required` 依赖关系。

## CI 范围

根据变更范围，CI 应覆盖：

- Python / Worker tests；
- TypeScript tests；
- Frontend e2e；
- Rust build / test；
- Rust / TypeScript contract tests；
- Ruff / Pyright（Python 仍存在期间）；
- format / Clippy；
- dependency / security scan；
- repository layout；
- packaging validation。

涉及核心 Runtime、Trust、Work、Capability ABI、构建或打包的变更不得跳过关键验证。

## R6 Runtime Convergence Gates

随着 Rust + TypeScript 收敛推进，CI 应逐步加入并最终强制：

- Rust Runtime contract tests；
- TypeScript ↔ Tauri / Rust IPC contract；
- Steer / Follow-up / Cancel e2e；
- Capability ABI compatibility；
- Worker crash / timeout / cancel / malformed response；
- forbidden Python core authority regression；
- forbidden Worker direct core DB write；
- migrated domain 无 Python fallback writer；
- 无新增 persistent Python application authority；
- portable / release smoke。

迁移验收关注**行为和 Authority**，不关注 Rust 百分比或 Python 文件数。

## Research Analysis Quality

研究分析不能只验证“代码执行成功”。

涉及统计、DOE、序贯试验和正式研究报告时，根据能力范围应增加：

- 数据 identity / hash；
- 数值结果 regression / tolerance；
- 统计方法前提；
- 随机种子 / reproducibility（适用时）；
- 设计矩阵可估计性；
- DOE / sequential-design constraint preservation；
- 停止规则和允许调整范围不能被静默改变；
- 图表 / structured result / report artifact 完整性；
- Source / Citation consistency。

模型自然语言解释不能替代可确定验证的统计计算和设计约束。

## Content / Media Quality

图文、图片、视频 Capability 根据任务需要验证：

- 文件格式和可打开性；
- 尺寸 / 时长 / codec / media type；
- 输出文件完整性；
- 平台规格；
- Source / rights metadata（适用时）；
- 资源消耗和 timeout；
- Worker staging boundary。

不要求核心 Runtime 判断审美优劣，但必须判断成果是否真实存在、格式可用、没有越权输出。

## Skill / Learning Quality

Skill Candidate 或 Learning 结果进入长期使用前必须有可观察证据。

至少根据风险使用：

- 历史 Run replay；
- Validation；
- 成功 / 失败率；
- 用户采用 / 修改；
- 回归任务；
- 版本比较。

Learning 不得通过测试或配置自动降低 Policy / Approval / Risk / Sandbox / Network / Secrets 边界。

## `layout-check`

`layout-check` 保护当前仓库物理结构和路径一致性。

至少检查：

- 非法顶层目录；
- 已废弃路径重新出现；
- 关键目录职责边界；
- 构建 / 发布路径残留；
- R6 迁移后已删除 Python Backend 路径是否被恢复。

目标架构的七个逻辑模块不要求创建七个同名目录；不得为了“符合架构图”提前修改 layout-check。

## 测试原则

新增功能覆盖核心行为；缺陷修复尽量增加回归测试；重构优先证明行为保持。

不得：

- 删除正常测试掩盖失败；
- 无依据增加 `skip`；
- 扩大 ignore；
- 降低断言；
- 删除关键测试路径；
- 只用手工验证替代已有自动测试。

R6 迁移尤其应把测试从 Python class / endpoint 内部实现逐步提升到稳定 Runtime / Capability / IPC contract，使旧 Backend 可以安全删除。

## CI 失败分类

出现失败先判断：

1. 本次变更引入；
2. 已存在问题；
3. 环境问题；
4. 外部服务问题；
5. 依赖 / 工具链变化。

认定为既存或外部问题时应有证据。“本地可以运行”不能绕过 CI。

## 安全检查

代码、文档、配置和测试不得提交真实：

- API Key / Access Token；
- Password / Private Key / PFX；
- 用户私有数据；
- 内部服务凭据。

示例必须使用明显占位值。

Worker / Skill / Connector / Learning 还必须验证不能绕过 Trust 或获取未声明资源。

## 依赖安全

依赖变更检查：

- 已知漏洞；
- License；
- Breaking change；
- Runtime / numerical behavior；
- Build / Packaging；
- Worker sandbox / binary redistribution。

详见 `docs/governance/dependency-policy.md`。

## 合并要求

进入 `main` 前至少满足：

1. `ci-required` 通过；
2. 关键 contract / regression tests 通过；
3. 无已知未处理安全问题；
4. 没有通过放宽检查掩盖失败；
5. 相关文档同步；
6. 变更范围与 PR 一致；
7. Authority Before / After 与代码一致（涉及迁移时）；
8. 新 Capability / Skill 未绕过 Trust / Work / Validation；
9. 三产品域真实任务没有因架构迁移无证据退化。

无法满足时必须明确原因和影响，临时例外不能自动变成长期规则。