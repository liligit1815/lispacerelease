"""Publish a verified signed APK, then promote its independent update manifest.

Credentials come from GH_TOKEN/GITHUB_TOKEN, GitHub CLI, or Git Credential Manager.
Neither credentials nor signing private keys are written to the repository.
"""
import argparse
import base64
import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from protocol import REPOSITORY, MAX_APK_SIZE, MAX_FEED_SIZE, require, validate_feed, ensure_forward_update

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)
    require(result.returncode == 0, f"工具执行失败：{Path(str(command[0])).name}\n{result.stdout}\n{result.stderr}")
    return result.stdout + result.stderr


def token():
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    if shutil.which("gh"):
        result = subprocess.run(["gh", "auth", "token", "--hostname", "github.com"],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    if shutil.which("git"):
        result = subprocess.run(["git", "-c", "credential.interactive=never", "credential", "fill"],
                                input="protocol=https\nhost=github.com\n\n", capture_output=True,
                                text=True, timeout=30, env={**os.environ, "GCM_INTERACTIVE": "never"})
        if result.returncode == 0:
            values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            if values.get("password"):
                return values["password"]
    raise ValueError("没有可用的 GitHub 登录凭据。请先运行 gh auth login，或设置仅本仓库 Contents 写权限的 GH_TOKEN。")


class GitHub:
    def __init__(self, secret):
        self.secret = secret

    def request(self, method, path, data=None, missing_ok=False):
        require(path.startswith(f"/repos/{REPOSITORY}/"), "拒绝操作其他仓库")
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
        headers = {"Accept": "application/vnd.github+json", "Authorization": "Bearer " + self.secret,
                   "User-Agent": "lispacerelease-publisher", "X-GitHub-Api-Version": "2022-11-28"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(API + path, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404 and missing_ok:
                return None
            details = error.read(4096).decode("utf-8", errors="replace")
            raise ValueError(f"GitHub 请求失败：HTTP {error.code} {details}") from None

    def upload(self, upload_url, apk, asset_name):
        parsed = urllib.parse.urlsplit(upload_url.split("{", 1)[0])
        require(parsed.scheme == "https" and parsed.netloc == "uploads.github.com" and
                parsed.path.startswith(f"/repos/{REPOSITORY}/releases/"), "无效的 GitHub 附件上传地址")
        connection = http.client.HTTPSConnection("uploads.github.com", timeout=120)
        try:
            connection.putrequest("POST", parsed.path + "?name=" + urllib.parse.quote(asset_name, safe=""))
            connection.putheader("Authorization", "Bearer " + self.secret)
            connection.putheader("User-Agent", "lispacerelease-publisher")
            connection.putheader("Accept", "application/vnd.github+json")
            connection.putheader("Content-Type", "application/vnd.android.package-archive")
            connection.putheader("Content-Length", str(apk.stat().st_size))
            connection.endheaders()
            with apk.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    connection.send(chunk)
            response = connection.getresponse()
            body = response.read(1024 * 1024)
            require(response.status == 201, f"APK 上传失败：HTTP {response.status} {body.decode('utf-8', errors='replace')}")
            return json.loads(body)
        finally:
            connection.close()


def build_tools(args):
    sdk = args.sdk or os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    require(sdk, "请用 --sdk 指定已有 Android SDK，或设置 ANDROID_HOME")
    directories = sorted((Path(sdk) / "build-tools").glob("*"), reverse=True)
    selected = next((item for item in directories if (item / "aapt.exe").exists() or (item / "aapt").exists()), None)
    require(selected is not None, "Android SDK 没有可用的 build-tools")
    aapt = selected / ("aapt.exe" if os.name == "nt" else "aapt")
    java_home = args.java_home or os.environ.get("JAVA_HOME")
    java = str(Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")) if java_home else shutil.which("java")
    require(java and Path(java).is_file(), "请用 --java-home 指定 JDK，或配置 JAVA_HOME")
    require((selected / "lib" / "apksigner.jar").exists(), "SDK 缺少 apksigner.jar")
    return aapt, java, selected / "lib" / "apksigner.jar"


def inspect_apk(args, app):
    apk = Path(args.apk).resolve()
    require(apk.is_file() and apk.suffix.lower() == ".apk", "请指定已签名 APK 文件")
    require(0 < apk.stat().st_size <= MAX_APK_SIZE, "APK 必须小于 2 GiB")
    aapt, java, signer = build_tools(args)
    badging = run([str(aapt), "dump", "badging", str(apk)])
    package = re.search(r"^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']*)'", badging, re.MULTILINE)
    minimum = re.search(r"^sdkVersion:'([0-9]+)'", badging, re.MULTILINE)
    require(package and minimum, "不能读取 APK 的包名、版本和最低 Android 版本")
    require(package[1] == app["packageName"], "所选 APK 属于其他软件")
    signatures = run([java, "-jar", str(signer), "verify", "--verbose", "--print-certs", str(apk)])
    certificates = re.findall(r"Signer #[0-9]+ certificate SHA-256 digest: ([0-9a-fA-F]{64})", signatures)
    require(len(certificates) == 1, "第一版发布工具只支持单签名者 APK")
    certificate = certificates[0].lower()
    require(app.get("signingCertificateSha256") == certificate, "发行证书不匹配；拒绝换签名 APK")
    with apk.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    notes = Path(args.notes_file).read_text(encoding="utf-8-sig").strip()
    require(notes, "更新说明不能为空")
    tag = app["id"] + "-v" + package[2]
    asset_name = app["id"] + "-" + package[2] + ".apk"
    url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{asset_name}"
    feed = {"schemaVersion": 1, "appId": app["id"], "packageName": app["packageName"],
            "release": {"versionCode": int(package[2]), "versionName": package[3], "minSdk": int(minimum[1]),
                        "publishedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                        "notes": notes, "apk": {"url": url, "size": apk.stat().st_size,
                                                "sha256": digest, "certificateSha256": certificate}}}
    validate_feed(feed, app)
    return apk, feed, tag, asset_name


def promote(api, app, apk, feed, tag, asset_name):
    base = f"/repos/{REPOSITORY}"
    path = f"{base}/contents/updates/{app['id']}.json"
    content = api.request("GET", path + "?ref=main", missing_ok=True)
    current = None
    if content:
        current_bytes = base64.b64decode(content["content"])
        require(len(current_bytes) <= MAX_FEED_SIZE, "现有清单过大")
        current = validate_feed(json.loads(current_bytes), app)
    ensure_forward_update(current, feed)
    release = api.request("GET", f"{base}/releases/tags/{tag}", missing_ok=True)
    if not release:
        release = api.request("POST", f"{base}/releases", {
            "tag_name": tag, "target_commitish": "main", "name": f"{app['name']} {feed['release']['versionName']} ({feed['release']['versionCode']})",
            "body": feed["release"]["notes"], "draft": True,
            "prerelease": bool(re.search(r"alpha|beta|test|\brc", feed["release"]["versionName"], re.IGNORECASE)),
            "make_latest": "false"})
    asset = next((item for item in release.get("assets", []) if item["name"] == asset_name), None)
    if asset:
        require(asset.get("size") == feed["release"]["apk"]["size"] and
                asset.get("digest") == "sha256:" + feed["release"]["apk"]["sha256"],
                "现有附件无法确认与本地 APK 完全相同；保留原附件，停止发布")
    else:
        require(release["draft"], "现有已公开 Release 缺少预期 APK；请人工检查，不自动修改历史发布")
        asset = api.upload(release["upload_url"], apk, asset_name)
    require(asset.get("state") == "uploaded" and asset.get("size") == feed["release"]["apk"]["size"] and
            asset.get("digest") == "sha256:" + feed["release"]["apk"]["sha256"] and
            asset.get("browser_download_url") == feed["release"]["apk"]["url"], "GitHub 附件校验失败")
    if release["draft"]:
        release = api.request("PATCH", f"{base}/releases/{release['id']}", {"draft": False, "make_latest": "false"})
    feed["release"]["publishedAt"] = release["published_at"]
    validate_feed(feed, app)
    data = json.dumps(feed, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    if current == feed:
        print("Release 和更新清单均已发布，无需重复修改。")
    else:
        update = {"message": f"Publish {app['id']} {feed['release']['versionCode']}",
                  "branch": "main", "content": base64.b64encode(data).decode("ascii")}
        if content:
            update["sha"] = content["sha"]
        api.request("PUT", path, update)
    local_feed = ROOT / "updates" / (app["id"] + ".json")
    local_feed.write_bytes(data)
    print("已发布：" + release["html_url"])
    print(f"更新源：https://raw.githubusercontent.com/{REPOSITORY}/main/updates/{app['id']}.json")


def main():
    parser = argparse.ArgumentParser(description="上传已签名 APK，再更新该软件的独立更新源")
    parser.add_argument("--app", required=True, help="apps.json 中的软件 id")
    parser.add_argument("--apk", required=True, help="已签名 APK 的完整路径")
    parser.add_argument("--notes-file", required=True, help="UTF-8 更新说明文件")
    parser.add_argument("--sdk", help="Android SDK 路径")
    parser.add_argument("--java-home", help="JDK 路径")
    parser.add_argument("--dry-run", action="store_true", help="检查并显示发布计划，不访问 GitHub")
    args = parser.parse_args()
    registry = json.loads((ROOT / "apps.json").read_text(encoding="utf-8"))
    require(registry["repository"] == REPOSITORY, "仓库配置不匹配")
    app = next((item for item in registry["apps"] if item["id"] == args.app), None)
    require(app, "未知软件 id")
    apk, feed, tag, asset_name = inspect_apk(args, app)
    if args.dry_run:
        current = json.loads((ROOT / "updates" / (app["id"] + ".json")).read_text(encoding="utf-8"))
        ensure_forward_update(validate_feed(current, app), feed)
        print(json.dumps(feed, ensure_ascii=False, indent=2))
        print("检查通过；未联网、未上传、未修改更新源。")
        return
    promote(GitHub(token()), app, apk, feed, tag, asset_name)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print("发布停止：" + str(error), file=sys.stderr)
        sys.exit(1)
