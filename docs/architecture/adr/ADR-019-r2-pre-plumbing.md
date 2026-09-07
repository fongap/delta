# ADR-019 — R2 Pre-Plumbing: Shadow Readers + Authority Selector 扩展

- 状态：Accepted
- 日期：2026-09-07
- 范围：R2 Trusted Execution 阶段的基础设施（Python authority selector 扩展 + 4 个 R2 域的 Rust 影子读 + 3 个 inspect binary + 跨语言测试）
- 决策类型：迁移阶段基础设施（Pre-R2 plumbing）
- 关联：ADR-012（R1 Pre-R1 plumbing）、ADR-017（R1 State Foundation 完成）、ADR-018（R2 Trusted Execution 计划）、`docs/governance/rust-core-migration.md` §5 R2 / §6 单领域迁移流程

## 背景

R1 State Foundation（ADR-017）完成后，R1 三个领域（Idempotency / Ledger / Task identity）的 Rust 写路径就位，但**写权威仍是 Python**（灰度开关默认关闭）。R2 Trusted Execution（ADR-018）计划 6 个域（Artifact / Validation / Checkpoint / Policy / Approval / Source-Citation）按单领域流程迁移。

R2 与 R1 的关键区别：

1. **R2 域中只有 4 个有"数据形态"**（Artifact / Validation / Checkpoint / Source-Citation），适合用 reader shadow-read 模式。Policy 和 Approval 是"评估/决策"面（不是"写后读"），需要不同的接入方式。
2. **R2 域没有 Rust 写路径**（PR131 阶段），但需要 Python 端能"邀请 Rust 进来读一读"以做 cross-check。
3. R1 的 `DELTA_RUST_AUTHORITY` 灰度开关语义是"写权威"；R2 reader 不能复用这个语义（不能让人误以为 `DELTA_RUST_AUTHORITY=artifact` 就让 Rust 接管 artifact 写入）。

## 决策

### 1. Python authority selector 扩展

`packages/storage_authority.py` 新增：

- `RUST_READ_DOMAINS: frozenset[str] = {"artifact", "validation", "checkpoint", "source_citation"}`
- `READER_ENV_VAR: Final[str] = "DELTA_RUST_READERS"`（与 `DELTA_RUST_AUTHORITY` 完全独立的 env var）
- `is_rust_shadow_reader(domain: str) -> bool` API
- `_parse_reader_domains()` 内部解析器（接受 `"all"` / `"1"` / `"true"` / `"yes"` / `"on"` / 逗号分隔列表）

**不变量**：

- R1 `is_rust_authority()` 语义完全不变；R1 测试继续全绿。
- `is_rust_authority(<R2_reader_domain>)` 抛 `InvalidAuthorityTargetError`（fail-closed）。
- `is_rust_shadow_reader(<R1_write_domain>)` 抛 `InvalidAuthorityTargetError`。
- 未知 domain 在解析阶段抛 `UnknownDomainError`（fail-fast，无静默忽略）。
- Policy / Approval **不**在 `RUST_READ_DOMAINS` 中；它们需要不同的 hook（在各自的 per-domain ADR 中定义）。

### 2. Rust reader scaffolding（4 个域）

`core/runtime-native/src/` 新增 4 个模块：

| 模块 | 输入 | 输出 | 行为 |
|---|---|---|---|
| `artifact.rs` | `run_events.db`（`artifact.registered` 事件）+ workspace 目录 | `ArtifactRecord` 列表 + `ArtifactMismatch` 列表 | 重新计算每个 artifact 的 sha256，与 ledger 记录对比 |
| `validation.rs` | `ValidationCriteria` dict + `Artifact` dict list + workspace 路径 + 可选 `valid_citation_count` | `ValidationResult` | 纯函数式 reimplementation `core/validation.py:run_validation` |
| `checkpoint.rs` | `RecoverySnapshot` JSON | `ParsedSnapshot`（schema 校验 + 字段提取） | 严格 schema 检查：未来 schema 拒绝（不静默误解） |
| `source_citation.rs` | `CitationRange` dict list | `ValidatedCitation` list | 严格 kind-specific 必填字段校验 + 范围 sanity |

每个模块：
- 不写任何状态（read-only / pure-functional）
- 不引入新外部依赖（仅用现有 `rusqlite` / `serde_json` / `sha2`）
- 包含 inline Rust 单元测试（`#[cfg(test)] mod tests`）

### 3. Inspect binaries（3 个）

`core/runtime-native/src/bin/` 新增 3 个诊断工具（验证用，不是生产路径）：

- `inspect_artifact` — `--db <run_events.db> --run-id <id> [--workspace <dir>]`
- `inspect_checkpoint` — `<snapshot.json>`
- `inspect_citation` — `<citations.json>` 或 `-`（stdin）
- `inspect_validation` — 通过 stdin 接收 `{criteria, artifacts, workspace?, valid_citation_count?}`，返回 `ValidationResult`

**不**为 Policy / Approval 创建 inspect binary：它们是评估/决策面，inspect 形态不适用。

### 4. 跨语言测试

`tests/test_r2_shadow_read.py`：

- 12 个测试覆盖 4 个 reader 域的 Python write → Rust read 一致性
- 不依赖 `delta_runtime_native` Python binding（通过 subprocess 调用 inspect binaries）
- 验证 Python 权威与 Rust 影子读在 verdict / sha256 / schema / citation 形状上完全一致
- 任何不一致视为 contract regression

## 不变量（Pre-R2 plumbing 期间保持）

- **Python 仍是所有 R2 域的唯一写权威**；Rust reader 仅做 cross-check，不接 production 路径
- **R1 行为测试基线不漂移**：`pytest tests/` 继续全绿
- **Env var 完全独立**：`DELTA_RUST_AUTHORITY` 改动不影响 `DELTA_RUST_READERS`，反之亦然
- **R2 reader 的所有状态**在 Python 端（filesystem、SQLite、JSON 文件）；Rust 端无状态

## 明确不做

- 不写 R2 域的 Rust **写**路径（PR132+ 才是各域切换）
- 不引入新的 IPC / 协议层（NDJSON stdin/stdout 继续适用 R2 reader inspect 模式）
- 不重命名任何 Python 模块
- 不切换默认权威（Python 永远是 default）
- 不引入 Policy / Approval 的 inspect 形态（它们是评估/决策面）
- 不引入 R2 reader 的 production hook（仅诊断 + 跨语言测试）
- 不在 `is_rust_shadow_reader` 中支持 `policy` / `approval`（它们需要不同的 hook）

## 完成标准

R2 Pre-Plumbing 完成（PR131 + 本 ADR）的判定：

- ✅ `RUST_READ_DOMAINS` 含 4 个 R2 reader 域
- ✅ `is_rust_shadow_reader()` API 可用，与 `is_rust_authority()` 完全独立
- ✅ 4 个 R2 reader 模块就位，含 inline Rust 单元测试
- ✅ 3 个 inspect binary 可调用（`inspect_artifact` / `inspect_checkpoint` / `inspect_citation` + `inspect_validation` 共 4 个）
- ✅ 跨语言测试覆盖 4 个 reader 域
- ✅ R1 行为测试基线不漂移
- ⏳ PR132+ 才是实际 R2 域权威切换（每域一个 PR + per-domain ADR）
