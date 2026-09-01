# Windows 代码签名

本文说明 Delta Windows 正式发行物的代码签名要求。

## 当前状态

当前状态：

```text
Not configured
```

在正式代码签名启用前，Delta 不应宣称具有受信任的 Windows Publisher 身份。

## 作用

SHA-256 用于验证文件内容是否发生变化。

Authenticode 代码签名用于证明 Windows 可执行文件的发布者身份。

两者作用不同，不能互相替代。

## 证书要求

公开发行时应使用合法代码签名证书。

不得使用 self-signed certificate 作为正式发行签名。

证书应由实际发布主体持有，并符合 Windows 代码签名要求。

## GitHub Secrets

启用签名后，建议使用：

```text
WINDOWS_SIGNING_CERT
WINDOWS_SIGNING_PASSWORD
```

其中：

`WINDOWS_SIGNING_CERT`

保存 Base64 编码后的 PFX 证书。

`WINDOWS_SIGNING_PASSWORD`

保存对应 PFX 密码。

不得把真实证书或密码提交到仓库。

## 签名位置

签名应发生在可执行文件构建完成之后、最终压缩包生成之前。

推荐顺序：

```text
Build executable
        ↓
Authenticode sign
        ↓
Verify signature
        ↓
Create final ZIP
        ↓
Generate SHA-256
        ↓
Publish Release
```

不得在最终 ZIP 和 SHA-256 生成后再修改内部可执行文件。

## 临时证书文件

CI 中解码得到的 PFX 只允许写入临时目录，例如：

```text
$RUNNER_TEMP
```

签名完成后应立即删除。

不得：

* 写入 repository workspace
* 上传为 Artifact
* 缓存证书文件
* 输出证书内容
* 输出密码

## 签名算法

应使用 SHA-256。

签名时应使用可信 Timestamp 服务，避免证书到期后已签名文件失去有效时间依据。

## 验证

签名完成后必须进行验证。

例如：

```text
signtool verify /pa /all
```

验证失败时不得继续生成正式 Release Artifact。

## 多个可执行文件

如果 Windows 发行包包含多个需要信任的可执行文件，应分别完成签名和验证。

例如：

* 主程序
* Launcher
* Sidecar
* 辅助可执行文件

不能只签名外层 ZIP。

## Release Artifact

最终 ZIP 必须基于已经完成签名和验证的文件生成。

SHA-256 必须针对最终实际发布的 ZIP 生成。

这样可以保证：

* 可执行文件身份可验证
* 压缩包内容完整性可验证
* Release Artifact 与校验值一致

## 未配置签名时

如果正式证书尚未配置：

* 可以继续发布未签名版本
* 可以继续提供 SHA-256
* 不执行伪签名
* 不生成自签名证书冒充正式发行
* 不声明 Publisher verified
* 文档继续明确标记 `Not configured`

## 启用签名时

正式启用前应同时确认：

1. 证书来源合法
2. GitHub Secrets 已配置
3. Release workflow 不输出敏感信息
4. PFX 只在临时目录中存在
5. 所有目标 executable 均完成签名
6. `signtool verify` 通过
7. ZIP 在签名完成后生成
8. SHA-256 基于最终 ZIP 生成

签名逻辑应集中在 Release workflow 中维护，避免开发机和 CI 使用不同的正式发行流程。