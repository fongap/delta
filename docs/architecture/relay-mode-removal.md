# `mode: "relay"` 运行时路径的最终移除

本文是 P0 Post-Decoupling 收尾之后，**必须**在 P1 阶段完成的一次性结构性清理的
前置承诺文件。它不是历史记录，是面向下一阶段的责任清单与禁止项。

## 背景

P0（[ADR-004](./adr/ADR-004-openworker-decouple-hub-boundary.md)）完成后：

* 路由层已不再向 `slack:default` / `github:default` 写入 `mode: "relay"`。
  旧 `/v1/connectors/*/oauth/callback` 和 `/v1/connectors/*/connect-managed` 等
  触发 `managed_connect_*` 的入口在 PR #71 中已删除。
* 但 **`mode: "relay"` 的运行时分支仍存在于以下位置**（P0 仅做了
  "确保默认运行路径不会进入 relay" 的最小保证，**未**删除 dead code）：

  | 文件 | 残留内容 |
  |------|----------|
  | `integrations/connectors/setup.py` | `managed_connect_slack_install`（写入 `mode: "relay"`）、`managed_connect_connector` 通用回写路径、`profile.get("mode") == "relay"` 多处运行时判断（`L38 / L113 / L151`）|
  | `integrations/connectors/adapters.py` | Slack/GitHub 适配器 `mode == "relay"` 分支与 `SlackRelayAdapter` / `GitHubRelayAdapter` 引用（`L452 / L466 / L483`）|
  | `integrations/connectors/config.py` | `mode == "relay"` 描述符（`L85 / L87`）|
  | `integrations/connectors/integration_github.py` | `mode == "relay"` GitHub adapter 分支（`L42`）|
  | `integrations/connectors/manager_inbox.py` | `mode == "relay"` 状态判断（`L205 / L228 / L384`）|
  | `integrations/connectors/relay_client.py` | `SlackRelayClient` 整文件 |
  | `integrations/connectors/github_installs.py` | `managed_connect_install`（写入 `mode: "relay"`）（`L62`）|
  | `apps/desktop/src/features/connectors/components/{ConnectorsList,SlackDetail,GithubDetail}.tsx` | UI relay 分支渲染（仅在 `c.mode === "relay"` 时）|
  | `apps/desktop/src/components/SubscriptionsChip.tsx` | `s.mode === "relay"` 列表项 |
  | `tests/test_slack_status.py / test_team_allowlist.py / test_connectors.py / test_github_installs.py / test_sender_attribution.py` | 直接调用 dead `managed_connect_*` 函数、断言 `mode == "relay"` 的测试 |

## 当前保证（P0 范围内已验证）

* **任何 production 路由** 都不会把 `mode: "relay"` 写入 secrets store。
  * 已检查：`services/server/app.py` 中所有 `/v1/connectors/*` 路由。
  * 已检查：`integrations/connectors/setup.py` 中所有写 `secrets.put(...)` 的代码路径。
* **没有用户可见的 UI 路径** 会进入 `mode: "relay"` 的渲染分支。
* **`relay_client.py` / `managed_connect_*` 当前是 unreachable code**：
  它们仅由测试直接 import 调用，不再被任何 route 或 fixture 触发。

## P1 阶段必须完成的清理

以下不是建议，是 P1（Reliable Task Runtime）启动前**必须**完成的事项：

1. **删除 `relay_client.py` 整文件**。
2. **删除 `adapters.py` 中 `mode == "relay"` 分支**：
   * `SlackRelayAdapter` / `GitHubRelayAdapter` 引用一并删除
   * `SlackDetail` / `GithubDetail` 中"managed relay"UI 状态从"隐藏/不渲染"改为"不实现"
3. **删除 `setup.py` 中 `managed_connect_slack_install` / `managed_connect_connector`**：
   * 同步删除 `github_installs.py` 中 `managed_connect_install`
   * 这三个函数是 P0 之后唯一能写 `mode: "relay"` 的代码；删除后，写入路径彻底归零
