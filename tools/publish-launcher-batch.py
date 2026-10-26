"""Publish verified launcher series together and atomically promote their feeds.

Dry-run is local only. Source roots provide identity verification, not build
dependencies. No launcher source, registry or tracked local feed is modified.
"""
import argparse
import base64
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import hashlib
import http.client
import json
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import zipfile

import publish
from protocol import REPOSITORY, MAX_FEED_SIZE, require, validate_feed, ensure_forward_update

ROOT = Path(__file__).resolve().parents[1]
BASE = f"/repos/{REPOSITORY}"
IDS = ("stacked-desktop", "stacked-desktop-redmagic10")
CERTIFICATE = "4e5c23c41de3d7d56f120309e9fe8dab7d53483c50ef18a176e00558abf7b8cb"
ANDROID = "http://schemas.android.com/apk/res/android"
SERIES_KEY = "com.lizhou.launcher.series"


class RemoteFailure(ValueError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"GitHub 请求未确认成功（HTTP {status}）" if status else "GitHub 请求未确认成功")


class SafeGitHub(publish.GitHub):
    """Reuse authenticated transport without exposing API bodies or signed URLs."""
    def request(self, *args, **kwargs):
        read_only = args and args[0] == "GET"
        for attempt in range(3 if read_only else 1):
            try:
                return super().request(*args, **kwargs)
            except ValueError as error:
                match = re.search(r"HTTP ([0-9]{3})", str(error))
                status = int(match[1]) if match else None
                if not read_only or status not in (502, 503, 504) or attempt == 2:
                    raise RemoteFailure(status) from None
            except (OSError, urllib.error.URLError, http.client.HTTPException, json.JSONDecodeError):
                if not read_only or attempt == 2:
                    raise RemoteFailure() from None
            time.sleep(0.25 * (attempt + 1))

    def upload(self, *args, **kwargs):
        try:
            return super().upload(*args, **kwargs)
        except ValueError as error:
            match = re.search(r"HTTP ([0-9]{3})", str(error))
            raise RemoteFailure(int(match[1]) if match else None) from None
        except (OSError, urllib.error.URLError, http.client.HTTPException, json.JSONDecodeError):
            raise RemoteFailure() from None


