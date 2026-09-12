# ADR-050: Product & Runtime Convergence

## Status

Accepted — target architecture for post-R5.1 convergence.

## Context

R1–R5 moved trusted state, execution decisions and provider authority toward Rust. R5.1 closes runtime-correctness findings and adds first-class human control for active runs.

At the same time, Delta's product scope has converged. The previous broad "knowledge work" framing and the earlier three labels "日常办公 / 数据分析 / 内容创作" still leave two ambiguities:

1. "数据分析" is too narrow once experiment design, sequential experiment design, model updating and research reporting are first-class capabilities.
2. The current repository still contains a persistent Python application/control layer, while the long-term authority design already assumes Rust owns facts and consequences.

A stable post-R5.1 target is needed before further product expansion.

## Decision

### D1: Product scope has exactly three first-level domains

The canonical product names are:

```text
日常办公
研究分析
内容创作
```

No fourth first-level domain is added without a new architecture/product decision.

### D2: 研究分析 is an end-to-end research loop

"研究分析" includes:

- research question / hypothesis / objectives;
- data preparation and exploratory analysis;
- statistical inference and modeling;
- experiment design (DOE);
- **sequential experiment design from the initial capability model**;
- model updating, uncertainty and next-experiment recommendation;
- stopping / confirmation logic under explicit human control;
- visualization, interpretation and formal research reporting.

It is not limited to analysing already-existing data.

### D3: 内容创作 includes text, graphics, images and video

The content domain includes:

- ideation and research;
- writing and editing;
- graphic-note / card / infographic production;
- image generation and processing;
- short-video scripting, storyboard, subtitles, generation / light processing and publishing assets;
- multi-platform adaptation;
- publishing preparation and content analytics.

Delta may orchestrate external media models/tools; the core Runtime does not need to become a professional image/video engine.

### D4: One Delta, not a growing fleet of product Agents

The default product shape is one primary Delta work Agent backed by Capabilities and Skills.

Office, Research Analysis and Content Creation are product domains, not three separate runtimes. Specialized workflows should normally become Skills rather than new permanent Agent families.

### D5: Seven long-lived logical modules

The target system is divided by stable responsibility, not by historic directory layers:

1. **Experience** — user-facing interaction and presentation.
2. **Runtime** — session/run/turn, model/tool loop, context and live control.
3. **Trust** — policy, approval, risk, ledger, idempotency, secrets and execution limits.
4. **Work** — workspace, source, citation, artifact, version, validation and provenance.
5. **Capability** — tools, skills, workers, office/research/media execution surfaces.
6. **Automation** — scheduler, wake, inbox, notifications and inbound/background triggers.
7. **Learning** — memory, preference, experience, failure memory and skill evaluation/evolution.

These are logical responsibility boundaries. This ADR does not require immediate creation of seven crates or seven processes.

### D6: Core product languages converge to Rust + TypeScript

Target core implementation languages:

```text
Rust + TypeScript
```

- TypeScript owns the product UI / Experience layer.
- Rust owns Runtime, trusted state, policy, automation authority, work facts, learning authority and the Capability host.

Python, PowerShell and Shell remain supported as **task execution languages**, not core architecture authorities.

A Python file may remain or be added for analysis/office/media work without violating this decision, provided it is a controlled Worker/Script and does not own product state or control flow.

### D7: Python control plane is transitional

The persistent Python server, Python SessionManager/TurnEngine authority and TS → Python → Rust forwarding topology are transitional post-R5.1 implementation details.

R6 should migrate control ownership and delete duplicate implementation instead of mechanically translating every Python line to Rust.

### D8: Skill is the primary expansion unit

A Skill may combine:

- metadata and version;
- instructions / workflow;
- required capabilities;
- permission declarations;
- validation;
- templates;
- optional scripts.

Learning may propose or evaluate Skill Candidates using real run evidence.

### D9: Learning can improve method, never self-elevate authority

Delta may learn and improve:

- preferences;
- workflow choices;
- prompts/instructions;
- templates;
- tool-selection strategies;
- script/report patterns;
- validated failure-avoidance knowledge;
- Skill versions.

Delta may not autonomously relax or rewrite:

- policy / approval;
- risk levels;
- sandbox / network boundaries;
- secrets access;
- ledger / validation authority;
- runtime authority.

Principle:

> **Capabilities may evolve; permissions may not self-evolve.**

### D10: Modular monolith by default

Core modules should coexist in one local Rust application/runtime unless an actual isolation, crash-containment or ecosystem requirement justifies another process.

Do not create local microservices merely to mirror module boundaries.

## Consequences

### Positive

- Product growth is focused on three real work domains instead of Agent/tool count.
- Research Analysis gains a clear differentiator through DOE and sequential research loops.
- Content Creation explicitly includes graphic, image and video deliverables.
- Rust/TypeScript language ownership becomes unambiguous.
- Python ecosystem advantages remain available without retaining Python as system authority.
- Skills and Learning can grow rapidly without destabilizing Trust or Runtime.
- Architecture discussions gain a stable module vocabulary independent of temporary directories.

### Costs

- Post-R5.1 convergence requires a real Runtime Host migration, not a cosmetic facade cleanup.
- Existing Python server/engine/tool abstractions must be audited into REMOVE / MOVE TO RUST / KEEP AS WORKER.
- Desktop IPC and packaging will change as the Python sidecar is retired.
- Some current Coding-specific surfaces and dependencies will be removed even if technically functional.

## Implementation direction

The follow-up convergence stage (R6) should proceed by subtraction and authority consolidation:

```text
Product-scope cleanup
        ↓
Rust Runtime Host
        ↓
TypeScript → Rust direct IPC
        ↓
Application control-plane migration
        ↓
Agent-loop migration
        ↓
Python capability workerization
        ↓
Persistent Python backend removal
        ↓
Rust + TypeScript hard-cut
```

Product capability expansion should not be coupled to the Runtime migration beyond what is necessary to preserve or validate the three product domains.

## References

- `docs/DELTA_BLUEPRINT.md`
- `docs/architecture/target-architecture.md`
- `docs/architecture/capability-abi.md`
- `docs/governance/rust-core-migration.md`
- ADR-009 — Delta Core Architecture
- ADR-049 — R5.1 Runtime & Human Control Convergence
