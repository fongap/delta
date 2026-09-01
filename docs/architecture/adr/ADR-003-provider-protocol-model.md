# ADR-003 Provider Protocol & Transport Model

**Status:** Active

## Context

Delta 支持多种 AI Provider。用户理解的单位是 **Vendor / Preset**（OpenAI、Anthropic、Gemini、Ollama、DeepSeek、OpenRouter、MiniMax……）。代码内部，Provider 客户端通过 **Wire Protocol** 与服务商通信（`openai`、`anthropic`、`gemini`）。云平台托管模型需要特定的 **Platform Transport**（`direct`、`bedrock`、`vertex`）来访问。

此前这三层概念在 `providers/registry.py` 中混在一起：
- `PROFILE_PROTOCOL_*` 常量暴露 first-class wire protocols。
- `PROTOCOL_*` 常量暴露所有可构建协议，包含 transport-specific 的（`bedrock`、`vertex`）。
- Vendor presets 由 profile 名称和 endpoint 默认值隐式定义。

这导致 `BedrockProvider` 和 `VertexProvider` 看起来与 `OpenAIProvider`、`AnthropicProvider` 平级，但实际上它们是 platform transport——内部复用 native provider 类（`AnthropicProvider`、`GeminiProvider`、`OpenAIProvider`）作为 wire protocol 客户端，只是通过平台特定 SDK client（`AnthropicBedrock`、`AnthropicVertex`、`genai.Client(vertexai=True)`、MaaS endpoint）访问。

## Decision

明确三层概念，并在代码注释中统一术语（行为不变，仅文档层面收敛）：

### 1. Vendor / Preset
用户可见的服务商名称。决定默认 endpoint、默认 model、UI 字段、API Key 帮助文本。
- OpenAI、Anthropic、Gemini、Ollama、DeepSeek、OpenRouter、MiniMax……
- Profile 名称是 preset；**不**选择独立的 client 实现。

### 2. Wire Protocol
Provider 客户端实际使用的 HTTP API 契约。决定请求/响应 payload 格式。
- `openai` — Chat Completions + `/v1/responses`（`OpenAIProvider` / `OpenAIResponsesProvider`）
- `anthropic` — Messages API（`AnthropicProvider`）
- `gemini` — Google GenAI generateContent（`GeminiProvider`）

只有这三个 first-class wire protocol（`PROFILE_PROTOCOL_*`）。所有 vendor preset 最终映射到其中之一。

### 3. Platform Transport
访问云平台托管模型后端的通道。处理认证、endpoint 解析和平台特定 SDK 使用。
- `direct` — 默认 HTTPS 到 vendor 公共 API（所有非云平台 provider）
- `bedrock` — AWS（boto3 / `AnthropicBedrock` / Converse）
- `vertex` — GCP（`genai.Client(vertexai=True)` / `AnthropicVertex` / MaaS endpoint）

**Bedrock 和 Vertex 不是 wire protocol**，是 platform transport。一个 transport 可承载多个 wire protocol（例如 Bedrock 为 Claude 提供 `anthropic` wire protocol，为其他模型提供 Converse wire format）。

## Implementation

已在以下文件中更新 docstring，标注三层归属（仅文档层面，行为不变）：
- `providers/registry.py` — 顶部注释说明三层模型与各常量归属
- `providers/base.py` — `ProviderClient` docstring 按三层模型描述实现
- `providers/openai_provider.py` — Wire Protocol: `openai` (Chat Completions); Transport: `direct`
- `providers/openai_responses.py` — Wire Protocol: `openai` (Responses API); Transport: `direct`
- `providers/anthropic_provider.py` — Wire Protocol: `anthropic`; Transport: `direct`（也被 `bedrock`/`vertex` transport 复用）
- `providers/gemini_provider.py` — Wire Protocol: `gemini`; Transport: `direct`（也被 `vertex` transport 复用）
- `providers/bedrock_provider.py` — Platform Transport: `bedrock`（按 model family 承载 `anthropic` 或 Converse wire protocol）
- `providers/vertex_provider.py` — Platform Transport: `vertex`（按 model family 承载 `gemini`、`anthropic` 或 `openai` wire protocol）

## Consequences

- 代码命名、registry、UI、API contract 和 ADR 对三层使用一致术语。
- 为后续 `registry.py` 职责拆分（将 transport-specific 逻辑从 protocol-specific builder 中分离）提供文档基础。
- 不改变任何运行时行为、公共 API 或 provider 构建逻辑。