def load_tool(root, name):
    path = root / "tools" / name
    spec = importlib.util.spec_from_file_location("batch_" + str(root.name) + name.replace("-", "_"), path)
    require(spec is not None and spec.loader is not None and path.is_file(), "缺少该系列本地核验模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def asset_url(tag, name):
    return f"https://github.com/{REPOSITORY}/releases/download/" + \
        urllib.parse.quote(tag, safe="") + "/" + urllib.parse.quote(name, safe="")


def matching_asset_url(value, tag, name):
    if not isinstance(value, str) or re.search(r"%(?![0-9a-fA-F]{2})", value):
        return False
    parsed = urllib.parse.urlsplit(value)
    prefix = f"/{REPOSITORY}/releases/download/"
    pieces = parsed.path[len(prefix):].split("/") if parsed.path.startswith(prefix) else []
    # Split the raw path first and decode each segment exactly once.
    return (parsed.scheme == "https" and parsed.netloc == "github.com"
            and not parsed.query and not parsed.fragment and "?" not in value and "#" not in value
            and len(pieces) == 2 and not re.search(r"%2f|%5c", parsed.path, re.I)
            and [urllib.parse.unquote(piece) for piece in pieces] == [tag, name])


def notes_for(code11, code10):
    return (f"红魔11系列安装堆叠桌面11-{code11}.apk\n"
            f"红魔10系列安装堆叠桌面10-{code10}.apk\n其它机型自测。")


def manifest_events(gate, apk):
    with zipfile.ZipFile(apk) as archive:
        return gate.manifest_semantics(archive.read("AndroidManifest.xml"))


def family_metadata(events):
    stack, result = [], []
    for event in events:
        if event["event"] == "start":
            if event["name"] == "meta-data" and any(
                    attr["name"] == "name" and attr["value"] == SERIES_KEY for attr in event["attributes"]):
                result.append((tuple(stack), event))
            stack.append(event["name"])
        elif event["event"] == "end":
            require(stack and stack.pop() == event["name"], "APK Manifest 层级不完整")
    require(not stack, "APK Manifest 层级不完整")
    return result


def sdk_identity(events):
    require(not any(attr["name"] == "versionCodeMajor" for attr in events[0]["attributes"]),
            "发行工具不支持 versionCodeMajor，不能忽略版本高位")
    nodes = [event for event in events if event["event"] == "start" and event["name"] == "uses-sdk"]
    require(len(nodes) == 1, "APK 必须只有一个 uses-sdk")
    attrs = nodes[0]["attributes"]
    minimums = [attr for attr in attrs if attr["name"] == "minSdkVersion"]
    targets = [attr for attr in attrs if attr["name"] == "targetSdkVersion"]
    compile_values = [attr for attr in events[0]["attributes"] if attr["name"] == "compileSdkVersion"]
    require(len(minimums) == len(targets) == len(compile_values) == 1 and
            all(attr["namespace"] == ANDROID and attr["type"] == 0x10
                for attr in minimums + targets + compile_values) and
            minimums[0]["resourceId"] == 0x0101020c and targets[0]["resourceId"] == 0x01010270,
            "APK SDK 身份字段无效")
    return minimums[0]["value"], targets[0]["value"], compile_values[0]["value"]


def source_sdk(root):
    text = (root / "src/apktool.yml").read_text(encoding="utf-8-sig")
    block = re.search(r"(?ms)^sdkInfo:\s*\n(.*?)(?=^\S|\Z)", text)
    require(block is not None, "源码缺少 sdkInfo")
    values = {}
    for key in ("minSdkVersion", "targetSdkVersion"):
        matches = re.findall(r"(?m)^  " + key + r":\s*['\"]?([0-9]+)['\"]?\s*$", block[1])
        require(len(matches) == 1, "源码 SDK 字段无效")
        values[key] = int(matches[0])
    return values["minSdkVersion"], values["targetSdkVersion"]


def verify_family(root, gate, apk, feed, series):
    events, versions = manifest_events(gate, apk)
    expected = gate.expected_versions(root / "src/apktool.yml")
    require(versions == expected and versions == {
        "versionCode": feed["release"]["versionCode"], "versionName": feed["release"]["versionName"]},
        "已签 APK 版本与该系列实际源码不一致")
    baseline_events, _ = manifest_events(gate, root / "original.apk")
    platform = {10: 35, 11: 36, 12: 37}[series]
    minimum, target = source_sdk(root)
    require(target == platform and sdk_identity(events) == (minimum, platform, platform) and
            sdk_identity(baseline_events)[1:] == (platform, platform) and
            feed["release"]["minSdk"] == minimum, "APK 与源码或原厂 SDK 系列身份不匹配")
    metadata = family_metadata(events)
    if series == 10:
        gate.strip_exact_update_nodes(events)
        require(len(metadata) == 1 and metadata[0][0] == ("manifest", "application"),
                "红魔10必须有唯一 application 直属系列标识")
        wanted = [("name", 0x01010003, SERIES_KEY), ("value", 0x01010024, "redmagic10")]
        attributes = metadata[0][1]["attributes"]
        require(len(attributes) == 2 and all(any(attr == {
            "namespace": ANDROID, "name": name, "resourceId": rid, "type": 3, "value": value}
            for attr in attributes) for name, rid, value in wanted), "红魔10系列标识类型或值不正确")
    else:
        require(not metadata, "该系列底包不能带红魔10系列标识")


def preflight(args, temporary):
    registry = json.loads((ROOT / "apps.json").read_text(encoding="utf-8-sig"))
    require(registry.get("repository") == REPOSITORY, "仓库配置不匹配")
    include12 = bool(getattr(args, "apk12", None) or getattr(args, "launcher12_root", None))
    require(not include12 or (args.apk12 and args.launcher12_root), "12系列需要同时指定APK与独立源码根目录")
    identities = IDS + (("stacked-desktop-redmagic12",) if include12 else ())
    series_list = [11, 10] + ([12] if include12 else [])
    apps = []
    for identity in identities:
        matches = [app for app in registry["apps"] if app["id"] == identity]
        require(len(matches) == 1, "apps.json 必须登记该系列的独立桌面更新源")
        app = matches[0]
        require(app["packageName"] == "com.zte.mifavor.launcher" and
                app["signingCertificateSha256"] == CERTIFICATE, "桌面登记身份或证书不匹配")
        apps.append(app)
    roots = [Path(args.launcher11_root).resolve(), Path(args.launcher10_root).resolve()]
    if include12:
        roots.append(Path(args.launcher12_root).resolve())
    require(len(set(roots)) == len(roots), "各系列必须使用各自独立源码根目录")
    gates = [load_tool(root, "check-launcher-stack-only-apk.py") for root in roots]
    versions = [gate.expected_versions(root / "src/apktool.yml") for root, gate in zip(roots, gates)]
    code11, code10 = [item["versionCode"] for item in versions[:2]]
    tag = f"stacked-desktop-redmagic10-batch-11-{code11}-10-{code10}"
    notes = notes_for(code11, code10)
    if include12:
        code12 = versions[2]["versionCode"]
        tag += f"-12-{code12}"
        require(getattr(args, "notes_file", None), "三系列发行须提供本次更新内容和下载说明")
    if getattr(args, "notes_file", None):
        notes = Path(args.notes_file).read_text(encoding="utf-8-sig").strip()
        require(notes and len(notes) <= 12000, "发行说明为空或过长")
        for series, version in zip(series_list, versions):
            require(f"堆叠桌面{series}-{version['versionCode']}" in notes, "发行说明缺少该系列实际下载版本")
    notes_file = temporary / "notes.txt"
    notes_file.write_text(notes, encoding="utf-8")
    feeds, paths = [], []
    sources = [args.apk11, args.apk10] + ([args.apk12] if include12 else [])
    for index, (root, gate, app, source) in enumerate(zip(roots, gates, apps, sources)):
        source = Path(source).resolve()
        require(source.is_file(), "已签 APK 文件不存在")
        snapshot = temporary / f"launcher{series_list[index]}.apk"
        shutil.copyfile(source, snapshot)
        inspect_args = SimpleNamespace(apk=snapshot, notes_file=notes_file, sdk=args.sdk, java_home=args.java_home)
        apk, feed, _, _ = publish.inspect_apk(inspect_args, app)
        verify_family(root, gate, apk, feed, series_list[index])
        paths.append(apk)
        feeds.append(feed)
    # One asset per series. The 10 client requires its canonical ASCII prefix;
    # a Chinese label also makes that same asset the manual download entry.
    names = [f"11-{code11}.apk", f"stacked-desktop-redmagic10-{code10}.apk"]
    labels = [f"堆叠桌面11-{code11}.apk", f"堆叠桌面10-{code10}.APK"]
    feeds[0]["release"]["apk"]["url"] = asset_url(tag, names[0])
    feeds[1]["release"]["apk"]["url"] = asset_url(tag, names[1])
    indices = [0, 1]
    if include12:
        names.append(f"12-{code12}.apk")
        labels.append(f"堆叠桌面12-{code12}.apk")
        indices.append(2)
        feeds[2]["release"]["apk"]["url"] = asset_url(tag, names[2])
    for feed in feeds[1:]:
        feed["release"]["publishedAt"] = feeds[0]["release"]["publishedAt"]
    for root, app, feed in zip(roots, apps, feeds):
        validate_feed(feed, app)
        client = load_tool(root, "check-launcher-online-release.py")
        client.parse_manifest(json.dumps(feed, ensure_ascii=False).encode("utf-8"))
        local = ROOT / "updates" / (app["id"] + ".json")
        current = validate_feed(json.loads(local.read_text(encoding="utf-8-sig")), app) if local.exists() else None
        ensure_forward_update(current, feed)
    assets = [{"name": name, "label": label, "apk": paths[index], "size": feeds[index]["release"]["apk"]["size"],
               "sha256": feeds[index]["release"]["apk"]["sha256"]}
              for name, label, index in zip(names, labels, indices)]
    for asset in assets:
        with asset["apk"].open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        require(asset["apk"].stat().st_size == asset["size"] and digest == asset["sha256"],
                "上传快照与已核验 APK 不符")
    title = (f"堆叠桌面 10 / 11 / 12（{code10} / {code11} / {code12}）" if include12
             else f"堆叠桌面11 / 10（{code11} / {code10}）")
    return {"apps": apps, "feeds": feeds, "tag": tag, "notes": notes, "assets": assets, "title": title}


def head_sha(api):
    value = api.request("GET", BASE + "/git/ref/heads/main")
    require(value.get("ref") == "refs/heads/main" and value.get("object", {}).get("type") == "commit",
            "main 引用不是已确认的提交")
    sha = value["object"]["sha"]
    require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "main 提交标识无效")
    return sha


