# 依赖治理

本文规定 Delta 的依赖升级、Dependabot、兼容性检查和自动合并要求。

## 基本原则

依赖管理以以下目标为优先：

1. 安全
2. 兼容
3. 稳定
4. 可维护
5. 必要时再追求新版本

不以“始终升级到最新版本”作为目标。

## Dependabot

Dependabot 用于：

* 检测依赖更新
* 创建升级 Pull Request
* 提供版本变更信息

Dependabot 本身不负责决定某个 Pull Request 是否可以安全合并。

`.github/dependabot.yml` 只定义更新策略，例如：

* package ecosystem
* directory
* schedule
* version update policy
* grouping

PR 自动合并逻辑应由独立 GitHub Actions workflow 负责。

## 升级分类

依赖升级按风险分为：

* Patch
* Minor
* Major

版本号只是风险信号之一，不能替代实际兼容性判断。

某些 Minor 更新也可能包含明显行为变化。

## Patch

Patch 更新通常可以作为低风险升级处理。

自动合并前至少满足：

1. `ci-required` 通过
2. 没有 Merge conflict
3. 没有安全或许可证异常
4. 不需要人工代码适配
5. 没有已知 breaking change
6. 不改变核心运行时行为

满足条件时，可以允许自动合并。

## Minor

Minor 更新需要根据依赖类型判断。

对于：

* 开发工具
* 测试工具
* 格式化工具
* 兼容性明确的基础依赖

在 CI 完整通过且没有行为变化时，可以允许自动合并。

对于：

* Runtime 核心依赖
* Provider SDK
* HTTP client
* 序列化库
* 异步运行时
* 数据库
* 构建工具链
* Packaging 相关依赖

即使是 Minor，也应提高审查等级。

## Major

Major 更新不得自动合并。

必须人工检查：

* Breaking changes
* API 删除或重命名
* 默认行为变化
* 配置格式变化
* Error 类型变化
* Serialization 行为变化
* Runtime 性能变化
* Build toolchain 影响
* Packaging 影响
* License 变化

如果需要修改业务代码才能适配，应将依赖升级和兼容性代码作为同一个明确 PR 处理。

## CI 与自动合并

依赖 PR 的 CI 失败时，不得继续自动合并。

必须区分：

* 依赖本身不兼容
* 测试暴露真实回归
* Lockfile 冲突
* GitHub Auto Merge 未启用
* Ruleset 阻止
* Workflow permission 不足
* 临时外部服务失败

自动合并 workflow 自身失败，不等于依赖升级本身存在问题。

反过来，CI 通过也不代表所有 Major 升级都可以自动合并。

## 多依赖冲突

如果多个依赖对同一个基础库要求不同版本，应优先解决依赖关系本身，而不是强制锁定一个无法同时满足双方约束的版本。

处理顺序：

1. 找出冲突依赖
2. 确认双方版本要求
3. 检查是否存在可兼容的新版本
4. 优先升级限制较旧的一方
5. 必要时评估替换依赖
6. 无法安全统一时暂停其中一个升级

不得通过忽略 resolver 错误来继续构建。

## Lockfile

修改依赖定义后，应同步更新对应 Lockfile。

例如：

```text id="m0g6ov"
uv.lock
package-lock.json
Cargo.lock
```

不得手工修改 Lockfile 来伪造兼容结果。

Lockfile 的变化应与依赖定义变化相对应。

## Provider 与协议相关依赖

涉及以下依赖时，应额外检查协议兼容性：

* OpenAI-compatible
* Anthropic-compatible
* HTTP client
* SSE / streaming
* WebSocket
* JSON serialization
* TLS

重点验证：

* 请求格式
* 响应格式
* Streaming 行为
* Timeout
* Retry
* Error mapping
* Header
* Authentication
* Proxy

## 构建相关依赖

涉及以下内容时，应额外检查构建和发布：

* Rust toolchain
* Tauri
* Node.js build tooling
* Python packaging
* PyInstaller
* WebView
* Installer / Portable tooling

必须确认：

* CI 可以构建
* Release workflow 可以构建
* Artifact 可正常运行

## 安全更新

存在明确安全漏洞时，应提高处理优先级。

安全升级仍需要验证兼容性，但不能因为升级困难而长期忽略高风险漏洞。

如果暂时无法升级，应明确记录：

* 漏洞影响范围
* 当前是否可被利用
* 临时缓解措施
* 后续升级计划

## 自动合并策略

自动合并仅适用于已经明确界定的低风险更新。

推荐原则：

* Patch：默认可自动
* Minor：按依赖类别判断
* Major：禁止自动

最终是否合并仍应受：

```text id="3tk2xa"
ci-required
```

以及 GitHub Ruleset 约束。

## 依赖删除

不再使用的依赖应及时删除。

删除依赖时应同步清理：

* import
* configuration
* feature flag
* build script
* documentation
* lockfile

避免保留已经失效的“备用依赖”或历史兼容代码。

## 评估依据

依赖升级判断应基于：

* Release notes
* Changelog
* Migration guide
* 实际编译结果
* 自动化测试
* Delta 当前使用方式

不得仅根据版本号判断升级是否安全。