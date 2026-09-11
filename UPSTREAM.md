# 外部项目与历史归属

Delta 是独立项目，不维护任何单一上游、镜像分支或自动同步流程。

## 当前原则

- 外部项目仅作为第三方参考，不自动影响 Delta 的产品、架构或发布节奏。
- 外部实现进入 `main` 前必须按照 Delta 当前产品边界、Runtime Authority、许可证、测试和 Pull Request 流程独立评估。
- 不为跟随外部项目恢复已经淘汰的目录、协议、产品形态或 Compatibility Layer。
- 历史来源、迁移记录和当时的设计决策保留在 Git 历史、CHANGELOG 与历史 ADR 中，不作为当前产品身份的一部分。
- 第三方版权和许可证义务以 `LICENSE` 及实际保留的第三方代码为准。

## 参考项目

Delta 可以研究任何公开项目在 Runtime、UI、安全、日常办公、数据分析、内容创作和 Agent 交互等方面的优秀做法。

参考的对象不构成“上游”。吸收原则是：

```text
研究语义
  ↓
评估是否符合 Delta 产品边界
  ↓
明确服务日常办公 / 数据分析 / 内容创作中的哪个工作环节
  ↓
定义 Delta 自己的 Contract / Authority
  ↓
独立实现或合规引入
  ↓
测试与验证
```

任何参考都必须服从 Delta 自身的长期定位：Delta 是一个本地优先的个人工作 AI Agent，一级产品方向只有 **日常办公、数据分析、内容创作**。

研究、文档处理、PDF / Office、Search、Citation、Validation、Scripting、Automation、Connector、MCP 等只能作为上述三类工作的具体环节或支撑能力，不因外部项目的产品结构而提升为 Delta 的新产品方向。

模型协议仅维护 OpenAI-compatible 与 Anthropic-compatible 两类；脚本是执行能力，不扩张为 Coding Agent 产品线；Provider 聚合、fallback 和复杂模型路由继续外置。