def remote_feeds(api, apps, sha):
    result = []
    for app in apps:
        content = api.request("GET", BASE + f"/contents/updates/{app['id']}.json?ref={sha}", missing_ok=True)
        if content is None:
            result.append(None)
            continue
        require(content.get("encoding") == "base64" and content.get("type") == "file", "远端更新源不是普通文件")
        data = base64.b64decode(content["content"], validate=False)
        require(len(data) <= MAX_FEED_SIZE, "远端更新源超过限制")
        result.append(validate_feed(json.loads(data), app))
    return result


def validate_release(release, plan):
    require(type(release.get("id")) is int and release["id"] > 0 and
            type(release.get("draft")) is bool and release.get("tag_name") == plan["tag"], "Release 身份不匹配")
    require(release.get("body") == plan["notes"], "已有 Release 正文不等于本批次发行说明")
    require(not release.get("prerelease"), "本批次不能复用 prerelease")


def list_assets(api, release):
    result = []
    for page in range(1, 11):
        assets = api.request("GET", BASE + f"/releases/{release['id']}/assets?per_page=100&page={page}")
        require(isinstance(assets, list), "附件列表响应无效")
        result.extend(assets)
        if len(assets) < 100:
            return result
    raise ValueError("附件过多，不能可靠核验此批次")


