# 发布治理

本文规定 Delta 的版本、Tag、构建产物和 Release 要求。

## 发布来源

所有正式 Release 必须基于 `main`。

不得从功能分支、临时分支或未合入 `main` 的 Commit 直接发布正式版本。

发布前应确认目标 Commit 已通过对应质量门禁。

## 版本规则

Delta 使用语义化版本：

```text id="c1o4u8"
MAJOR.MINOR.PATCH
```

Tag 使用：

```text id="1k4sl7"
vMAJOR.MINOR.PATCH
```

例如：

```text id="oe6yqh"
v0.3.0
v1.0.0
v1.0.1
```

## 版本含义

### PATCH

用于：

* 缺陷修复
* 小型兼容性修正
* 不改变能力边界的维护更新

### MINOR

用于：

* 新功能
* 明显能力增强
* 用户可感知但保持兼容的变化

### MAJOR

用于：

* 重大架构变化
* 明显不兼容变更
* 公开接口、配置或数据格式的重大调整

在 `1.0.0` 之前，也应尽量保持版本含义一致，避免把所有变化都归入 PATCH。

## 发布前检查

发布前至少确认：

1. `main` 对应 Commit 的 `ci-required` 已通过
2. 版本号一致
3. Release workflow 配置有效
4. 构建产物可以正常生成
5. Portable 或其他正式 Artifact 可正常启动
6. 不包含开发环境绝对路径
7. 不包含 Secret、Token 或用户私有数据
8. Release Notes 或 `CHANGELOG.md` 已更新
9. 关键兼容性变化已经说明
10. 必要的升级说明已经提供

## 构建来源

正式 Release 应优先由 GitHub Actions 统一构建。

本地构建可以用于开发和验证，但不应成为正式发行物的唯一来源。

这样可以保证：

* 构建环境可重复
* 构建过程可追溯
* Artifact 来源明确
* Release 与仓库 Commit 对应

## Artifact

正式发布的 Artifact 应：

* 名称稳定、可理解
* 与版本对应
* 来源可追溯
* 不混入临时调试文件
* 不包含开发机专属路径
* 不包含未声明的额外运行时数据

如果提供 SHA-256，应确保哈希基于最终发布文件生成。

## Windows 发布

Windows 发行包在正式代码签名启用前，不得宣称具有受信任 Publisher 身份。

代码签名要求见：

```text id="4b7e0n"
docs/operations/windows-code-signing.md
```

签名启用后，应先完成可执行文件签名和验证，再生成最终 ZIP 和 SHA-256。

## Release Notes

Release Notes 应说明用户真正需要知道的变化，优先包括：

* 新功能
* 重要修复
* 兼容性变化
* 配置变化
* 已知问题
* 升级注意事项

不需要逐条复制内部 Commit。

## Tag

正式 Tag 必须与 Release 版本一致。

已经发布的正式 Tag 原则上不得：

* 删除
* 强制移动
* 静默重建
* 指向不同 Commit

如果已发布版本存在问题，应优先发布新的修订版本，而不是替换旧版本。

## Release Artifact 修改

已经公开发布的 Artifact 原则上不得静默替换。

如果必须修正发行内容，应：

1. 明确说明原因
2. 使用新的版本号
3. 重新生成 Artifact
4. 重新生成校验值
5. 在 Release Notes 中说明修正内容

## 发布失败

Release workflow 失败时，应区分：

* 代码或测试失败
* 构建失败
* Packaging 失败
* 签名失败
* 权限失败
* GitHub Actions 环境问题
* 外部依赖问题

不得通过跳过关键验证来完成发布。

## 发布完成

发布完成后，应确认：

* Release 页面正常
* Tag 指向正确 Commit
* Artifact 完整
* SHA-256 与文件一致
* 安装包或 Portable 可以正常启动
* 没有误上传调试产物或敏感文件

发现问题后应立即停止继续传播错误 Artifact，并按新的修订版本处理。