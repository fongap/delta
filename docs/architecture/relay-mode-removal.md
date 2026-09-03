# `mode: "relay"` 运行时路径的最终移除（已完成，2026-09）

> **Status: Closed.** All cleanup steps in the P1 commitment list below were
> completed and merged in the relay-mode-removal PR. This file is preserved as
> a historical record of the obligation; it is no longer a forward-looking
> commitment. Future managed relay work, if any, lives in a fresh
> `integrations/managed/adapters/<provider>.py` per the
> [hub-federation-boundary](./hub-federation-boundary.md) contract.

## 完成项

| 项 | 文件 / 位置 | 状态 |
|----|-------------|------|
| 删除 `relay_client.py` 整文件 | `integrations/connectors/relay_client.py` | ✓ `git rm` |
| 删除 `github_relay.py` 整文件 | `integrations/connectors/github_relay.py` | ✓ `git rm`（同样依赖 `RelayHub`） |
| 删除 `adapters.py` 中 `mode == "relay"` 分支 | `integrations/connectors/adapters.py:466-499` | ✓ 移除 Slack + GitHub relay 分支 |
| 删除 `managed_connect_slack_install` | `integrations/connectors/setup.py:421-472` | ✓ 移除 |
| 删除 `managed_connect_connector` | `integrations/connectors/setup.py:388-418` | ✓ 移除 |
| 删除 `managed_connect_install` | `integrations/connectors/github_installs.py:62-99` | ✓ 替换为 `connect_install`（manual metadata 入口） |
| 删除 `integration_github.py` 中 `mode == "relay"` 分支 | `integrations/connectors/integration_github.py:42-53` | ✓ 移除 |
| 删除 `config.py` 中 `mode == "relay"` 描述符 | `integrations/connectors/config.py:83-92` | ✓ 移除 |
| 删除 `manager_inbox.py` 中 `mode == "relay"` 状态判断 | `services/server/manager_inbox.py:205/228/384` | ✓ 移除；slack/github status 简化为 offline stub |
| 删除 UI relay 分支（`ConnectorsList` / `SlackDetail` / `GithubDetail` / `SubscriptionsChip`） | `apps/desktop/src/...` | ✓ 4 个组件清理 |
| 删除 `test_slack_status.py` 整文件 | `tests/test_slack_status.py` | ✓ `git rm`（全是 relay 行为测试） |
| 删除 `test_slack_relay.py` 整文件 | `tests/test_slack_relay.py` | ✓ `git rm` |
| 重写 `test_team_allowlist.py` / `test_github_installs.py` / `test_sender_attribution.py` | 三个文件 | ✓ 改为 manual mode 路径 |
| 修改 `test_send_target_resolution.py` / `test_slack_approval_owners.py` / `test_connectors.py` | 三个文件 | ✓ 删除 relay-only 测试 + 替换 `managed_connect_*` 调用为 `secrets.put(...)` |
| Inbox token 写方向 `[ow:…]` → `[d:…]` | `core/inbox_routing.py:113` / `services/server/manager_gateway.py:185` | ✓ 写方向破坏性变更；P2 同时终止 parse 兼容，只接受 `[d:…]` |
| 注释同步更新到 Delta（@OpenWorker → @Delta, [ow:id] → [d:id]） | `core/interactions.py` / `core/subscriptions.py` / `services/server/manager_gateway.py:405` | ✓ |
| 更新 `pyproject.toml` 注释 | `pyproject.toml:24` | ✓ `websockets` 注释更新 |
| 更新 `packaging/server/delta-server.spec` 注释 | `packaging/server/delta-server.spec:100` | ✓ |
| 更新 `integrations/managed/relay.py` 注释 | `integrations/managed/relay.py` | ✓ |

## 关闭

* Slack 真实状态字段：仅 `connected`（无 `mode` 字段） + `relay: {state: "offline"}`（恒离线占位）+ `teams: {}`
* GitHub 真实状态字段：仅 `connected` + `installations` 列表
* `mode` 字段在 secrets store 中保留为字符串类型（`"manual"` / `""`），但**不再有 `"relay"` 写入路径**；任何带有 `mode: "relay"` 的旧 secrets profile 应在用户重新连接时被新逻辑覆盖
* 测试与文档一致反映"manual-only"基线

## 后续

* 未来 Federation Adapter（如 OpenWorker）若实现，位于
  `integrations/managed/adapters/<provider>.py`，与 Capability Port 并列
* 不再维护"两套实现"（manual + relay）；relay 路径一旦清理完成即删除
* 旧 `[ow:…]` / `[ocw:…]` 解析兼容已在 P2 终止（`core/inbox_routing.py:_ID_TOKEN`
  只匹配 `[d:…]`）；写方向历史在 git 记录中保留
