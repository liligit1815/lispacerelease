# 更新协议第 1 版

每个软件使用固定 HTTPS 地址 `https://raw.githubusercontent.com/liligit1815/lispacerelease/main/updates/<appId>.json`。软件必须校验 `schemaVersion`、`appId` 和 `packageName`。首次发布之前 `release` 为 `null`，客户端显示暂无可更新版本。

```json
{
  "schemaVersion": 1,
  "appId": "redmagic-duo",
  "packageName": "ls.augment.com",
  "release": {
    "versionCode": 20518,
    "versionName": "2.0.0-alpha1-test20518",
    "minSdk": 35,
    "publishedAt": "2026-10-11T00:00:00Z",
    "notes": "新增软件内检查更新、下载及系统安装入口。",
    "apk": {
      "url": "https://github.com/liligit1815/lispacerelease/releases/download/redmagic-duo-v20518/redmagic-duo-20518.apk",
      "size": 123456,
      "sha256": "实际 APK 的 64 位十六进制 SHA-256",
      "certificateSha256": "既有签名证书的 64 位十六进制 SHA-256"
    }
  }
}
```

示例中的时间、大小和哈希为格式说明，正式数据由发布工具从签名 APK 和 GitHub Release 生成。

## 检查与下载

- 只有远端 `versionCode` 大于本地时提示升级，`versionName` 用于展示。
- `minSdk` 大于当前 Android API 时说明不兼容，不下载安装。
- 自动检查按软件单独持久化节流，每 24 小时至多一次；手动检查可以立即执行。
- 清单最大 64 KiB，更新说明最大 12000 个字符，显示版本名最大 100 个字符，APK 最大 512 MiB；网络有连接和读取超时。
- 初始下载地址必须是本仓库的 HTTPS Release APK 附件。只允许 HTTPS 重定向到 GitHub 或 GitHub 官方附件域名，不携带发布者凭据。
- 下载 APK 必须匹配大小、SHA-256、包名、清单版本、最低 Android API、既有应用签名。校验失败不能打开系统安装器。
- 安装 APK 使用仅开放更新文件的非导出内容提供者，临时授予安装器读取权限。未知来源授权、用户取消、失败以及页面销毁均应有对应处理。

清单只指向公开且已上传完成的 APK。Release 标签和 APK 不覆盖；更新清单是唯一随推送版本变化的入口。先发布附件、后更新清单，使中途失败时用户仍能下载上一个选定版本。
