# `mode: "relay"` 运行时路径的最终移除（已完成，2026-09）

> **Status: Closed.** 本文保留为 relay compatibility 清理的历史记录，不再定义未来实现路径。
>
> 未来如果重新出现 managed relay / federation 需求，必须按 [`hub-federation-boundary.md`](./hub-federation-boundary.md)、[`capability-abi.md`](./capability-abi.md) 和 ADR-050 重新评估；不再预设未来 Adapter 必须是 Python 或位于某个 `.py` 路径。

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
| 删除 `manager_inbox.py` 中 `mode == "relay"` 状态判断 | `services/server/manager_inbox.py:205/228/384` | ✓ 移除；Slack / GitHub status 简化为 offline stub |
| 删除 UI relay 分支 | `apps/desktop/src/...` | ✓ 清理 |
| 删除 relay-only tests | `tests/` | ✓ 清理 |
| manual-mode 测试重写 | connector / allowlist / install tests | ✓ |
| Inbox token 写方向 `[ow:…]` → `[d:…]` | routing / gateway | ✓；P2 后只接受 `[d:…]` |
| 注释和 packaging 同步到 Delta | runtime / packaging | ✓ |

## 关闭状态

- Slack 当前真实状态不再包含可工作的 `mode: "relay"` 路径；
- GitHub 当前使用本地 / manual connection metadata；
- secrets 中遗留 `mode` 字段不代表 relay Runtime 仍存在；
- 旧 `[ow:…]` / `[ocw:…]` 解析兼容已经终止；
- 当前产品不维护 manual + relay 两套平行实现。

## 未来规则

如果真实需求再次要求 inbound relay / OAuth broker / delegated identity：

```text
External Event / Provider
        ↓
Federation / Connector Adapter
        ↓
Capability / Automation boundary
        ↓
Trust / Policy / Approval
        ↓
Runtime / Ledger / Work
```

未来实现：

- 不恢复 `mode: "relay"` 历史分支；
- 不恢复单一外部 provider 依赖；
- 不规定 Python 为 Adapter 的核心语言；
- 不绕过 Rust Trust / Runtime Authority；
- 优先复用普通 Connector / MCP / Capability，只有确有必要才增加 Federation 复杂度。

本文的代码路径和完成清单只用于追溯 2026-09 的清理事实；目标架构以 ADR-050 和 `target-architecture.md` 为准。