4. **删除 `integration_github.py / config.py / manager_inbox.py` 中所有
   `mode == "relay"` 判断**：
   * `manager_inbox.py` 的 slack/github health 输出从 `mode: "relay"` 改为
     显式 `mode: "manual"` 或移除 `mode` 字段
5. **删除 `tests/test_slack_status.py / test_team_allowlist.py / test_connectors.py
   / test_github_installs.py / test_sender_attribution.py` 中所有
   `managed_connect_*` 测试用例与 `mode == "relay"` 断言**：
   * 替换为 manual / local token 路径的测试
6. **删除 `SlackDetail` / `GithubDetail` / `ConnectorsList` / `SubscriptionsChip`
   中 `c.mode === "relay"` UI 分支**：
   * Slack 真实状态字段收窄为 `connected` + `mode: "manual"` (or absent)
   * GitHub 真实状态字段收窄为 `connected` + installations 列表
7. **更新 `hub-federation-boundary.md` 与本文件**：
   * 删除 "Managed Relay" 行（仅保留 manual/local + future Federation Adapter 行）
   * 关闭本文件

## 同步清理：Inbox 回复 token 与提及 bot 名称

P0 阶段一并保留的 wire-format 品牌残留，必须在 P1 中与 relay 路径**同步处理**：

| 残留 | 位置 | 用户可见？ | 处理 |
|------|------|------------|------|
| `[ow:…]` Inbox 回复 token | `core/inbox_routing.py:112`（写入用户可见消息） | 是（Slack 消息末尾） | 替换为 `[d:<id>]`，**同时**保留 `[ow:…]` 作为 parse-only legacy 格式 |
| 提及 bot 名 `@OpenWorker` | `core/mentions.py:3`（注释）、`services/server/manager_gateway.py:423`（注释）、`integrations/connectors/relay_client.py:336`（dead code 注释） | 否（实际匹配用 `<@U…>` Slack token） | 注释同步更新到 Delta；`relay_client.py` 已在本文件 §P1 步骤 1 删除 |
| `tests/test_connectors.py:1249` 测试中 `"account": "rohit@openworker.com"` | 测试 fixture email | 否 | 改为 `"rohit@delta.app"` 或中性 `user@example.com` |

**注意**：`[ow:…]` → `[d:…]` 是**仅写方向**的破坏性变更，**仅读方向**仍接受 `[ow:…]` / `[ocw:…]`（这是显式的兼容承诺，不是技术债）。该承诺可由 P2 决定是否终止。

## 禁止

* **不得**为兼容旧 secrets profile 维持 `mode: "relay"` 的 parse 分支。
  `mode` 字段在 P1 后只接受 `"manual"` 与空字符串；其他值视作非法 secrets 状态。
* **不得**借机重写 Slack/GitHub health 字段的语义；本次清理的目标是"删除"而非"重构"。
  如果发现 health 字段需要重新设计，应作为单独 PR 评估。
* **不得**在没有删除 `managed_connect_*` 函数的情况下发布 P1。
* **不得**让本文件被解释为"长期兼容路径"。P1 中 `mode == "relay"` 应被视为非法值。

## 验收

P1 发布前必须验证：

| 检查项 | 标准 |
|--------|------|
| `rg "mode.*relay\|mode.*==.*['\"]relay" --type py apps packages services core integrations providers` | 仅在 `tests/` 与本文档中出现 |
| `rg "managed_connect_(slack_install\|connector\|install)" --type py` | 0 命中 |
| `find integrations/connectors -name relay_client.py` | no such file |
| Slack 真实状态仅有 `connected` / `mode: "manual"` | UI 渲染与 API 输出一致 |
| GitHub 真实状态仅有 `connected` + installations 列表 | UI 渲染与 API 输出一致 |
