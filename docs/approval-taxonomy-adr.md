# ADR: Approval Taxonomy L0–L4 and Fail-Closed Policy (ARCH-002)

- Status: Proposed
- Date: 2026-08-25
- Scope: permission engine, approval flow, execution gating

## Context

Delta is a general-purpose office agent: one product serving any user's everyday work,
not a vertical compliance tool. Approvals today are binary and interactive:
`PermissionEngine` (coworker/permissions.py) classifies by Mode (chat/read/write-ish),
workspace roots, and a command whitelist; `ApprovalOutcome{ONCE, ALWAYS_TOOL,
ALWAYS_COMMAND, DENY}` resolves an inline prompt. There is no risk taxonomy, no notion
of reversible vs irreversible, and no explicit fail-closed rule for missing policy.

Three requirements make the current shape insufficient:

1. Automation/unattended runs must resolve approvals without a human present.
2. Security policy must be deterministic and live outside the agent loop — never in
   prompts, never influenced by model output claiming "the user approved".
3. Users must be told the truth about consequences: "undo" is not the same as
   "compensate", and some actions are simply irreversible.

## Decision

### 1. Five risk levels (L0–L4)

| Level | Definition | Examples | Default policy |
|---|---|---|---|
| L0 | read-only, no side effects | read file, search, list, summarize | auto-allow inside trusted scope |
| L1 | reversible local writes | write/edit files under scratch & writable roots | auto-allow (audited) |
| L2 | consequential local writes, outside sandbox guarantees | installs, bulk deletes, config changes | ask once / standing rule |
| L3 | external effects, compensatable | send chat message, create calendar event, open PR, upload to shared drive | explicit approval per run or standing grant |
| L4 | irreversible / sensitive | send email, delete cloud files/data, share docs externally, payments, protected paths | explicit per-action approval, no standing grants |

Classification is **deterministic**: tool metadata + target resource + policy tables.
The model can request; it can never classify itself upward past policy, and approval
results only enter via `DeltaClient → ApprovalService`, never from model-visible text.

### 2. Reversibility is declared, not implied

Every effectful action carries one of:

```text
reversible      → undo exists locally (file edit under checkpoint)
compensatable   → a compensation handler exists (create event → delete event)
irreversible    → no honest undo (email sent) — UI says so
```

Compensation handlers are registered by tools at execution time and recorded as
`{action_id, idempotency_key, receipt, compensation}` so Checkpoint/Resume can act on
them. Absent a handler, L3+ defaults to `irreversible`.

### 3. Fail closed

- Missing/unknown classification → treat as L4 (ask).
- Approval service unavailable during automation → deny, record `unavailable`.
- Every ask and decision (including denials and timeouts) is audited.

### 4. Standing rules are scoped

Standing grants (`ALWAYS_TOOL`/`ALWAYS_COMMAND`) survive but are bounded: per tool,
per resource scope, per level ≤ L2. L3 may have standing grants only when explicitly
created by the user in Settings; L4 never.

## Consequences

- `permissions.py` gains a level classifier ahead of the existing root/whitelist checks.
- Automation resolution path uses the same ApprovalService as interactive runs.
- The Execution Gateway (future ADR) consumes levels as its primary gate input.
