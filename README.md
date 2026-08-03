[English](#english) | [中文](#中文)

---

## English

Delta is a personal AI work system running locally — tasks, resources, keys, approvals, and deliverables are completely stored locally. Supports pause/resume, complete audit, source traceability, and controlled learning. No cloud sync, cloud memory, or managed automation.

### Core Principles

- **Local First**: All data stored offline
- **Evidence-based**: Conclusions traceable to local source fragments
- **Human in Control**: High-risk actions require approval
- **Checkpointable & Recoverable**: Pause, resume, and replay at any checkpoint
- **Improvable Without Loss of Control**: Experience and skills require evaluation before enablement
- **Model Agnostic**: Users configure their own models and keys

### System Architecture

| Layer | Responsibility |
|-------|-----------------|
| **Workspace** | Workspace, tasks, deliverables entry point |
| **Orchestration** | Planning, execution, checkpoint, approval orchestration |
| **Learning** | Experience, failure memory, controlled skill updates |
| **Source** | Local resource import, indexing, referencing |
| **Context** | Hierarchical context construction, compression, recovery |
| **Execution** | Model invocation, tool invocation, result validation |
| **Storage** | SQLite, key storage, version archive |

### Task Closure Loop

Goal → Planning & Plan Critic → Execution → Hard Validation/Approval → Checkpoint & Deliverables → Review → Experience Update

- **Plan Critic** independently checks for plan gaps and risks
- High-risk actions require approval before execution
- Failure logs record reproducible error evidence, not just text conclusions
- **M0 Acceptance**: One complete local, auditable, verifiable office delivery

### Key Features

#### Source Layer (Grounding Resources)

Supports local documents, PDFs, spreadsheets, image OCR, and audio transcription. Each deliverable retains clickable local references with precise locations (file/page/paragraph). Automatically generates a source guide to identify resource gaps.

#### Learning Layer (Continuous Advancement)

Inspired by Hermes. Tasks can resume from checkpoints. Experience, skills, and preferences require evaluation before enablement. Users can revoke or delete entries.

#### Context Management (Claude Principles)

Separates current work, task summaries, sources, long-term experience, and archives. Compression preserves source evidence, approval conclusions, and risks.

### Engineering Roadmap

| Milestone | Content |
|-----------|---------|
| **M0** | Windows local execution; complete audit chain |
| **M1** | Remove cloud login/OAuth; configurable local keys; offline capable |
| **M2a** | Artifacts, approvals, hard validation, Plan Critic |
| **M2b** | Source Layer, referencing, hierarchical context, learning loop |

Based on OpenWorker, preserving upstream Git history. Each refactoring submitted independently with continuous backport of upstream fixes.

### Quick Start

```bash
git clone https://github.com/fongap/delta.git
cd delta
git remote add upstream https://github.com/andrewyng/openworker.git
npm install && npm run dev
