# Provenance & Third-Party Governance

Delta 是独立项目，不维护任何单一上游、镜像分支或自动同步关系。

本文只负责记录**来源、第三方代码吸收和许可证治理**，不重复产品边界、目标架构或 Runtime 设计。

## 原则

- 外部项目只作为参考，不自动影响 Delta 的产品、架构或发布节奏；
- 外部代码、行为语义或架构方案进入 `main` 前必须独立评估；
- 不为跟随外部项目恢复已经淘汰的目录、协议、Coding 产品形态、兼容层或多 Agent 结构；
- 历史来源和迁移事实保留在 Git、CHANGELOG、ADR 和 PR 中；
- 第三方版权与许可证义务以实际保留代码和对应许可证为准。

## 评估过滤器

任何外部实现进入 Delta 前必须回答：

1. 是否改善日常办公、研究分析或内容创作中的真实工作环节？
2. 属于 Experience / Runtime / Trust / Work / Capability / Automation / Learning 中哪个职责？
3. 谁拥有状态和 Authority？
4. 能否优先作为 Capability / Skill，而不是扩张 Runtime / Agent？
5. 是否符合 Rust + TypeScript 核心边界与受控 Worker 模型？
6. 是否引入新的安全、许可证或供应链风险？
7. 是否有测试、Validation 和可回滚路径？

## 外部参考不等于上游

Delta 可以借鉴 Codex、WorkBuddy、MiMo、OpenWorker、Maka 或其他公开项目的 Runtime、UI、Trust、Office、Research、Content、Skill、Memory、Automation 等优秀做法，但参考对象不构成产品身份或同步关系。

吸收的是：

```text
问题语义
→ 可验证的行为
→ Delta 自己的 Contract
→ 独立实现或合规引入
```

不是目录、品牌和历史包袱的复制。

## 命名与来源

外部项目原有模块名不自动进入 Delta 命名体系。

Delta 当前命名原则：

> **独立边界用 `delta-*`，内部职责用语义名。**

因此即使外部项目采用大量品牌化 crate / package，Delta 也只在真实形成独立 executable、API、protocol、SDK、版本或发布边界时使用 `delta-*`。

## 许可证

引入第三方代码时必须确认：

- 原始许可证；
- Copyright notice；
- 是否要求保留 NOTICE / attribution；
- 是否允许修改与再分发；
- 是否与 Delta 当前 LICENSE 兼容；
- 是否存在生成代码、模型权重、媒体资产等额外授权要求。

不确定时不合并。

## 历史归属

早期 OpenWorker 等历史关系只属于项目演进史，不是当前产品身份。需要追溯时使用 Git history、ADR、CHANGELOG 和对应 PR，而不是在当前 README / architecture 文档中维持旧身份。