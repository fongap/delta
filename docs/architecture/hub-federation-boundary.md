# Delta Hub 联邦化边界设计

本文档描述 Delta Hub 的架构边界，以及 OpenWorker Cloud 作为可选联邦化适配器的定位。

## 核心原则

> **Delta Hub MUST operate independently of OpenWorker Cloud.**
>
> **OpenWorker Cloud MAY be supported as an optional federation or managed-service adapter.**
>
> **Removal or failure of the OpenWorker integration MUST NOT affect native Delta Hub or Delta Desktop functionality.**

中文：

> Delta Hub 必须能够完全独立于 OpenWorker Cloud 运行。
>
> OpenWorker Cloud 可以作为可选身份联邦、OAuth、Relay 或其他 Managed Capability Adapter。
>
> OpenWorker 集成被删除、不可达或失效时，Delta Hub 与 Delta Desktop 的原生能力必须保持完整。

## 架构边界

```
Delta Desktop
     │
     │ HTTPS / WSS
     ▼
Delta Hub (未来、可选、自托管)
├── Native Auth
│   └── Device Token (架构基座)
├── Identity Adapters
│   ├── OpenWorker (可选、可删除)
│   ├── OIDC (未来)
│   └── Future (未来)
├── OAuth Brokers
│   ├── Native
│   └── Optional External
└── Relay Adapters
    ├── Native Hub Relay
    └── Optional External
```

### 依赖方向（正确）

```
OpenWorker Cloud
      ↓ optional
Delta Hub
      ↓ optional
Delta Desktop
```

### 依赖方向（禁止）

```
Delta Desktop
      ↓
OpenWorker Cloud
```

## 核心 Capability Ports

Delta Hub 通过以下 Capability Ports 与 Delta Desktop 交互。Desktop 侧已在 `integrations/managed/` 中定义 Protocol：

| Port | Protocol | 默认实现 | 说明 |
|------|----------|----------|------|
| Native Auth | `ExternalIdentityProvider` | `NullIdentityProvider` | Device Token 验证 |
| OAuth Broker | `OAuthBroker` | `NullOAuthBroker` | Managed OAuth (begin/exchange/refresh/disconnect) |
| Relay Transport | `RelayTransport` | `NullRelayTransport` | WebSocket Relay |
| GitHub App Broker | `GitHubAppBroker` | `NullGitHubAppBroker` | Installation Token Mint |

**默认 = Null***：未配置 Hub 时，所有 managed 能力返回 "unavailable"，manual/local 路径完全不受影响。

## OpenWorker 联邦化适配器

### 位置

```
integrations/federation/openworker/
├── __init__.py
├── oauth.py        # OpenWorkerOAuthBroker (实现 OAuthBroker)
├── relay.py        # OpenWorkerRelayTransport (实现 RelayTransport)
├── github_app.py   # OpenWorkerGitHubAppBroker (实现 GitHubAppBroker)
└── README.md       # 说明：删除此目录即可完全移除 OpenWorker 支持
```

### 关键约束

1. **不实现**：当前目录仅为占位，所有实现抛出 `NotImplementedError`
2. **可删除**：删除整个 `integrations/federation/openworker/` 目录不影响任何原生功能
3. **无运行时依赖**：Desktop 和 Hub 核心代码**绝不** import 此目录
4. **显式启用**：仅当用户显式配置并启用时才加载

## Native Device Token：架构基座

Native Device Token 是 Delta Hub 的**原生认证基座**，而非普通用户唯一 UI 方式。作用包括：

- 完全自托管 / 局域网 / NAS / Tailscale / 企业内网 / 离线私网
- Break-glass access（紧急访问）
- 外部 Federation 全部失效时仍可管理 Hub
- **绝不**把 OpenWorker Cloud 变成 Delta Hub 的认证根

## Delta Hub 的未来职责

Delta Hub（未来实现）仅负责：

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
Delta Desktop
      ↓
OpenAI / Anthropic / AI Gateway (直接连接)
```

**永远禁止**：

```
Delta Desktop
      ↓
Delta Hub
      ↓
Model
```

Delta Hub 与 AI Gateway 是两个独立系统。

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

Connector token 长期存储策略应单独设计安全模型，不因当前 OpenWorker 代码直接继承。

## 验收标准

| 检查项 | 标准 |
|--------|------|
| OpenWorker Cloud 运行时依赖 | = 0 |
| Auth0 运行时依赖 | = 0 |
| OpenWorker Telemetry | = 0 |
| OpenWorker Relay 依赖 | = 0 |
| Native Device Token | = architecture baseline |
| OpenWorker Federation | = optional future adapter |
| 删除 `integrations/federation/openworker/` | 不影响 Desktop/Hub 原生功能 |
| 离线验证（阻断 OpenWorker endpoints） | Desktop 核心功能完整 |