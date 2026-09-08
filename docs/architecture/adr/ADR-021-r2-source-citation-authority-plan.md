# ADR-021 — R2.1 Source / Citation Authority Plan

- 状态：Accepted
- 日期：2026-09-08
- 范围：R2.1 Source / Citation 核心事实关系的 Python → Rust 单向迁移计划
- 决策类型：单领域迁移计划（文档基线；本 ADR 不修改生产逻辑）
- 关联：ADR-006（现有 Source / Citation 产品契约）、ADR-009（Delta Core Architecture）、ADR-018（R2 初始计划）、ADR-019（R2 shadow reader）、ADR-020（Artifact authority）、`docs/governance/rust-core-migration.md` §6 / §7 / §9 / §11

## 决策摘要

Source / Citation 不再按 ADR-018 所述停留在“Rust 只读重放、Python 继续拥有事实”的状态。R2.1 的目标是：

```text
Python capability
  ├─ 读取 PDF / DOCX / XLSX / 文本
  ├─ OCR / connector / message lookup
  └─ 产生绑定到 source revision 的候选 locator / extent
                 │
                 ▼
Rust Core
  ├─ Source identity / revision / fingerprint
  ├─ Citation canonical structure
  ├─ Citation → SourceRevision relationship
  ├─ current-revision / stale 判定
  ├─ locator validity verdict
  └─ validation result persistence and product consequence
```

Rust 必须成为 citation validity 的唯一最终 authority。Python 可以产生候选 Citation 和专业解析结果，但不能独立决定最终 validity，也不能通过吞错把“未知”变成“有效”或“无引用”。

本 ADR 取代 ADR-018 中以下两项旧结论：

1. “Source/Citation 是只读领域，不存在权威切换语义”；
2. PR132 之后继续依次迁移 Approval / Policy / Checkpoint。

新的阶段顺序是 Source/Citation → Validation → Trusted Execution Convergence → Runtime Boundary Audit → Product Reality Gate。Product Reality Gate 通过前暂停 Approval / Policy / Checkpoint 及 R3–R5。

## 当前实现审计

### 当前持久化模型

每个 workspace 使用 `<workspace>/.delta/sources.json`。`core.sources.SourceStore` 把所有事实保存在一个 JSON 对象的 `refs` 数组中：

```text
SourceRef
  id                 random UUID; 当前同时扮演 source identity 和 revision identity
  origin             file | url | connector | db | manual
  location           workspace-relative path / URI / connector coordinate
  fingerprint        sha256(content bytes)
  status             current | changed | missing
  mtime_ns / size    hash 快速路径缓存，不是事实身份
  cited_ranges[]
    run_id
    ranges[]          kind-specific locator dict
```

当前模型没有独立的 `SourceRevision`，也没有一等 `Citation` / `citation_id`。同一路径内容变化会创建新的随机 `SourceRef.id`，旧 ref 标为 `changed`；rename / move 会被当成新的 location 和新的 ref。因而当前 `id` 实际是“路径 + 当时内容”记录的身份，不是稳定 Source identity。

### 当前真实调用图

```text
integrations.tools.files / integrations.tools.documents
        │ 读取文件并在 Python 中构造 CitationRange
        ▼
core.citation.cite
        ├─ SourceStore.capture_file
        │    ├─ Python read_bytes
        │    ├─ Python sha256
        │    ├─ 按 location + fingerprint 复用或新建 SourceRef
        │    └─ 写 sources.json
        └─ SourceStore.add_citation / mark_cited
             ├─ Python to_range_dict / normalize_cited_ranges
             └─ 把 {run_id, ranges} 追加到同一个 SourceRef 并写 sources.json

sources.json
   ├─ Analyzer.source_citation_hits
   │    └─ SourceStore.validate_citation (Python 最终 verdict)
   ├─ SessionManager._count_valid_citations
   │    └─ run_validation(valid_citation_count=...)
   ├─ GET run detail
   │    └─ 输出 sources / citations 供产品检查
   └─ SourceStore.check_freshness / reindex_stale
        └─ Python stat + sha256 更新 current | changed | missing

core/runtime-native/source_citation.rs
   └─ inspect_citation / tests only
        └─ 仅校验 CitationRange JSON shape；不读取 SourceRef、revision 或当前 source
```

Rust 的 `source_citation.rs` 当前不是 production path，也不是 authority。它只对 8 种 range 做结构检查，无法回答 source 是否存在、是否改变、citation 是否绑定原 revision、range 是否越界。

### 当前合法 kind

