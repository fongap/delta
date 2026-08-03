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
```

First run initializes the local workspace, SQLite database, and key storage.

---

## 中文

**Delta 是本地运行的个人 AI 工作系统**——任务、资料、密钥、审批和交付物完全保存在本机。支持暂停/恢复、完整审计、来源追溯和受控学习。不做云同步、云端记忆或托管式自动化。

### 核心原则

- **本地优先**：所有数据离线存储
- **可证据化**：结论追溯到本地来源片段
- **人掌握后果**：高风险动作需批准
- **可复盘恢复**：任意检查点暂停、恢复、重放
- **可改进不失控**：经验和技能需评估才可启用
- **模型中立**：用户自配模型与密钥

### 系统分层

| 层级 | 职责 |
|------|------|
| **Workspace** | 工作区、任务、交付物入口 |
| **Orchestration** | 计划、执行、检查点、审批编排 |
| **Learning** | 经验、失败记忆、技能受控更新 |
| **Source** | 本地资料导入、索引、引用 |
| **Context** | 分层上下文构建、压缩、恢复 |
| **Execution** | 模型调用、工具调用、结果校验 |
| **Storage** | SQLite、密钥存储、版本归档 |

### 任务闭环

目标 → 计划与 Plan Critic → 执行 → 硬性校验/审批 → 检查点与交付物 → 复盘 → 经验更新

- Plan Critic 独立检查计划的遗漏与风险
- 高风险动作待审批方可执行
- 失败记录可复现的错误证据，不仅文字结论
- **M0 验收**：完成一次本地、可审计、可验证的办公交付

### 特色能力

#### Source Layer（资料接地）

支持本地文档、PDF、表格、图片 OCR、音频转录。每个交付物保留可点击的本地引用，定位到文件/页码/段落。自动生成 source guide 识别资料缺口。

#### Learning Layer（持续推进）

灵感来自 Hermes。任务可从检查点恢复。经验、技能、偏好需评估后才可启用，用户可撤销、删除。

#### Context 管理（Claude 原则）

分离当前工作、任务摘要、来源、长期经验、归档等上下文层。压缩时保留来源证据、审批结论和风险。

### 工程路线图

| 阶段 | 内容 |
|------|------|
| **M0** | Windows 本地运行；完整审计链 |
| **M1** | 移除云登录/OAuth；本地密钥可配；断网可用 |
| **M2a** | artifacts、approvals、硬性校验、Plan Critic |
| **M2b** | Source Layer、引用、分层上下文、学习闭环 |

基于 OpenWorker，保留上游 Git 历史。每项改造独立提交，持续回补上游修复。

### 快速开始

```bash
git clone https://github.com/fongap/delta.git
cd delta
git remote add upstream https://github.com/andrewyng/openworker.git
npm install && npm run dev
```

首次运行初始化本地工作区、SQLite 和密钥存储。

---
