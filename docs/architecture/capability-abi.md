# Capability ABI

## 1. 目的

Capability ABI 定义 Delta Rust Core 与本地 Capability Worker、外部 Adapter 之间的稳定交互边界。

目标：

- Rust Core 掌握控制权；
- Worker 专注专业能力；
- Worker 可以替换、升级、崩溃和重启；
- 核心 Task / Run / Policy / Ledger 状态不依赖 Worker 内存；
- 不将 Python 实现细节泄漏到 Rust Core；
- 普通 Capability 开发不要求修改 Rust Core。

---

## 2. 总体架构

```text
React / TypeScript
        ↓
Tauri IPC
        ↓
Delta Rust Core
        ↓
Capability Registry
        ↓
Capability ABI
   ┌────┴──────────────┐
   ▼                   ▼
Local Worker       External Adapter
Python / Node      MCP / Connector
```

Capability ABI 是执行能力边界，不是第二套 Runtime。

---

## 3. 控制面与数据面

### Control Plane

默认：

```text
JSON-RPC 2.0 / NDJSON over stdio
```

用于：

- request；
- response；
- progress；
- heartbeat；
- cancel；
- typed error；
- capability discovery。

### Data Plane

大型数据使用：

- input handle；
- path handle；
- temporary file；
- artifact staging；
- shared file mapping（后续按需）。

禁止长期通过控制通道发送大型 Base64 Blob。

---

## 4. Capability Manifest

Worker 必须能够暴露 Capability Manifest。

最低字段：

```json
{
  "name": "document.pdf.extract",
  "version": "1.0.0",
  "description": "Extract structured content from a PDF",
  "input_schema": {},
  "output_schema": {},
  "permissions": [],
  "side_effect": "none",
  "idempotent": true,
  "default_timeout_ms": 120000,
  "supports_progress": true,
  "supports_cancel": true
}
```

### side_effect

至少支持：

```text
none
local_read
local_write
external_read
external_write
message_send
destructive
```

可以扩展，但不得使用模糊的自由文本替代核心分类。

---

## 5. Capability Request

标准请求至少应包含：

```json
{
  "run_id": "run_...",
  "tool_call_id": "tool_...",
  "capability": "document.pdf.extract",
  "deadline": "2026-09-04T12:00:00Z",
  "workspace": {
    "id": "ws_..."
  },
  "inputs": [],
  "output_staging": {
    "handle": "stage_..."
  },
  "permissions": [],
  "limits": {}
}
```

Rust Core 负责生成：

- `run_id`；
- `tool_call_id`；
- deadline；
- capability grant；
- staging；
- limits。

Worker 不得自行扩大这些范围。

---

## 6. 输入句柄

Worker 不应默认收到整个 Workspace 的自由访问权。

输入应显式授权，例如：

```json
{
  "handle": "src_...",
  "path": "...",
  "sha256": "...",
  "read_only": true
}
```

路径只是本地传输实现的一部分。

系统语义应优先围绕 handle，而不是依赖任意路径字符串。

---

## 7. 输出与 Artifact staging

Worker 不直接创建正式 Artifact。

Worker 只能：

1. 写入 Rust Core 分配的 staging；
2. 返回候选输出声明。

示例：

```json
{
  "status": "ok",
  "outputs": [
    {
      "relative_path": "report.md",
      "media_type": "text/markdown"
    }
  ]
}
```

Rust Core 随后负责：

```text
Verify path
  ↓
Check boundary
  ↓
Hash
  ↓
Validation
  ↓
Artifact registration
  ↓
Ledger event
```

---

## 8. Progress

长任务应支持结构化 Progress。

示例：

```json
{
  "tool_call_id": "tool_...",
  "progress": {
    "current": 63,
    "total": 100,
    "message": "Extracting page 126/200"
  }
}
```

Progress 是观察数据，不是 Run 状态事实。

Worker 不得通过 Progress 改变 Run lifecycle。

---

## 9. Heartbeat

长生命周期 Worker 可以发送 heartbeat。

Rust Core 可以根据：

- process exit；
- heartbeat timeout；
- broken pipe；
- deadline；
- explicit health check；

判断 Worker 是否失效。

Worker 失效不自动意味着 Capability 可以重试。

---

## 10. Cancellation

Rust Core 是 cancellation authority。

流程：

```text
User / Runtime requests cancel
        ↓
Rust Core
        ↓
Capability cancel
        ↓
grace period
        ↓
terminate worker if necessary
```

Worker 可以报告：

- cancelled；
- cannot_cancel；
- partial_output。

最终 Run 状态仍由 Rust Core 决定。

---

## 11. Typed Error

Worker 错误必须结构化。

建议字段：

```json
{
  "status": "error",
  "error": {
    "code": "document_corrupt",
    "message": "PDF structure is invalid",
    "retryable": false,
    "details": {}
  }
}
```

`retryable` 只是 Worker hint。

真正是否重试，由 Rust Core 结合：

- side effect；
- idempotency；
- retry policy；
- checkpoint；
- execution history；

决定。

---

## 12. Retry

Capability ABI 不允许 Worker 隐式无限重试。

如果 Worker 内部有必要进行技术级瞬时重试，必须：

- 有界；
- 不改变 side effect 语义；
- 不绕过 Rust Core retry budget；
- 能被观测。

高后果副作用不得由 Worker自行进行业务级 Retry。

---

## 13. Approval 与 Policy

Worker 只声明权限要求。

例如：

```json
{
  "permissions": [
    "filesystem.read",
    "filesystem.write"
  ]
}
```

Rust Core 负责：

```text
Capability requirement
        ↓
Policy
        ↓
Allow / Confirm / Deny
        ↓
Approval
        ↓
Scoped grant
        ↓
Execution
```

Worker 不得自行请求用户 Approval，也不得把“用户已同意”作为普通输入字段信任。

---

## 14. Capability Discovery

Worker 启动时应支持能力发现。

例如：

```text
capability.list
```

Rust Core 将 Manifest 注册为：

- Capability；
- Tool Schema；
- Policy metadata；
- UI metadata（必要时）。

普通新增 Python Capability 不应要求修改 Rust Core。

---

## 15. MCP Adapter

MCP 通过 Adapter 进入 Delta。

```text
Rust Core
   ↓
MCP Adapter
   ↓
MCP Server
```

MCP tool 仍必须绑定：

- Run；
- Policy；
- Approval；
- Ledger；
- Artifact / Validation（如果适用）。

禁止 MCP Server 直接成为 Delta Runtime authority。

---

## 16. 版本兼容

Capability ABI 必须版本化。

至少定义：

```text
protocol_version
capability_version
```

破坏性变更必须：

- 更新协议版本；
- 明确兼容窗口；
- 提供测试；
- 更新 Worker；
- 不使用静默 fallback 掩盖不兼容。

---

## 17. 安全原则

Capability ABI 不等同于安全沙箱。

Worker 运行边界应逐步包含：

- least privilege；
- scoped input；
- scoped output；
- minimal environment；
- timeout；
- resource limit；
- process supervision；
- network grant。

---

## 18. 验收

Capability ABI 初版至少满足：

- 一个 Python Worker 可被 Rust 拉起；
- capability discovery 正常；
- Rust 可发起一次 request；
- Worker 可返回 progress；
- Worker 可生成 staging output；
- Rust 能登记正式 Artifact；
- Worker crash 不导致 Run 事实丢失；
- cancel 能终止执行；
- typed error 能进入统一错误分类；
- Worker 无法直接修改核心 Run / Ledger 状态。
