# ADR-003：Provider 协议模型

## 状态

已采纳。

## 决策

Delta 的模型运行时只支持两种协议：

- `openai`：OpenAI Responses 或 Chat Completions；二者是协议内部的 `api_mode`，不构成新的 Provider 协议。
- `anthropic`：Anthropic Messages API。

自定义服务商由以下信息定义：

- 服务商名称（alias）
- 协议（`openai` 或 `anthropic`）
- API endpoint
- API key
- 用户选择的模型列表

alias 是配置、路由前缀和凭据隔离边界，不决定客户端实现。`ProviderRouter` 从 profile 的 `protocol` 选择 `OpenAIProvider` / `OpenAIResponsesProvider` 或 `AnthropicProvider`；服务商品牌只可提供 endpoint、推荐模型和 API key 帮助等表单默认值。

DeepSeek、OpenRouter、NVIDIA、MiniMax、GLM / Z.AI、Moonshot 等兼容服务使用 `openai`。本地服务若暴露 OpenAI-compatible endpoint，也使用 `openai`，并由用户明确填写包含 `/v1` 的地址。Anthropic-compatible 网关使用 `anthropic`。

## 不支持的边界

Delta 不提供第三种协议、平台 transport abstraction、占位 Provider 或未来兼容层。未实现 OpenAI-compatible 或 Anthropic Messages API 的平台，需要先由外部网关转换为受支持协议，才能接入 Delta。

## 结果

- `providers/registry.py` 只暴露 `PROFILE_PROTOCOL_OPENAI` 与 `PROFILE_PROTOCOL_ANTHROPIC`。
- 自定义服务商的协议下拉框只显示 OpenAI 与 Anthropic。
- Provider 身份不会产生平行 builder、router 分支或 SDK 依赖。
- endpoint profile 独立保存凭据，避免把官方服务的环境变量凭据发送到自定义地址。
