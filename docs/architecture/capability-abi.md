# Capability ABI

## 1. 目的

Capability ABI 定义 Delta Rust Capability Host 与本地 Worker、MCP、Connector 和外部 Adapter 之间的稳定执行边界。

目标：

- Rust 掌握 Runtime / Trust / Work 控制权；
- Worker 专注日常办公、研究分析、内容创作中的专业执行；
- Worker 可以替换、升级、崩溃和重启；
- 核心 Session / Run / Policy / Ledger / Artifact 状态不依赖 Worker 内存；
- 不将 Python、PowerShell、Shell 或第三方 SDK 的实现细节泄漏到 Rust 核心；
- 普通 Capability / Skill 开发不要求修改 Runtime；
- Worker 永远不能因为“能力扩展”获得系统 Authority。

Capability ABI 是执行能力边界，不是第二套 Runtime。

---

## 2. 总体架构

目标形态：

```text
Experience — TypeScript / React
            ↓
      Tauri Commands / Events
            ↓
        Rust Runtime
            ↓
      Capability Host
            ↓
      Capability Registry
            ↓
        Capability ABI
     ┌──────┼──────────┐
     ▼      ▼          ▼
 Native   Worker    External Adapter
  Rust   Python /   MCP / Connector /
         PS / Shell media / office service
```

Python、PowerShell、Shell 在这里是**工作执行语言**，不是 Delta 核心产品语言。

---

## 3. Capability 与 Skill 的区别

- **Capability**：执行原语，例如 `document.read`、`statistics.fit`、`image.generate`、`video.compose`。
- **Skill**：可版本化的工作方法，组合多个 Capability、instructions、workflow、permissions、validation、templates 和 optional scripts。

```text
Skill
  ↓
Runtime selects required capabilities
  ↓
Trust evaluates permissions
  ↓
Capability Host executes
```

Skill 不直接绕过 Capability ABI，也不拥有 Run / Policy / Artifact Authority。

---

## 4. Control Plane

默认 Worker Control Plane：

```text
versioned JSON / NDJSON over stdio
```

具体实现可以使用 JSON-RPC 语义，但必须提供明确版本和 request identity。

用于：

- request / response；
- progress；
- heartbeat；
- cancel；
- typed error；
- capability discovery；
- structured logs / diagnostics。

控制通道不得成为大型数据搬运通道。

---

## 5. Data Plane

大型数据优先通过：

- input handle；
- path handle；
- temporary file；
- artifact staging；
- shared file mapping（确有性能需要时）。

禁止长期通过控制通道传输大型 Base64 Blob。

Worker 不应默认得到整个 Workspace 的自由访问权。

---

## 6. Capability Manifest

Worker 必须暴露 Capability Manifest。

最低字段：

