# 上游管理

Delta 基于 OpenWorker 演进，但作为独立项目维护自身的产品定位、架构和发布节奏。

本文规定 Delta 对 OpenWorker 上游的同步、评估和吸收方式。

## 上游来源

当前上游：

```text
Repository: andrewyng/openworker
Branch: main
```

Delta 使用：

```text
upstream-openworker
```

作为上游镜像分支。

## 镜像分支

`upstream-openworker` 只用于保存 OpenWorker 上游状态。

该分支必须：

* 与上游保持同步
* 不包含 Delta 自有 Commit
* 不承担 Delta 产品开发
* 不直接用于正式 Release
* 不自动合入 `main`

上游镜像同步由 GitHub Actions 负责。

## 基本原则

上游同步与上游吸收是两件不同的事。

同步只表示：

> Delta 已获取最新 OpenWorker 上游状态。

同步不表示：

> Delta 应自动采用这些变更。

任何上游代码进入 `main` 前都必须经过 Delta 自身评估。

## 吸收流程

上游变更应按以下顺序处理：

1. 获取最新上游状态
2. 识别有价值的变更
3. 判断与 Delta 当前架构是否相关
4. 检查行为和接口兼容性
5. 判断是否需要重新实现
6. 完成代码适配
7. 执行测试和 CI
8. 通过 Pull Request 合入 `main`

不得直接把 `upstream-openworker` merge 到 `main` 作为常规同步方式。

## 优先吸收

通常优先关注：

* Agent/runtime 修复
* Workflow 和任务执行稳定性
* 安全与权限修复
* Approval 机制改进
* MCP 协议改进
* Tool 调用改进
* Connector 修复
* Model protocol 兼容性
* Streaming 稳定性
* Retry / timeout 改进
* 性能和资源使用优化
* 通用测试和可靠性改进

是否吸收仍应以 Delta 当前需求为准。

## 独立维护

以下内容原则上由 Delta 独立维护，不因上游变化自动调整：

* 产品定位
* UI 和交互
* 中文化
* Provider 配置体验
* 模型路由策略
* Repository layout
* Packaging
* Portable
* Release 流程
* 品牌资源
* Delta 专有治理规则

如果上游实现与 Delta 当前架构冲突，应优先保持 Delta 的职责边界。

## 直接移植与重新实现

上游变更不要求逐 Commit 原样 Cherry-pick。

根据实际情况，可以采用：

### 直接吸收

适用于：

* 改动独立
* 与 Delta 当前代码结构一致
* 没有明显产品耦合
* 风险较低

### 适配后吸收

适用于：

* 功能有价值
* API 或路径存在差异
* 需要适配 Delta 当前模块边界

### 重新实现

适用于：

* 上游实现强依赖其自身目录结构
* 与 Delta 产品架构明显冲突
* 直接移植会重新引入历史兼容结构
* Delta 已有更合适的实现基础

目标是吸收能力和修复，而不是复制上游结构。

## 冲突处理

如果上游变更与 Delta 已有实现发生冲突，应优先判断：

1. 上游解决了什么问题
2. Delta 是否存在同样问题
3. Delta 当前实现是否已经覆盖
4. 上游方案是否更可靠
5. 是否值得替换 Delta 实现
6. 替换是否会引入新的架构负担

不得因为“上游更新了”就默认以上游实现覆盖 Delta 实现。

## 上游删除

如果上游删除某项能力，不代表 Delta 必须同步删除。

应判断：

* Delta 是否仍然使用
* 是否属于 Delta 独立能力
* 是否存在安全或维护风险
* 是否还有继续维护的价值

同样，上游新增能力也不代表 Delta 必须引入。

## 测试要求

上游吸收后的代码必须通过 Delta 自身测试体系。

不能仅以上游测试通过作为合并依据。

涉及以下内容时应重点验证：

* Provider
* Streaming
* Retry
* Timeout
* Tool calling
* MCP
* Connector
* Permission
* Approval
* Persistence
* Packaging

最终仍以：

```text
ci-required
```

作为进入 `main` 的质量门禁。

## Pull Request

上游吸收应通过独立 Pull Request 完成。

建议使用：

```text
upstream/*
```

分支前缀。

Pull Request 应说明：

* 对应上游变更
* Delta 为什么需要
* 采用直接移植、适配还是重新实现
* 与当前架构的差异
* 验证结果
* 是否存在兼容性影响

大型上游更新应拆分处理，避免一次 PR 同时引入大量无法独立验证的变化。

## 自动化边界

允许自动化：

* 获取上游状态
* 更新 `upstream-openworker`
* 识别差异
* 生成候选变更清单
* 执行测试

不得默认自动化：

```text
upstream-openworker → main
```

正式代码吸收必须经过 Delta 自身判断。

## 来源说明

Delta 保留对 OpenWorker 项目及其贡献者的来源说明，并遵守相应开源许可证要求。

上游同步机制不改变 Delta 作为独立项目的维护边界。