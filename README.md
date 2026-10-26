# LiSpace 软件发布与在线更新

红魔Duo、红魔12、红魔11和红魔10堆叠桌面、iQOO肩键扩展的APK发布中心。各软件通过独立更新清单检查新版；各系列桌面分别提供对应安装包，源码分别维护在独立分支。

## 下载

请进入 [Releases](https://github.com/liligit1815/lispacerelease/releases) 选择软件并下载 `.apk` 附件。GitHub 自动生成的 Source code ZIP/TAR 是本更新仓库的文件，不能用于安装软件。

| 软件 | 软件标识 | 更新清单 |
| --- | --- | --- |
| 红魔 Duo | `redmagic-duo` | [redmagic-duo.json](https://raw.githubusercontent.com/liligit1815/lispacerelease/main/updates/redmagic-duo.json) |
| 堆叠桌面11 | `stacked-desktop` | [stacked-desktop.json](https://raw.githubusercontent.com/liligit1815/lispacerelease/main/updates/stacked-desktop.json) |
| 堆叠桌面10 | `stacked-desktop-redmagic10` | [stacked-desktop-redmagic10.json](https://raw.githubusercontent.com/liligit1815/lispacerelease/main/updates/stacked-desktop-redmagic10.json) |
| 堆叠桌面12 | `stacked-desktop-redmagic12` | [stacked-desktop-redmagic12.json](https://raw.githubusercontent.com/liligit1815/lispacerelease/main/updates/stacked-desktop-redmagic12.json) |
| iQOO 肩键扩展 | `iqoo-shoulder` | [iqoo-shoulder.json](https://raw.githubusercontent.com/liligit1815/lispacerelease/main/updates/iqoo-shoulder.json) |

## 在软件内更新

打开软件后每天自动检查一次；也可以点击“检查更新”。发现新版时显示版本和更新说明，用户确认后下载 APK，校验通过再打开 Android 系统安装界面。

更新下载需要能访问 GitHub。自动检查失败时保持正常使用；手动检查或下载失败时说明原因并支持重试。Android 可能要求用户允许来自该软件的安装，并在系统安装界面再次确认。

堆叠桌面的覆盖更新适用于已正确安装同签名修改版桌面的设备。首次替换原厂桌面请遵循该软件的安装说明。

## 发布新版

发布者准备已签名 APK 和 UTF-8 更新说明，运行 [发布工具](tools/publish.py)。工具读取 APK 的实际包名、版本、最低 Android 版本及签名证书，计算 SHA-256，先上传并公开 Release 附件，再更新该软件的清单。

需要 Python 3.11 或更新版本、已有 Android SDK build-tools、JDK，以及 GitHub 写入权限。认证可使用 `GH_TOKEN` / `GITHUB_TOKEN`、已登录的 GitHub CLI 或 Git Credential Manager。令牌应只授予本仓库 Contents 写权限；令牌和签名私钥不存放在仓库中。

```powershell
python tools/publish.py --app redmagic-duo --apk "新版.apk" --notes-file "更新说明.md" --sdk "Android SDK路径" --java-home "JDK路径" --dry-run
```

`--dry-run` 检查 APK 并显示发布计划，检查通过后去掉该参数进行发布。其他软件将 `--app` 改为对应软件标识。红魔12使用 `stacked-desktop-redmagic12`，附件名为 `12-<内部版本>.apk`，发行配置核对target SDK37。

发布直接更新 GitHub 上的清单，本地发布记录保存到忽略的 `out/published/`。需要同步本地仓库清单时运行 `git pull --ff-only`；先同步再进行下一次离线预检查。

每个软件只有一个更新通道。运行发布工具就表示选择该版本推送给用户；版本名包含 alpha/test 的版本同样可以被选择。工具拒绝降低 `versionCode`、以同版本号换包、改变既有签名或上传其他软件的 APK。网络中断时清单保持旧版本，可以用相同命令重试。

每个软件、版本分别生成标签，例如 `redmagic-duo-v20518`。客户端读取独立清单，避免仓库级“Latest”混淆多个软件。

### 三个系列桌面同批次发布

[本批次发行](https://github.com/liligit1815/lispacerelease/releases/tag/stacked-desktop-redmagic10-batch-11-260030-10-250002-12-270007)

10.x.x系统请安装[堆叠桌面10-250002.APK](https://github.com/liligit1815/lispacerelease/releases/download/stacked-desktop-redmagic10-batch-11-260030-10-250002-12-270007/stacked-desktop-redmagic10-250002.apk)

11.x.x系统请安装[堆叠桌面11-260030](https://github.com/liligit1815/lispacerelease/releases/download/stacked-desktop-redmagic10-batch-11-260030-10-250002-12-270007/11-260030.apk)

12.x.x系统请安装[堆叠桌面12-270007](https://github.com/liligit1815/lispacerelease/releases/download/stacked-desktop-redmagic10-batch-11-260030-10-250002-12-270007/12-270007.apk)

更新内容：

1. 新增长按动作震动反馈
2. 新增边界动画回弹效果
3. 优化动画效果和流畅性

使用 `tools/publish-launcher-batch.py` 核验三个系列的最终签名APK，并通过 `--notes-file` 指定本次更新内容与下载说明。每个系列只发布一个附件。红魔10显示为 `堆叠桌面10-<内部号>.APK`，实际文件名保留 `stacked-desktop-redmagic10-<内部号>.apk`，供手动下载和已有客户端在线更新共同使用；11、12系列实际文件名分别为 `11-<内部号>.apk`、`12-<内部号>.apk`。三个独立清单分别绑定本系列附件、版本、大小、SHA-256和签名，不能按包名相同混用。

```powershell
python tools/publish-launcher-batch.py --apk10 "红魔10最终签名.apk" --apk11 "红魔11最终签名.apk" --apk12 "红魔12最终签名.apk" `
  --launcher10-root "红魔10独立工作树" --launcher11-root "红魔11独立工作树" --launcher12-root "红魔12独立工作树" `
  --notes-file "本批更新说明.txt" --sdk "Android SDK目录" --java-home "JDK目录" --dry-run
```

检查通过后以相同参数去掉 `--dry-run`。所有附件完成上传和校验后才公开Release；三个独立清单在同一Git提交更新。重复发布相同版本必须保持APK哈希和签名一致；仅迁移到同批次发行不改变安装包身份。网络中断后可使用相同命令复用已完成附件。

源码：[红魔10分支](https://github.com/liligit1815/LS_RedMagicLauncher/tree/redmagic-10) · [红魔11分支](https://github.com/liligit1815/LS_RedMagicLauncher/tree/redmagic-11) · [红魔12分支](https://github.com/liligit1815/LS_RedMagicLauncher/tree/redmagic-12)。本仓库不保存桌面源码、编译过程文件或私有签名材料。

## 文件布局

- `apps.json`：软件目录、包名、公开发行证书指纹。
- `updates/*.json`：各软件当前选定的版本、更新说明、下载地址和校验值。
- `tools/`：发布与清单验证工具。
- Releases 附件：已签名 APK。软件源码继续保存在各自项目中。

清单格式见 [更新协议](docs/update-protocol.md)。维护时可以运行 `python tools/validate.py` 和 `python -m unittest discover -s tools -p "test_*.py"`。