```json
{
  "name": "research.statistics.anova",
  "version": "1.0.0",
  "description": "Run an ANOVA with diagnostics",
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

建议增加：

```json
{
  "domain": "research-analysis",
  "deterministic": true,
  "network": "none",
  "resource_profile": "medium"
}
```

`domain` 只能用于发现和组织能力，不能改变 Policy。

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

不得使用模糊自由文本替代核心风险分类。

---

## 7. Capability Request

标准请求至少包含：

```json
{
  "run_id": "run_...",
  "tool_call_id": "tool_...",
  "capability": "research.statistics.anova",
  "deadline": "2026-09-12T12:00:00Z",
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

Rust 负责生成和约束：

- `run_id`；
- `tool_call_id`；
- deadline；
- capability grant；
- staging；
- limits；
- network grant；
- cancellation scope。

Worker 不得自行扩大这些范围。

---

## 8. 输入句柄

显式授权输入，例如：

```json
{
  "handle": "src_...",
  "path": "...",
  "sha256": "...",
  "read_only": true
}
```

路径只是本地传输实现。

系统语义优先围绕 handle、Source、Artifact staging 和 grant，而不是任意路径字符串。

研究分析尤其应保存输入版本 / hash，使 DOE、统计分析和正式报告能够回溯到实际使用的数据。

---

## 9. 输出与 Artifact staging

Worker 不直接创建正式 Artifact。

Worker 只能：

1. 写入 Rust 分配的 staging；
2. 返回候选输出声明；
3. 返回结构化结果和可验证 metadata。

示例：

```json
{
  "status": "ok",
  "outputs": [
    {
      "relative_path": "anova-results.json",
      "media_type": "application/json"
    },
    {
      "relative_path": "interaction-plot.png",
      "media_type": "image/png"
    }
  ]
}
```

Rust 随后负责：

```text
Verify path
  ↓
Check boundary / grants
  ↓
Hash
  ↓
Validation
  ↓
Artifact registration
  ↓
Ledger event
```

图文、图片、视频、DOCX、XLSX、PDF 等均遵守同一 staging → validation → artifact 流程。

---

## 10. Progress

长任务应支持结构化 Progress。

```json
{
  "tool_call_id": "tool_...",
  "progress": {
    "current": 4,
    "total": 8,
    "message": "Fitting response-surface model"
  }
}
```

Progress 是观察数据，不是 Run lifecycle 事实。

Worker 不得通过 Progress 改变 Run 状态。

---

## 11. Heartbeat 与 Process Supervision

长生命周期 Worker 可以发送 heartbeat。

Rust 可以根据：

- process exit；
- heartbeat timeout；
- broken pipe；
- deadline；
- explicit health check；

判断 Worker 是否失效。

Worker crash 不得导致核心 Run / Ledger 事实丢失，也不自动意味着业务动作可以重试。

---

## 12. Cancellation

Rust 是 cancellation authority。

```text
User / Runtime requests cancel
        ↓
Rust Runtime / Trust
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
- partial_output；
- uncertain。

最终 lifecycle 和副作用状态由 Rust 决定。

---

## 13. Typed Error

Worker 错误必须结构化：

```json
{
  "status": "error",
  "error": {
    "code": "design_matrix_singular",
    "message": "The proposed model is not estimable",
    "retryable": false,
    "details": {}
  }
}
```

`retryable` 只是 Worker hint。

真正是否重试，由 Rust 根据 side effect、idempotency、retry policy、checkpoint 和 execution history 决定。

---

## 14. Retry

Capability ABI 不允许 Worker 隐式无限重试。

Worker 内部技术级瞬时重试必须：

- 有界；
- 不改变 side-effect 语义；
- 不绕过 Runtime retry budget；
- 可观测。

高后果副作用不得由 Worker 自行进行业务级 Retry。

---

## 15. Approval 与 Policy

Worker 只声明最低权限需求。

```json
{
  "permissions": [
    "filesystem.read",
    "filesystem.write"
  ]
}
```

Rust 负责：

```text
Capability requirement
        ↓
Trust / Policy
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

Learning 生成的新 Skill / Capability 配置同样不能自行提高 permissions。

---

## 16. Capability Discovery

Worker 启动时应支持能力发现，例如：

```text
capability.list
```

Rust 将 Manifest 注册为：

- Capability；
- Tool Schema；
- Policy metadata；
- Skill compatibility metadata；
- UI metadata（必要时）。

普通新增 Worker Capability 不应要求修改 Runtime 核心。

---

## 17. 产品域示例

### Office

```text
office.document.render
office.spreadsheet.transform
office.pdf.extract
```

### Research Analysis

```text
research.statistics.describe
research.statistics.test
research.doe.generate
research.doe.sequential-next
research.model.fit
research.report.render
```

序贯试验 Capability 必须把当前数据、既定研究约束、允许调整范围和停止规则显式作为输入，不允许 Worker 隐式改变研究协议。

### Content Creation

```text
content.graphic.compose
content.image.generate
content.video.compose
content.platform.adapt
```

专业媒体模型可以通过 External Adapter 进入，不改变核心 ABI。

---

## 18. MCP / Connector / External Adapter

外部能力统一通过 Adapter 进入 Delta：

```text
Rust Capability Host
        ↓
Adapter
        ↓
MCP / Connector / External Service
```

外部能力仍必须绑定：

- Run；
- Trust / Policy；
- Approval；
- Ledger；
- Artifact / Validation（适用时）。

禁止任何外部 Server 直接成为 Delta Runtime authority。

---

## 19. 版本兼容

Capability ABI 必须版本化，至少定义：

```text
protocol_version
capability_version
```

破坏性变更必须：

- 更新协议版本；
- 明确兼容窗口；
- 提供 contract tests；
- 更新受影响 Worker；
- 不使用静默 fallback 掩盖不兼容。

---

## 20. 安全原则

Capability ABI 不等同于安全沙箱。

Worker 执行边界应逐步包含：

- least privilege；
- scoped input；
- scoped output；
- minimal environment；
- timeout；
- resource limit；
- process supervision；
- explicit network grant；
- no core DB authority；
- no unscoped secrets inheritance。

---

## 21. 验收

Capability ABI 至少满足：

- Python / PowerShell / Shell Worker 可由 Rust 拉起并受控；
- capability discovery 正常；
- Rust 可发起 versioned request；
- Worker 可返回 progress；
- Worker 可生成 staging output；
- Rust 能 Validation 并登记正式 Artifact；
- Worker crash 不导致 Run 事实丢失；
- cancel 能终止或正确标记 uncertain 执行；
- typed error 能进入统一错误分类；
- Worker 无法直接修改核心 Session / Run / Policy / Ledger / Artifact 状态；
- 新 Capability 可以被 Skill 组合，而不修改 Runtime 核心。
