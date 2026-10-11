# LiSpace 软件发布与在线更新

红魔 Duo、堆叠桌面、iQOO 肩键扩展的 APK 发布中心。各软件独立发版，通过自己的更新清单检查新版。

## 下载

请进入 [Releases](https://github.com/liligit1815/lispacerelease/releases) 选择软件并下载 `.apk` 附件。GitHub 自动生成的 Source code ZIP/TAR 是本更新仓库的文件，不能用于安装软件。

| 软件 | 软件标识 | 更新清单 |
| --- | --- | --- |
| 红魔 Duo | `redmagic-duo` | [redmagic-duo.json](https://raw.githubusercontent.com/liligit1815/lispacerelease/main/updates/redmagic-duo.json) |
| 堆叠桌面 | `stacked-desktop` | [stacked-desktop.json](https://raw.githubusercontent.com/liligit1815/lispacerelease/main/updates/stacked-desktop.json) |
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

`--dry-run` 检查 APK 并显示发布计划，检查通过后去掉该参数进行发布。其他软件将 `--app` 改为 `stacked-desktop` 或 `iqoo-shoulder`。

发布直接更新 GitHub 上的清单，本地发布记录保存到忽略的 `out/published/`。需要同步本地仓库清单时运行 `git pull --ff-only`；先同步再进行下一次离线预检查。

每个软件只有一个更新通道。运行发布工具就表示选择该版本推送给用户；版本名包含 alpha/test 的版本同样可以被选择。工具拒绝降低 `versionCode`、以同版本号换包、改变既有签名或上传其他软件的 APK。网络中断时清单保持旧版本，可以用相同命令重试。

每个软件、版本分别生成标签，例如 `redmagic-duo-v20518`。客户端读取独立清单，避免仓库级“Latest”混淆多个软件。

## 文件布局

- `apps.json`：软件目录、包名、公开发行证书指纹。
- `updates/*.json`：各软件当前选定的版本、更新说明、下载地址和校验值。
- `tools/`：发布与清单验证工具。
- Releases 附件：已签名 APK。软件源码继续保存在各自项目中。

清单格式见 [更新协议](docs/update-protocol.md)。维护时可以运行 `python tools/validate.py` 和 `python -m unittest discover -s tools -p "test_*.py"`。
