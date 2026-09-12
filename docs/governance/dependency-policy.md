# 依赖治理

本文规定 Delta 的依赖升级、Dependabot、兼容性检查、自动合并，以及 R6 期间核心产品依赖与 Worker 生态依赖的边界。

## 基本原则

依赖管理优先级：

1. 安全；
2. 兼容；
3. 稳定；
4. 可维护；
5. 体积 / 启动 / 性能；
6. 必要时再追求新版本。

不以“永远最新”作为目标。

## 依赖域

R6 后必须区分两类依赖。

### Core Product Dependencies

目标仅服务 Rust + TypeScript 核心产品：

- Rust Runtime / Tauri；
- TypeScript / React；
- IPC / serialization / database / crypto / network / UI 等基础依赖。

核心依赖会影响 Runtime、Trust、Work、Automation 或 Experience，应提高审查等级。

### Capability Worker Dependencies

用于具体工作能力，例如：

- Python statistics / data stack；
- Office / PDF / OCR；
- image / video processing；
- specialized SDK；
- PowerShell / Shell 调用工具。

Worker 依赖不得因为方便而把 Python / 第三方 SDK 重新带回核心 Runtime Authority。

原则：

> **生态依赖可以丰富，控制面依赖必须收敛。**

## R6 依赖冻结规则

在 Rust + TypeScript hard-cut 完成前：

- 原则上不新增 Python application server / manager / persistence framework；
- 不新增仅为 TS → Python → Rust 转发服务的依赖；
- 不新增第三套模型协议 SDK；
- Python Office / Research / Media 依赖应能明确归类为 Capability Worker；
- Rust Runtime 新依赖必须说明它服务哪个长期模块；
- 删除 Python Backend 时同步删除 FastAPI / Uvicorn / PyInstaller sidecar 等不再需要的依赖和打包链；
- 旧 Runtime/tool abstraction 依赖在调用方退出后及时删除，不保留“可能以后有用”的备用路径。

## Dependabot

Dependabot 用于发现更新和创建 PR，不负责决定是否安全合并。

`.github/dependabot.yml` 只定义 ecosystem、directory、schedule、version policy 和 grouping；自动合并策略由 CI / workflow / Ruleset 决定。

## 升级风险分类

版本号只是信号，不代替实际判断。

### Patch

通常低风险，但自动合并前至少满足：

1. `ci-required` 通过；
2. 无 merge conflict；
3. 无安全 / license 异常；
4. 不需要人工代码适配；
5. 无已知 breaking change；
6. 不改变核心 Runtime / Trust / Worker ABI 行为。

### Minor

开发 / 测试工具、兼容性明确的基础依赖，在 CI 全绿且行为未变时可以低风险处理。

以下即使 Minor 也提高审查等级：

- Rust async / IPC / HTTP / serialization；
- Tauri；
- database；
- model protocol / SSE / streaming；
- Worker process / sandbox；
- statistics / scientific libraries；
- Office / PDF parser；
- image / video codec / processing；
- packaging toolchain。

### Major

不得自动合并。

必须人工检查：

- breaking changes；
- API 删除 / 重命名；
- 默认行为；
- serialization / protocol；
- error type；
- numerical behavior；
- Runtime performance；
- build / packaging；
- license。

## Scientific / Research Dependencies

研究分析依赖需要额外治理。

例如：

```text
pandas / polars
numpy
scipy
statsmodels
matplotlib
DOE / optimization libraries
```

升级时除普通测试外，应关注：

- 数值结果是否改变；
- 默认算法 / optimizer 是否改变；
- statistical distribution / p-value / CI 行为；
- missing-data / dtype semantics；
- random seed / reproducibility；
- DOE design generation；
- model fit / convergence warning；
- output schema。

对会改变统计结果的升级，不得只凭“测试没崩”自动合并，应有基准数据或结果容差测试。

## Media Dependencies

图像 / 视频依赖升级额外关注：

- codec support；
- output size / quality；
- metadata handling；
- platform binary compatibility；
- resource usage；
- security advisories；
- license / redistribution。

重型媒体引擎优先作为受控外部 Capability，而不是无条件塞进核心桌面包。

## Provider / Protocol Dependencies

Delta 只维护：

```text
OpenAI-compatible
Anthropic-compatible
```

涉及 HTTP / SSE / TLS / JSON / provider transport 时验证：

- request / response；
- streaming；
- tool calls；
- reasoning / usage；
- timeout / cancel / backpressure；
- headers / auth；
- proxy；
- protocol compatibility。

不得因为某个厂商推出新 SDK 就新增第三套原生协议。

## Lockfile

修改依赖定义后同步对应 Lockfile：

```text
uv.lock
package-lock.json
Cargo.lock
```

不得手工伪造 Lockfile 兼容结果。

R6 删除 Python core dependency 时，应确认 `uv.lock` 中只剩仍被 Worker / dev / packaging 真实需要的 Python 依赖。

## CI 与自动合并

依赖 PR CI 失败时不得自动合并。

必须区分：

- 依赖不兼容；
- 真实回归；
- Lockfile conflict；
- Auto Merge / Ruleset / permission 问题；
- 临时外部服务失败。

CI 通过也不代表高风险升级可以自动合并。

## 多依赖冲突

处理顺序：

1. 找出冲突依赖；
2. 确认版本要求；
3. 查找可兼容版本；
4. 优先升级限制较旧的一方；
5. 必要时替换依赖；
6. 无法安全统一时暂停升级。

不得忽略 resolver 错误继续构建。

## Build / Packaging Dependencies

当前过渡期可能同时涉及：

- Rust toolchain；
- Tauri；
- Node.js build tooling；
- Python packaging / PyInstaller；
- WebView；
- Portable tooling。

必须确认 CI、Release 和实际 Artifact 可运行。

R6 hard-cut 后，不再需要的 Python sidecar packaging 依赖应物理删除，而不是永久留作备用。

## 安全更新

存在明确安全漏洞时提高优先级。若不能立即升级，应记录影响、可利用性、缓解措施和后续计划。

Worker 生态安全漏洞同样重要：被 Sandbox 限制不意味着可以忽略恶意文件解析、codec 或 scientific stack 漏洞。

## 自动合并原则

- Patch：明确低风险时可自动；
- Minor：按依赖域判断；
- Major：禁止自动；
- Scientific numerical behavior / Runtime / Trust / packaging：默认人工审查。

最终仍受 `ci-required` 与 Ruleset 约束。

## 依赖删除

不再使用的依赖及时删除，并同步清理：

- import；
- config；
- feature flag；
- build script；
- documentation；
- lockfile；
- packaging；
- CI matrix。

避免保留已经失效的“备用 SDK”“备用 Runtime”或静默 fallback。

## 评估依据

依赖决策基于：

- release notes / changelog / migration guide；
- 编译与自动化测试；
- contract / numerical regression；
- packaging smoke；
- license；
- Delta 当前真实使用方式。

不得只根据版本号或流行度判断。