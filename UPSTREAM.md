# 上游管理

Delta 基于 OpenWorker 演进，但作为独立项目维护自身的产品定位、架构和发布节奏。

## 当前状态

**不再自动同步 OpenWorker 上游。**

原有的 `upstream-sync.yml` 已删除。`upstream-openworker` 镜像分支不再维护。

## 原则

Delta 现在是一个完全独立的项目。OpenWorker 的历史贡献通过 MIT 许可证保留，但：

- **不再有自动同步机制**：不再通过 GitHub Actions 自动镜像 `andrewyng/openworker:main`
- **不再有上游吸收流程**：任何外部代码进入 `main` 前必须经过 Delta 自身的完整评估和 PR 流程
- **OpenWorker 仅为参考**：OpenWorker 现在仅作为第三方参考项目，其变更不自动影响 Delta

## 历史说明

Delta 起源于 OpenWorker 项目（MIT 许可证）。感谢 OpenWorker 的所有贡献者。Delta 保留对 OpenWorker 项目及其贡献者的来源说明，并遵守相应开源许可证要求。

## 未来联邦化边界

Federation 是一条开放、供应商无关的可选能力边界。OpenWorker、第三方、自建等
都是潜在的 Federation 适配对象之一。

- OpenWorker 仅为若干潜在 Federation 适配对象之一，**绝非** Delta 的核心
- Delta Core 不依赖、不感知任何特定 Federation 适配器
- `integrations/managed/` 中定义 Capability Port 协议；具体适配器若实现，
  将位于 `integrations/managed/adapters/<provider>.py`

详见 `docs/architecture/hub-federation-boundary.md`。