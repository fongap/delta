# 外部项目与历史归属

Delta 是独立项目，不维护任何单一上游、镜像分支或自动同步流程。

## 当前原则

- 外部项目仅作为第三方参考，不自动影响 Delta 的产品、架构或发布节奏。
- 外部实现进入 `main` 前必须按照 Delta 当前产品边界、Target Architecture、Runtime Authority、许可证、测试和 Pull Request 流程独立评估。
- 不为跟随外部项目恢复已经淘汰的目录、协议、产品形态或 Compatibility Layer。
- 历史来源、迁移记录和当时的设计决策保留在 Git 历史、CHANGELOG 与历史 ADR 中，不作为当前产品身份的一部分。
- 第三方版权和许可证义务以 `LICENSE` 及实际保留的第三方代码为准。

## 当前产品边界

Delta 的一级产品方向只有：

```text
日常办公
研究分析
内容创作
```

其中：

- 研究分析包含数据分析、统计推断、DOE、序贯试验设计、模型更新和正式研究报告；
- 内容创作包含文本、图文、图片、短视频、多平台适配、发布准备和内容复盘；
- Coding、Provider 聚合、单独 Research 产品、PDF / Office、Automation、Connector、MCP 等不提升为新的一级产品方向。

## 参考项目

Delta 可以研究任何公开项目在以下方面的优秀做法：

- Runtime / Agent loop / live steering；
- UI / Artifact / editing experience；
- Trust / Policy / sandbox / recovery；
- 日常办公；
- 研究分析与统计研究；
- 图文 / 图片 / 视频内容生产；
- Skill / Memory / Learning；
- Automation / Connector / MCP。

参考对象不构成“上游”。吸收原则：

```text
研究真实语义
  ↓
是否改善日常办公 / 研究分析 / 内容创作？
  ↓
属于 Experience / Runtime / Trust / Work /
Capability / Automation / Learning 中哪个职责？
  ↓
谁拥有状态与 Authority？
  ↓
能否优先作为 Capability / Skill 落地？
  ↓
定义 Delta 自己的 Contract
  ↓
独立实现或合规引入
  ↓
测试 / Validation / 安全审查
```

不得因为参考产品采用“专家团队”“多 Agent”“全场景”“模型市场”或重型媒体编辑器，Delta 就复制同样的产品结构。

## 长期架构过滤器

外部方案进入 Delta 前必须服从：

- 核心产品语言目标为 Rust + TypeScript；
- Python / PowerShell / Shell 只作为受控 Worker / Script；
- 一个领域一个 Authority；
- Skill 是主要能力扩展单位；
- Learning 可以改进方法，不能自行扩大权限；
- 模型协议只维护 OpenAI-compatible 与 Anthropic-compatible；
- Provider 聚合、多 Key、fallback、权重和复杂路由继续外置；
- 没有真实收益，不新增进程、微服务、协议或长期 Agent family。

目标架构见 `docs/architecture/target-architecture.md`，产品蓝图见 `docs/DELTA_BLUEPRINT.md`。