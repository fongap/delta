# Delta 联邦化边界设计

本文档描述 Delta 的联邦化（Federation）边界：Delta 是独立、本地优先的运行时；
Federation 是一条开放、供应商无关的可选边界；任何外部系统（包括 OpenWorker Cloud）
都只是潜在的 Federation 适配对象之一。

## 核心原则

> **Delta MUST operate independently of any external Federation provider.**
>
> **Federation is an open, provider-neutral, optional capability boundary.**
>
> **Removal or failure of any Federation adapter MUST NOT affect native Delta functionality.**

中文：

> Delta 必须完全独立于任何外部 Federation 提供方运行。
>
> Federation 是一条开放、供应商无关、可选的能力边界。
>
> 任何 Federation 适配器被删除、不可达或失效时，Delta 的原生能力必须保持完整。

## 架构边界

```
Delta (Core)
    │
    │ optional, provider-neutral
    ▼
Federation Boundary
    ├── OpenWorker Adapter        (potential external)
    ├── Delta-operated Adapter    (potential self-hosted)
    ├── Self-hosted Adapter       (potential self-hosted)
    ├── Third-party Adapter       (potential external)
    └── Custom Adapter            (potential user-defined)
```

Federation 是开放、供应商无关的能力边界。任何实现 Federation 能力协议的适配器
（Delta 自营的、第三方、用户自建）都可以接入。任何单一适配器（包括 OpenWorker）
都不是 Federation 的核心，只是若干候选之一。

### 依赖方向（正确）

```
Any Federation Provider
        ↓ optional
Delta Core
```

### 依赖方向（禁止）

```
Delta Core
        ↓
OpenWorker Cloud (or any single provider)
```

Delta Core 不依赖任何特定 Federation 提供方；Federation 协议不绑定 OpenWorker。

## 核心 Capability Ports

Federation 边界通过 `integrations/managed/` 中定义的 Capability Port 协议与 Delta 交互：

| Port | Protocol | 默认实现 | 说明 |
|------|----------|----------|------|
| OAuth Broker | `OAuthBroker` | `NullOAuthBroker` | 代理 OAuth 流程（begin/exchange/refresh/disconnect） |
| Relay Transport | `RelayTransport` | `NullRelayTransport` | WebSocket Relay 入站事件 |
| GitHub App Broker | `GitHubAppBroker` | `NullGitHubAppBroker` | Installation Token Mint |
| External Identity | `ExternalIdentityProvider` | `NullIdentityProvider` | 身份联邦 / Device Token 验证 |

**默认 = Null***：未配置任何 Federation 适配器时，所有 managed 能力返回 "unavailable"，
manual/local 路径完全不受影响。

> **Federation 适配器位置（未来）**：
> 若一个具体 Federation 适配器（如 OpenWorker）被实现，它将位于
> `integrations/managed/adapters/<provider>.py`，与 Capability Port 并列；
> Core 不感知、不依赖任何具体实现。
>
> 截至当前（2026-09），所有 Capability Port 的实现都是 `Null*`；
> 没有真实的 Federation 适配器被实现。Capability Port 保留用于未来扩展。

## Native Device Token：架构基座

Native Device Token 是 Delta 的**原生认证基座**。作用包括：

- 完全自托管 / 局域网 / NAS / Tailscale / 企业内网 / 离线私网
- Break-glass access（紧急访问）
- 任何 Federation 失效时仍可管理 Hub
- **绝不**把任何单一外部 Federation 提供方变成 Delta 的认证根

## Delta Hub 的未来职责（仅限 Federation 边界内）

Delta Hub（未来实现）若存在，仅负责：

- Device Authentication（Native Device Token）
- OAuth Broker（代理 OAuth 流程、刷新）
- Token Refresh Broker（刷新 managed connector token）
- Webhook Receiver（接收平台回调）
- WebSocket Relay（Slack/GitHub inbound 事件转发）
- GitHub App Token Mint（代理 Installation Token 申请）

**不负责**：

- 模型代理 / AI Gateway
- 多租户账号平台
- 公共 SaaS
- 云端 Workspace / Memory / 文档存储

## 模型请求永久绕过 Hub

```
Delta
      ↓
OpenAI / Anthropic / AI Gateway (直接连接)
```

**永远禁止**：

```
Delta
  ↓
Delta Hub
  ↓
Model
```

Delta Hub 与 AI Gateway 是两个独立系统；Delta 直接与模型提供方通信。

## Hub 数据边界

**可保存**：

- device metadata
- OAuth pending state / temporary exchange code
- connection metadata
- GitHub installation metadata
- relay cursor
- federation subject mapping

**禁止保存**：

- conversation / prompts / model outputs
- workspace files / memory / user documents
- Delta session history
- 长期模型 API Key

Connector token 长期存储策略应单独设计安全模型，不因任何具体 Federation
实现而直接继承。

## 验收标准

| 检查项 | 标准 |
|--------|------|
| 任何单一 Federation 提供方运行时依赖 | = 0 |
| Core 包含特定 Federation 提供方专属语义 | = 0 |
| Native Device Token | = architecture baseline |
| Federation 适配器 | = optional, provider-neutral |
| 离线验证（阻断所有外部 Federation endpoints） | Delta 核心功能完整 |
