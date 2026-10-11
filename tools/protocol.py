"""Validation shared by the publisher and offline repository checks."""
import datetime
import json
import re
from urllib.parse import urlsplit

REPOSITORY = "liligit1815/lispacerelease"
MAX_APK_SIZE = 2 * 1024**3 - 1
MAX_FEED_SIZE = 64 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_feed(feed, app):
    require(isinstance(feed, dict), "更新清单必须是 JSON 对象")
    require(type(feed.get("schemaVersion")) is int and feed["schemaVersion"] == 1,
            "不支持的更新清单格式")
    require(feed.get("appId") == app["id"], "软件标识不匹配")
    require(feed.get("packageName") == app["packageName"], "包名不匹配")
    require("release" in feed, "缺少 release")
    release = feed["release"]
    if release is None:
        return feed
    require(isinstance(release, dict), "release 必须是对象或 null")
    require(type(release.get("versionCode")) is int and 0 < release["versionCode"] <= 2**31 - 1,
            "versionCode 必须为正整数")
    require(isinstance(release.get("versionName"), str) and 0 < len(release["versionName"]) <= 128,
            "缺少有效的显示版本")
    require(type(release.get("minSdk")) is int and release["minSdk"] > 0, "无效的 minSdk")
    require(isinstance(release.get("notes"), str) and len(release["notes"]) <= 12000,
            "更新说明必须为不超过 12000 个字符的文字")
    require(isinstance(release.get("publishedAt"), str), "缺少发布时间")
    timestamp = datetime.datetime.fromisoformat(release["publishedAt"].replace("Z", "+00:00"))
    require(timestamp.tzinfo is not None, "发布时间必须含时区")
    apk = release.get("apk")
    require(isinstance(apk, dict), "缺少 APK 信息")
    require(type(apk.get("size")) is int and 0 < apk["size"] <= MAX_APK_SIZE, "APK 大小无效")
    for key in ("sha256", "certificateSha256"):
        require(isinstance(apk.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", apk[key]),
                "无效的 " + key)
    certificate = app.get("signingCertificateSha256")
    require(certificate and apk["certificateSha256"] == certificate,
            "APK 签名不符合该软件既有发行证书")
    require(isinstance(apk.get("url"), str), "缺少下载地址")
    parsed = urlsplit(apk["url"])
    prefix = f"/{REPOSITORY}/releases/download/"
    require(parsed.scheme == "https" and parsed.netloc == "github.com" and
            parsed.path.startswith(prefix) and parsed.path.endswith(".apk") and
            len(parsed.path[len(prefix):].split("/")) == 2 and not parsed.query and not parsed.fragment,
            "下载地址必须是本仓库的 GitHub Release APK 地址")
    require(len(json.dumps(feed, ensure_ascii=False).encode("utf-8")) <= MAX_FEED_SIZE,
            "更新清单过大")
    return feed


def ensure_forward_update(current, proposed):
    """A repeated identical publication is safe; downgrades or reused codes are rejected."""
    previous = current.get("release") if current else None
    release = proposed["release"]
    if not previous:
        return
    require(release["versionCode"] >= previous["versionCode"], "拒绝将更新源回退到旧版本")
    if release["versionCode"] == previous["versionCode"]:
        require(release["apk"]["sha256"] == previous["apk"]["sha256"] and
                release["apk"]["certificateSha256"] == previous["apk"]["certificateSha256"],
                "同一个 versionCode 不允许替换为另一个 APK；请先递增版本号")