R2.1 保持现有集合，不新增 kind：

```text
lines | page | cells | row | column | sheet | message_id | custom
```

### 当前 verdict 行为

`SourceStore.validate_citation` 当前返回一个松散 dict：

```text
valid: bool
status: current | changed | missing
reason: valid | content_changed | out_of_bounds | file_missing | source_gone
```

实际语义存在以下限制：

- 只有 `lines` 做越界检查；page / cells / row / column / sheet / message_id / custom 在文件 sha256 不变时直接判 `valid`；
- non-file source 没有 connector revalidation 协议，validation path 仍尝试把 location 当文件读取；
- structure validation 和 source/revision validation 分离在不同调用时刻，最终结果无法表达是哪一层失败；
- verdict 不持久化，重启后由 Python 重新计算；
- `core.citation.cite` 吞掉 `OSError` 和 malformed range 的 `ValueError`，成功的 capability call 可以静默失去 Source / Citation 审计证据；
- `SessionManager._count_valid_citations` 和 run-detail endpoint 调用不存在的 `SourceStore.all()`，再用 broad `except Exception` 吞掉错误。当前代码因此可能把内部错误静默投影为 `0` 个 valid citation 或空 citation 列表；
- `run_validation` 在要求 citation 但 `valid_citation_count is None` 时跳过 citation check，并允许 run 通过。这不满足 fail-closed completion gate；
- validity roll-up 使用 Python 私有 `_VALIDITY_RANK`，产品后果仍由 Python 决定。

这些是基线事实，不在本 ADR 的文档 PR 中修复；它们是实现 PR 的 P0 acceptance cases。

## 当前 scoped Authority Matrix

禁止使用 “hybrid / both / mostly” 描述。每一行列出当前真正能驱动产品行为的唯一 authority；shadow 只能观察。

| Fact / consequence | Authority now | Reader | Writer / evaluator | Gray switch | Production path | Legacy / shadow path | Deletion condition |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Source record identity | Python `SourceStore` | Python server / analyzer | Python `capture_file` | none | `sources.json` | none | Rust store migration + restart/upgrade tests pass |
| Source revision identity | Python `SourceRef.id + fingerprint`（两者未分离） | Python | Python `capture_file` | none | `sources.json` | none | Rust `SourceId` / `SourceRevision` migration complete |
| Source fingerprint | Python | Python; Rust artifact reader is unrelated | Python `hashlib.sha256` | none | `capture_file` / freshness | none | Rust fingerprint verdict is production authority |
| Citation canonical structure | Python | Python server / UI | Python `to_range_dict` | `DELTA_RUST_READERS=source_citation` only enables tests/diagnostics | `mark_cited` | Rust `inspect_citation` shadow | Rust agreement green, production command live, direct Python verdict forbidden |
| Citation → source / revision link | Python implicit nesting | Python | Python `mark_cited` | none | nested under `SourceRef.cited_ranges` | none | explicit Rust-owned relationship persisted and migrated |
| Citation current validity | Python | analyzer / automation | Python `validate_citation` | none | analyzer + completion count | Rust has no equivalent | Rust verdict drives all readers and completion gates |
| Citation visibility in run detail | Python server | desktop / API clients | Python endpoint projection | none | `services.server.app` | none | endpoint reads authoritative Rust result and no broad swallow remains |
| Citation count used by Validation | Python automation manager | Python validation | Python `_count_valid_citations` | none | automation finalization | none | Rust returns persisted validation inputs/results linked to run |
| Source freshness cache | Python | Python | Python check/reindex | none | background / on-demand | none | either removed as non-authoritative cache or updated only from Rust verdict |

## Target domain contract

### Core types

Rust owns the canonical serialized forms of at least these types:

```text
SourceId
  value: stable opaque id for the logical source

SourceRevision
  id: opaque revision id
  source_id: SourceId
  fingerprint: SourceFingerprint
  captured_at
  origin
  location_at_capture
  extent_attestation?       # capability-produced, revision-bound metadata

SourceFingerprint
  algorithm: sha256
  digest: lowercase hex

Citation
  citation_id
  run_id
  source_id
  source_revision_id
  source_fingerprint
  kind
  location: CitationRange
  created_at

CitationKind
  lines | page | cells | row | column | sheet | message_id | custom

CitationRange
  kind-specific canonical locator; unknown fields rejected

CitationValidity
  valid | source_missing | source_changed | range_invalid |
  unsupported_kind | malformed | unknown

CitationValidationResult
  citation_id
  source_id
  source_revision_id
  validity
  source_exists
  source_unchanged
  structure_valid
  revision_matches
  range_valid?              # null only when it cannot be established
  checked_at
  current_fingerprint?
  details                   # structured diagnostic, no raw traceback
```