def check_asset(asset, expected, tag=None, *, check_label=True):
    require(type(asset.get("id")) is int and asset["id"] > 0 and
            asset.get("state") == "uploaded" and asset.get("name") == expected["name"] and
            asset.get("size") == expected["size"] and asset.get("digest") == "sha256:" + expected["sha256"],
            "GitHub 附件状态、大小或哈希与已核验 APK 不符")
    if check_label and expected.get("label") is not None:
        require(asset.get("label") == expected["label"], "GitHub 附件中文显示标签不符；不修改已公开附件")
    if tag is not None:
        require(matching_asset_url(asset.get("browser_download_url"), tag, expected["name"]),
                "公开附件下载地址不是预期仓库、标签和名称")


def draft_asset_label(api, release, asset, expected, plan):
    check_asset(asset, expected, check_label=False)
    if expected.get("label") is not None and asset.get("label") != expected["label"]:
        current = api.request("GET", BASE + f"/releases/{release['id']}")
        validate_release(current, plan)
        require(current["id"] == release["id"] and current["draft"],
                "Release 已公开；保留附件，不自动修改中文显示标签")
        changed = api.request("PATCH", BASE + f"/releases/assets/{asset['id']}",
                              {"name": expected["name"], "label": expected["label"]})
        require(changed.get("id") == asset["id"], "标签写入返回了其他附件身份")
        check_asset(changed, expected)
        return changed
    check_asset(asset, expected)
    return asset


def verified_assets(api, release, plan, *, public=False):
    assets = list_assets(api, release)
    require(len(assets) == len(plan["assets"]) and {asset.get("name") for asset in assets} ==
            {asset["name"] for asset in plan["assets"]}, "本批次必须只有全部唯一预期 APK 附件")
    for expected in plan["assets"]:
        check_asset(next(asset for asset in assets if asset["name"] == expected["name"]),
                    expected, plan["tag"] if public else None)


def publish_assets(api, plan):
    release = publish.find_release(api, BASE, plan["tag"])
    if release is None:
        release = api.request("POST", BASE + "/releases", {
            "tag_name": plan["tag"], "target_commitish": "main",
            "name": plan.get("title", f"堆叠桌面11 / 10（{plan['feeds'][0]['release']['versionCode']} / {plan['feeds'][1]['release']['versionCode']}）"),
            "body": plan["notes"], "draft": True, "prerelease": False, "make_latest": "false"})
    validate_release(release, plan)
    if release["draft"]:
        parsed = urllib.parse.urlsplit(release["upload_url"].split("{", 1)[0])
        require(parsed.scheme == "https" and parsed.netloc == "uploads.github.com" and
                parsed.path == f"/repos/{REPOSITORY}/releases/{release['id']}/assets" and
                not parsed.query and not parsed.fragment, "草稿上传地址不正确")
        assets = list_assets(api, release)
        require(len({asset.get("name") for asset in assets}) == len(assets) and
                {asset.get("name") for asset in assets} <= {item["name"] for item in plan["assets"]},
                "草稿存在重复或非本批次附件，保留原草稿并停止")
        missing = []
        for expected in plan["assets"]:
            asset = next((item for item in assets if item["name"] == expected["name"]), None)
            require(not asset or asset.get("state") != "starter",
                    "草稿存在未完成附件；保留原附件，检查后重试，不自动删除可能已公开的附件")
            if asset is None:
                missing.append(expected)
            else:
                draft_asset_label(api, release, asset, expected, plan)
        # Distinct immutable attachments may upload together. Keep the draft and
        # every feed unchanged unless every worker completes and metadata agrees.
        uploaded = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(api.upload, release["upload_url"], item["apk"], item["name"]): item
                       for item in missing}
            for future in as_completed(futures):
                uploaded.append((futures[future], future.result()))
        for expected, asset in uploaded:
            draft_asset_label(api, release, asset, expected, plan)
        # Re-read all remote metadata immediately before making the draft public.
        release = api.request("GET", BASE + f"/releases/{release['id']}")
        validate_release(release, plan)
        verified_assets(api, release, plan, public=not release["draft"])
        if release["draft"]:
            api.request("PATCH", BASE + f"/releases/{release['id']}", {"draft": False, "make_latest": "false"})
    release = api.request("GET", BASE + f"/releases/{release['id']}")
    validate_release(release, plan)
    require(not release["draft"], "Release 尚未确认公开，不更新清单")
    verified_assets(api, release, plan, public=True)
    timestamp = release.get("published_at")
    require(isinstance(timestamp, str) and timestamp.endswith("Z"), "Release 发布时间未确认")
    return timestamp


