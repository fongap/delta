# Delta Federation 边界

本文描述 Delta 与可选托管 / 联邦能力之间的长期边界。

Federation 不是 Delta 的一级产品方向，也不是核心 Runtime 前置依赖。它属于 **Capability / Automation 的外部适配边界**。

## 核心原则

> **Delta MUST operate independently of any external Federation provider.**
>
> **Federation is optional, provider-neutral and subordinate to Delta Trust.**
>
> **Removal or failure of any Federation adapter MUST NOT affect native Delta work.**

中文：

- Delta 必须可以完全不依赖任何外部 Federation provider 运行；
- Federation 开放、供应商无关、可选；
- 外部 Adapter 不得拥有 Runtime / Trust / Work Authority；
- Adapter 删除、不可达或失败时，本地核心能力仍然成立。

## 目标架构位置

```text
Delta Runtime / Trust / Work — Rust
              │
              ▼
        Capability Host
              │
      Federation Adapter
       ┌──────┼──────┐
       ▼      ▼      ▼
 self-hosted third-party custom
```

依赖方向：

```text
External Federation Provider
          ↓ optional
Delta Capability Boundary
          ↓
Delta Runtime remains authoritative
```

禁止：

```text
Delta Runtime
    ↓ mandatory
Single Federation Provider
```

## Adapter Contract

Federation Adapter 可以提供：

- OAuth broker；
- webhook / inbound event relay；
- external identity mapping；
- installation / delegated-token broker；
- managed connector metadata。

但进入 Delta 后仍必须绑定：

```text
Run / Automation Trigger
        ↓
Trust / Policy / Approval
        ↓
Capability / Automation
        ↓
Ledger / Work facts
```

外部服务不能因为“托管能力”绕过本地 Policy、Approval、Ledger、Source / Artifact / Validation。

## 当前实现与目标实现

当前仓库仍存在 `integrations/managed/` 的 Capability Port / `Null*` 兼容边界；截至 2026-09，没有真实 Federation provider 成为 Delta 的必需依赖。

R6 后具体 Adapter 的物理实现位置和语言不在本文冻结。它可以是 Rust Adapter、受控 Worker、MCP / Connector 或 External Adapter，只要满足 `capability-abi.md` 和 Trust 边界。

因此不再规定未来 Adapter 必须位于某个 `.py` 路径。

## Native / Local fallback

任何 Federation 功能都必须有清晰的“没有 Federation 时怎么办”。

本地核心能力不能以云端身份或托管 Relay 为认证根。

如果未来保留 Native Device Token / local device authentication，其职责是支持：

- 本机 / 局域网；
- NAS / Tailscale / 企业内网；
- break-glass access；
- 外部 provider 故障时的本地管理。

具体认证实现需独立安全 ADR，不在 Federation 文档里预先冻结协议。

## Hub（如果未来确有需要）

如果真实需求证明需要独立 Delta Hub，它最多承担 Federation 边界能力，例如：

- device / delegated authentication broker；
- OAuth exchange / refresh；
- webhook receiver；
- inbound event relay；
- installation token broker。

它不负责：

- 模型代理 / AI Gateway；
- 公共模型市场；
- Delta Runtime Authority；
- 云端 Workspace / Memory / Source / Artifact 主存储；
- 多租户 SaaS 控制面。

没有真实需求时，不因为“架构完整”提前建设 Hub。

## 模型请求永久独立于 Federation

Delta 模型边界只有：

```text
OpenAI-compatible
Anthropic-compatible
```

调用路径：

```text
Delta Runtime
      ↓
User-configured compatible endpoint
```

复杂 Provider 聚合 / multi-key / fallback / quota / region routing 外置给 AI Gateway 或其他兼容 Endpoint，与 Federation 是不同职责。

禁止把 Federation Hub 变成隐式模型代理。

## 数据边界

Federation Adapter 如有必要，可以处理最少量：

- connection metadata；
- pending OAuth state；
- relay cursor；
- external identity mapping；
- installation metadata。

默认禁止成为以下数据的权威存储：

- conversation / prompt / model output；
- Workspace files；
- Memory / Experience / Skill authority；
- Session / Run history；
- Artifact / Validation；
- 长期模型 API Key。

Connector token 的长期存储和 delegated access 需服从 Secrets / Trust 设计。

## 验收

| 检查项 | 标准 |
| --- | --- |
| 单一 Federation provider 为 Runtime 必需依赖 | = 0 |
| 外部 Adapter 拥有核心 Authority | = 0 |
| Adapter 绕过 Trust / Ledger | = 0 |
| Federation 与模型 Gateway 强耦合 | = 0 |
| 阻断外部 Federation 后 | 本地核心工作仍可运行 |
| 新 Federation 能力 | 优先走 Capability / Automation boundary |

## 设计过滤器

Federation 只有在真实外部连接问题无法由普通 Connector / MCP / Capability 解决时才扩展。

> **没有真实依赖，不新增 Hub；没有安全边界，不新增 Relay；没有产品价值，不新增 Federation 复杂度。**