`CitationValidationResult` 不能只提供 `valid: bool`。外部 API 可为兼容保留 derived boolean，但核心必须保留四个独立事实：source exists、source unchanged、citation structurally valid、citation still references the captured revision。

### Source identity and rename / move

- `SourceId` 表示逻辑来源；`SourceRevision` 表示当时看到的内容版本；两者不得再由一个随机 ref id 同时承担。
- file location 是属性，不是 identity。相同路径的新内容是同一 Source 的新 revision；可证明的 rename / move 保持 `SourceId` 并记录新 location。
- 在无法可靠证明 move（例如同 fingerprint 同时存在多个文件）时，结果必须是 `unknown` 或新 Source，不能猜测关系。
- mtime / size 只允许作为避免重算 hash 的缓存提示，不能单独证明 source unchanged。

### Range validity without moving parsers into Rust

Rust 不解析 PDF / DOCX / XLSX，也不实现 OCR。需要专业解析的 kind 采用 revision-bound capability attestation：

```text
Python capability reads source revision bytes
  → returns candidate locator + extent metadata
  → metadata includes source fingerprint + producer/version
Rust verifies fingerprint/revision binding and canonical locator
  → evaluates locator against attested extent
  → persists the final CitationValidationResult
```

例如：

- `lines`：Rust 可直接按捕获 bytes 计算 line count；
- `page`：PDF capability 返回 page count，绑定 source fingerprint；
- `cells` / `row` / `column` / `sheet`：XLSX capability 返回 sheet / used-range extent，绑定 source fingerprint；
- `message_id`：connector 返回 revision / message existence attestation；
- `custom`：只验证 canonical structure 和 revision binding。若没有该 descriptor 的 authoritative resolver，最终 verdict 必须是 `unknown`，不得自动 `valid`。

专业 capability 的结果是输入证据，不是最终 verdict。

## Authority Before / After

### Domain

`source_citation`：Source identity、SourceRevision identity、fingerprint 绑定、Citation canonicalization、Citation → SourceRevision relation、stale detection fact 和最终 CitationValidationResult。

### Authority Before

Python `SourceStore` 和 `SourceStore.validate_citation`。Rust 仅有测试/诊断用 shape validator。

### Authority After

Rust Core 持有并持久化上述全部事实；Python readers / workers 只提交 source bytes/handles、candidate citation 和专业 extent attestation。所有会驱动 UI、Automation citation count 或 Validation completion gate 的 verdict 都来自 Rust。

### Compatibility

- 现有 `SourceDTO`、run-detail citation shape 和 `cite(...) -> source id | None` 在迁移期保持兼容；新增字段只能 additive；
- 旧 `sources.json` 必须可升级，保留历史 `run_id`、location、fingerprint 和 ranges；升级不得重置用户历史；
- Python baseline 仅用于 shadow agreement，不得在 Rust authority 启用后驱动产品后果；
- 临时开关沿用 per-domain fail-closed selector。实现 PR 将 `source_citation` 从 `RUST_READ_DOMAINS` 提升为 authority domain；声明 Rust authority 而 Core 不可用时不得回退到 Python verdict。

### Exit Condition

删除 Python 独立 verdict path 的条件：

1. 所有 8 个现有 kind 的 canonicalization cross-language agreement 全绿；
2. file changed / deleted / moved / renamed / range overflow / malformed / restart / CJK + 空格路径 contract tests 全绿；
3. 旧 `sources.json` upgrade + rollback fixture 全绿；
4. analyzer、run detail、Automation、Validation 全部读取 Rust result；
5. Product Reality Gate 长文档任务验证 stale → rerun → valid；
6. 一个发布观察期内无 authority disagreement。

目标版本：`v0.4.0` 发布前删除生产 Python final-verdict path。若观察期未完成，则 `v0.4.0` 不得宣称 Source/Citation authority 稳定，也不得进入 Approval / Policy / Checkpoint。

### Failure / Rollback

- shadow 阶段：Python 仍是 primary，Rust disagreement 立即阻断切权并触发阶段暂停条件；
- authority 阶段：Rust Core / protocol / persistence 出错时返回 `unknown` 或 typed `CitationError` 并阻止要求 citations 的 completion gate；不得静默调用 Python 给出第二个 verdict；
- rollback 只能通过显式取消该域的灰度开关回到切权前版本，不能在一次运行内部 fail-open；
- schema migration 前保留原 `sources.json` 的可恢复备份/事务写，测试异常中断后旧数据仍可读取。