def commit_feeds(api, plan, published_at, attempts=4):
    proposed = deepcopy(plan["feeds"])
    for app, feed in zip(plan["apps"], proposed):
        feed["release"]["publishedAt"] = published_at
        validate_feed(feed, app)
    for _ in range(attempts):
        sha = head_sha(api)
        current = remote_feeds(api, plan["apps"], sha)
        for old, new in zip(current, proposed):
            ensure_forward_update(old, new)
        if current == proposed:
            return {"sha": sha, "changed": False}
        commit = api.request("GET", BASE + "/git/commits/" + sha)
        tree = []
        for app, feed in zip(plan["apps"], proposed):
            data = json.dumps(feed, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
            require(len(data) <= MAX_FEED_SIZE, "更新清单超过限制")
            blob = api.request("POST", BASE + "/git/blobs", {
                "content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
            tree.append({"path": f"updates/{app['id']}.json", "mode": "100644", "type": "blob", "sha": blob["sha"]})
        # A concurrent publication must be evaluated against its own current feeds.
        if head_sha(api) != sha:
            continue
        new_tree = api.request("POST", BASE + "/git/trees", {"base_tree": commit["tree"]["sha"], "tree": tree})
        new_commit = api.request("POST", BASE + "/git/commits", {
            "message": "Publish launcher batch " + plan["tag"], "tree": new_tree["sha"], "parents": [sha]})
        try:
            api.request("PATCH", BASE + "/git/refs/heads/main", {"sha": new_commit["sha"], "force": False})
        except (RemoteFailure, ConnectionError, OSError) as error:
            if isinstance(error, RemoteFailure) and error.status not in (None, 409, 422):
                raise
            continue
        # Verify every feed from one immutable current HEAD after the ref update.
        final_sha = head_sha(api)
        final = remote_feeds(api, plan["apps"], final_sha)
        if final == proposed:
            return {"sha": new_commit["sha"], "observed_head": final_sha, "changed": True}
        # A newer concurrent update is preserved; the next iteration rejects downgrade.
    raise ValueError("main 并发变化或写入结果未确认；未强制更新，重试会重新核验各系列更新源")


def promote_batch(api, plan):
    current = remote_feeds(api, plan["apps"], head_sha(api))
    for old, new in zip(current, plan["feeds"]):
        ensure_forward_update(old, new)
    published_at = publish_assets(api, plan)
    result = commit_feeds(api, plan, published_at)
    return {"tag": plan["tag"], "release_url": f"https://github.com/{REPOSITORY}/releases/tag/{plan['tag']}",
            "feeds": [f"https://raw.githubusercontent.com/{REPOSITORY}/main/updates/{app['id']}.json"
                      for app in plan["apps"]], "commit": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("apk11", "apk10", "launcher11-root", "launcher10-root"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--apk12")
    parser.add_argument("--launcher12-root")
    parser.add_argument("--notes-file", help="包含更新内容和各系列实际版本的UTF-8发行说明")
    parser.add_argument("--sdk")
    parser.add_argument("--java-home")
    parser.add_argument("--dry-run", action="store_true", help="仅本地核验；不读取凭据、不联网、不发布")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="launcher-batch-") as temporary:
        plan = preflight(args, Path(temporary))
        if args.dry_run:
            print(json.dumps({"dry_run": True, "tag": plan["tag"], "notes": plan["notes"],
                "feeds": plan["feeds"], "assets": [{key: value for key, value in asset.items() if key != "apk"}
                for asset in plan["assets"]]}, ensure_ascii=False, indent=2))
            print("本地核验通过；未联网、未读取凭据、未修改远端或本地更新源。")
        else:
            print(json.dumps(promote_batch(SafeGitHub(publish.token()), plan), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as error:
        # Never print arbitrary transport exceptions (which can include signed queries).
        message = str(error) if type(error) is ValueError or isinstance(error, RemoteFailure) else type(error).__name__
        print("发布停止：" + message, file=sys.stderr)
        raise SystemExit(1)