## Migration contract

实现 PR 必须按以下顺序完成，不允许长期双主控：

```text
1. 冻结 Python baseline 与旧数据 fixtures
2. 在 Rust 定义 canonical types + typed result
3. 扩展 shadow validation 到 source / revision / range / stale 全语义
4. 对同一输入比较 Python 与 Rust 结果；任一 disagreement 立即停止
5. 把 capture / citation record / validate 接入统一 delta_core 协议
6. Rust authority 开关下只接受 Rust verdict，fail-closed
7. analyzer / run detail / automation / validation completion consumers 改读 Rust result
8. CI 禁止这些 consumer 直接调用 Python validate_citation
9. 删除 broad exception swallow 和无效 SourceStore.all() 调用
10. 达到 Exit Condition 后删除 Python 独立 final-verdict 实现
```

实现应优先复用现有 `delta_core` NDJSON 进程与 `DeltaCoreClient`，不引入第二套 IPC 或 runtime。协议命令保持单领域，例如 `source.capture`、`citation.record`、`citation.validate`；具体 command 名在实现 PR 固化。

## Acceptance criteria

### Semantic contract

至少覆盖：

1. 普通文本 `lines` 引用（含 CRLF / LF、空文件、单行无尾换行）；
2. PDF `page` / page range；
3. XLSX `sheet` / `cells` / `row` / `column`；
4. connector `message_id`；
5. source 内容修改产生新 revision，旧 citation 为 `source_changed`；
6. source 删除为 `source_missing`；
7. rename / move 不依赖原路径或当前 mtime 猜测 identity；
8. start/end 反转、0/负数、EOF/page/sheet/cell 越界为 `range_invalid`；
9. unknown kind 为 `unsupported_kind`，缺字段/错类型为 `malformed`；
10. 应用重启后同一 citation 得到相同 verdict，历史关系仍可查询；
11. Portable Windows 构建下 CJK + 空格 + 长路径；
12. Python/Rust shadow verdict 一致；
13. Core unavailable / malformed protocol response fail-closed；
14. required citation 无法判定时 Validation 不得把 Run 标记 Completed；
15. run detail 可检查 Source、revision、Citation 和 typed validity。

### Production-path proof

测试不能只调用 inspect binary。至少有一个测试从实际 reader/cite hook 进入，经 `DeltaCoreClient`，再由实际 analyzer / run detail / Automation completion consumer 读取结果。

### Restart and migration proof

- 载入 PR132 之前生成的 `sources.json`；
- 原子升级到 Rust schema；
- 在 capture 与 citation record 之间、citation record 与 verdict persistence 之间模拟崩溃；
- 重启后不得产生重复 Citation，不得丢失 SourceRevision，不得把 `unknown` 改成 `valid`。

## Runtime boundary

本领域实现不得把 PDF / Office / OCR / connector SDK 搬进 Rust，不得重写 `core/engine.py`，不得新增 UI layout。`core/engine.py` 只允许删除重复 authority 或接入既有协调边界，且文件体积不得因本迁移净增长。

## Tests and gates

实现 PR 至少运行：

```text
pytest (source/citation + analyzer + automation + run detail + validation contracts)
ruff
pyright
cargo fmt --check
cargo clippy -- -D warnings
cargo test
authority migration guard
legacy/layout guards
portable path tests
```

全仓 gate 在 Product Reality Gate 阶段执行；不得 skip、continue-on-error、删除旧测试或降低 lint / CI 标准。

## 本 ADR 明确不做

- 不修改 Python / Rust 生产代码；
- 不修复 `SourceStore.all()`、broad exception swallow 或 completion gate（留给实现 PR，并已有 P0 acceptance）；
- 不引入新 citation kind；
- 不迁移 PDF / Office / OCR / connector SDK；
- 不推进 Approval / Policy / Checkpoint；
- 不进入 R3 / R4 / R5；
- 不大改 UI 或重写 engine。

## 完成标准

本计划 ADR 完成意味着：当前真实调用图、Authority Before / After、核心数据模型、迁移契约、回滚/删除条件和验收标准已冻结。它不意味着 Source / Citation authority 已完成；只有实现 PR 的 production path、cross-language agreement、restart/portable tests 和后续 Product Reality Gate 全部通过后，才可将该领域标记为稳定。
