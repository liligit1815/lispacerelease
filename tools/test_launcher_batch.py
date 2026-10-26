"""Failure, retry and concurrency tests for publishing the launcher pair."""
import base64
from copy import deepcopy
import importlib.util
import hashlib
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("launcher_batch", Path(__file__).with_name("publish-launcher-batch.py"))
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def fixture():
    codes = (260029, 250001)
    tag = f"stacked-desktop-redmagic10-batch-11-{codes[0]}-10-{codes[1]}"
    names = (f"11-{codes[0]}.apk", f"stacked-desktop-redmagic10-{codes[1]}.apk")
    labels = (f"堆叠桌面11-{codes[0]}.apk", f"堆叠桌面10-{codes[1]}.APK")
    apps, feeds = [], []
    for index, identity in enumerate(batch.IDS):
        app = {"id": identity, "name": identity, "packageName": "com.zte.mifavor.launcher",
               "signingCertificateSha256": batch.CERTIFICATE}
        apps.append(app)
        feeds.append({"schemaVersion": 1, "appId": identity, "packageName": app["packageName"], "release": {
            "versionCode": codes[index], "versionName": f"display{index}", "minSdk": 31,
            "publishedAt": "2026-10-14T00:00:00Z", "notes": batch.notes_for(*codes),
            "apk": {"url": batch.asset_url(tag, names[index]), "size": 1024 * (index + 1),
                    "sha256": ("a" if index == 0 else "b") * 64, "certificateSha256": batch.CERTIFICATE}}})
    assets = [{"name": name, "label": label, "apk": Path(f"unused{index}.apk"),
               "size": feeds[index]["release"]["apk"]["size"], "sha256": feeds[index]["release"]["apk"]["sha256"]}
              for name, label, index in zip(names, labels, (0, 1))]
    return {"apps": apps, "feeds": feeds, "assets": assets, "tag": tag, "notes": batch.notes_for(*codes)}


class FakeGitHub:
    """Model immutable Git trees and a fast-forward-only main reference."""
    def __init__(self, plan):
        self.plan = plan
        self.events = []
        self.counter = 100
        self.head = "1" * 40
        self.initial = {"README.md": b"original", "updates/unrelated.json": b"untouched"}
        for app in plan["apps"]:
            self.initial[f"updates/{app['id']}.json"] = json.dumps({
                "schemaVersion": 1, "appId": app["id"], "packageName": app["packageName"], "release": None}).encode()
        self.trees = {"2" * 40: deepcopy(self.initial)}
        self.commits = {self.head: {"tree": {"sha": "2" * 40}, "parents": []}}
        self.blobs = {}
        self.release = None
        self.assets = []
        self.fail_upload = None
        self.corrupt_upload = None
        self.fail_label = None
        self.label_result_fault = None
        self.race = None
        self.raced = False

    def identifier(self):
        self.counter += 1
        return f"{self.counter:040x}"

    def files(self):
        return self.trees[self.commits[self.head]["tree"]["sha"]]

    def advance(self, newer=False):
        files = deepcopy(self.files())
        files["README.md"] = b"concurrent preserved"
        if newer:
            feed = deepcopy(self.plan["feeds"][1])
            feed["release"]["versionCode"] += 1
            files[f"updates/{batch.IDS[1]}.json"] = json.dumps(feed).encode()
        tree, commit = self.identifier(), self.identifier()
        self.trees[tree] = files
        self.commits[commit] = {"tree": {"sha": tree}, "parents": [self.head]}
        self.head = commit

    def request(self, method, path, data=None, missing_ok=False):
        self.events.append((method, path, deepcopy(data)))
        if method == "GET" and "/git/ref/heads/main" in path:
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": self.head}}
        if method == "GET" and "/contents/" in path:
            file, sha = path.split("/contents/", 1)[1].split("?ref=")
            tree = self.trees[self.commits[sha]["tree"]["sha"]]
            return None if file not in tree else {"type": "file", "encoding": "base64",
                "content": base64.b64encode(tree[file]).decode()}
        if method == "GET" and "/releases/tags/" in path:
            return deepcopy(self.release)
        if method == "GET" and "/releases?" in path:
            return [deepcopy(self.release)] if self.release else []
        if method == "POST" and path.endswith("/releases"):
            self.release = {**data, "id": 7, "upload_url":
                f"https://uploads.github.com/repos/{batch.REPOSITORY}/releases/7/assets{{?name,label}}"}
            return deepcopy(self.release)
        if method == "GET" and "/releases/7/assets?" in path:
            return deepcopy(self.assets)
        if method == "GET" and path.endswith("/releases/7"):
            return deepcopy(self.release)
        if method == "PATCH" and "/releases/assets/" in path:
            asset = next(item for item in self.assets if str(item["id"]) == path.rsplit("/", 1)[-1])
            if asset["name"] == self.fail_label:
                raise batch.RemoteFailure(503)
            if not self.release["draft"] or data["name"] != asset["name"]:
                raise AssertionError("cannot mutate public asset or canonical name")
            asset.update(data)
            result = deepcopy(asset)
            if self.label_result_fault:
                result.update(self.label_result_fault)
            return result
        if method == "PATCH" and path.endswith("/releases/7"):
            self.release.update(data)
            self.release["published_at"] = "2026-10-14T08:00:00Z"
            return deepcopy(self.release)
        if method == "GET" and "/git/commits/" in path:
            return deepcopy(self.commits[path.rsplit("/", 1)[-1]])
        if method == "POST" and path.endswith("/git/blobs"):
            sha = self.identifier()
            self.blobs[sha] = base64.b64decode(data["content"])
            return {"sha": sha}
        if method == "POST" and path.endswith("/git/trees"):
            sha = self.identifier()
            files = deepcopy(self.trees[data["base_tree"]])
            for entry in data["tree"]:
                files[entry["path"]] = self.blobs[entry["sha"]]
            self.trees[sha] = files
            return {"sha": sha}
        if method == "POST" and path.endswith("/git/commits"):
            sha = self.identifier()
            self.commits[sha] = {"tree": {"sha": data["tree"]}, "parents": data["parents"]}
            return {"sha": sha}
        if method == "PATCH" and path.endswith("/git/refs/heads/main"):
            if data.get("force") is not False:
                raise AssertionError("force update")
            if self.race and not self.raced:
                self.raced = True
                if self.race in ("unrelated", "newer"):
                    self.advance(self.race == "newer")
                    raise batch.RemoteFailure(422)
                if self.race == "unknown_success":
                    self.head = data["sha"]
                    raise ConnectionResetError("unknown response with sensitive query")
            if self.commits[data["sha"]]["parents"] != [self.head]:
                raise batch.RemoteFailure(422)
            self.head = data["sha"]
            return {"object": {"sha": self.head}}
        raise AssertionError((method, path, data))

    def upload(self, url, apk, name):
        self.events.append(("UPLOAD", name, None))
        if name == self.fail_upload:
            raise ConnectionResetError("interrupted")
        expected = next(item for item in self.plan["assets"] if item["name"] == name)
        # Match the real platform's removal of Chinese filename characters.
        sanitized_name = "".join(character for character in name if character.isascii())
        asset = {"id": len(self.assets) + 10, "name": sanitized_name, "label": None, "state": "uploaded", "size": expected["size"],
                 "digest": "sha256:" + expected["sha256"], "browser_download_url": batch.asset_url(self.plan["tag"], name)}
        if name == self.corrupt_upload:
            asset["digest"] = "sha256:" + "c" * 64
        self.assets.append(asset)
        return deepcopy(asset)


class BatchPublicationTests(unittest.TestCase):
    def setUp(self):
        self.plan = fixture()
        self.api = FakeGitHub(self.plan)

    def assert_no_promotion(self):
        self.assertEqual(self.api.files(), self.api.initial)
        self.assertFalse(any(method == "PATCH" and (path.endswith("/releases/7") or
                            path.endswith("/git/refs/heads/main")) for method, path, _ in self.api.events))

    def triple(self):
        plan = fixture()
        plan["tag"] += "-12-270007"
        app = deepcopy(plan["apps"][0]);app["id"] = "stacked-desktop-redmagic12"
        feed = deepcopy(plan["feeds"][0]);feed["appId"] = app["id"]
        feed["release"]["versionCode"] = 270007
        feed["release"]["apk"]["url"] = batch.asset_url(plan["tag"], "12-270007.apk")
        plan["apps"].append(app);plan["feeds"].append(feed)
        asset = deepcopy(plan["assets"][0]);asset.update(name="12-270007.apk", label="堆叠桌面12-270007.apk")
        plan["assets"].append(asset)
        for original in plan["feeds"][:2]:
            name = original["release"]["apk"]["url"].rsplit("/",1)[-1]
            original["release"]["apk"]["url"] = batch.asset_url(plan["tag"], name)
        plan["notes"] = "10.x.x系统请安装堆叠桌面10-250001\n11.x.x系统请安装堆叠桌面11-260029\n12.x.x系统请安装堆叠桌面12-270007\n新增长按动作震动反馈"
        plan["title"] = "堆叠桌面 10 / 11 / 12"
        for original in plan["feeds"]:original["release"]["notes"] = plan["notes"]
        return plan, FakeGitHub(plan)

    def test_three_series_upload_snapshots_preserve_each_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);work = root / "work";work.mkdir()
            versions = {"10": {"versionCode": 250002, "versionName": "25.5"},
                        "11": {"versionCode": 260030, "versionName": "26.9"},
                        "12": {"versionCode": 270007, "versionName": "27.0"}}
            apps = [{"id": identity, "packageName": "com.zte.mifavor.launcher", "signingCertificateSha256": batch.CERTIFICATE}
                    for identity in (*batch.IDS, "stacked-desktop-redmagic12")]
            (root / "apps.json").write_text(json.dumps({"repository": batch.REPOSITORY, "apps": apps}), encoding="utf-8")
            values = {"sdk": None, "java_home": None}
            for series in (10, 11, 12):
                source = root / str(series);source.mkdir()
                apk = root / f"{series}.apk";apk.write_bytes(str(series).encode())
                values[f"launcher{series}_root"] = source;values[f"apk{series}"] = apk
            notes = root / "description.txt"
            notes.write_text("堆叠桌面10-250002\n堆叠桌面11-260030\n堆叠桌面12-270007", encoding="utf-8")
            values["notes_file"] = notes
            def tool(source, name):
                if name == "check-launcher-stack-only-apk.py":
                    return SimpleNamespace(expected_versions=lambda _: versions[source.name])
                return SimpleNamespace(parse_manifest=lambda _: None)
            def inspect(args, app):
                apk = Path(args.apk);series = apk.read_bytes().decode();version = versions[series]
                feed = {"schemaVersion": 1, "appId": app["id"], "packageName": app["packageName"], "release": {
                    **version, "minSdk": 31, "publishedAt": "2026-10-26T00:00:00Z", "notes": notes.read_text(encoding="utf-8"),
                    "apk": {"url": batch.asset_url(app["id"]+"-v"+str(version["versionCode"]), app["id"]+"-x.apk"),
                            "size": apk.stat().st_size, "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
                            "certificateSha256": batch.CERTIFICATE}}}
                return apk, feed, "unused", "unused"
            with patch.object(batch, "ROOT", root), patch.object(batch, "load_tool", tool), \
                 patch.object(batch, "verify_family"), patch.object(batch.publish, "inspect_apk", inspect):
                plan = batch.preflight(SimpleNamespace(**values), work)
            self.assertEqual([a["name"] for a in plan["assets"]],
                             ["11-260030.apk", "stacked-desktop-redmagic10-250002.apk", "12-270007.apk"])
            self.assertEqual(plan["assets"][1]["label"], "堆叠桌面10-250002.APK")
            for asset, expected in zip(plan["assets"], (b"11", b"10", b"12")):
                self.assertEqual(asset["apk"].read_bytes(), expected, "another series overwrote the upload snapshot")
                self.assertEqual(hashlib.sha256(asset["apk"].read_bytes()).hexdigest(), asset["sha256"])
            self.assertEqual(len({a["apk"] for a in plan["assets"]}), 3)
            self.assertEqual({f["release"]["apk"]["url"] for f in plan["feeds"]},
                             {batch.asset_url(plan["tag"], a["name"]) for a in plan["assets"]})

    def test_three_series_keep_compatibility_asset_and_promote_all_feeds_together(self):
        plan, api = self.triple()
        batch.promote_batch(api, plan)
        self.assertEqual(len(api.assets), 3)
        self.assertEqual(api.release["body"], plan["notes"])
        self.assertEqual(api.release["name"], plan["title"])
        writes = [data for method,path,data in api.events if method=="POST" and path.endswith("/git/trees")]
        self.assertEqual(len(writes), 1)
        self.assertEqual({entry["path"] for entry in writes[0]["tree"]}, {f"updates/{a['id']}.json" for a in plan["apps"]})
        for feed in plan["feeds"]:
            actual = json.loads(api.files()[f"updates/{feed['appId']}.json"])
            self.assertEqual(actual["release"]["apk"], feed["release"]["apk"])
        previous = api.head
        batch.promote_batch(api, plan)
        self.assertEqual(api.head, previous)
        self.assertEqual(sum(m=="UPLOAD" for m,_,_ in api.events), 3)
        api.events = []
        batch.promote_batch(api, plan)
        self.assertTrue(all(method == "GET" for method, _, _ in api.events))

    def test_failed_third_series_keeps_every_feed_and_retry_reuses_prior_assets(self):
        plan, api = self.triple();api.fail_upload = "12-270007.apk"
        with self.assertRaises(ConnectionResetError):batch.promote_batch(api, plan)
        self.assertTrue(api.release["draft"])
        self.assertEqual(api.files(), api.initial)
        api.fail_upload = None
        batch.promote_batch(api, plan)
        self.assertFalse(api.release["draft"])
        self.assertEqual(sum(m=="UPLOAD" and p=="11-260029.apk" for m,p,_ in api.events), 1)

    def test_two_confirmed_assets_then_one_commit_for_exactly_two_feeds(self):
        result = batch.promote_batch(self.api, self.plan)
        self.assertFalse(self.api.release["draft"])
        self.assertEqual(len(self.api.assets), 2)
        trees = [data for method, path, data in self.api.events if method == "POST" and path.endswith("/git/trees")]
        self.assertEqual(len(trees), 1)
        self.assertEqual({item["path"] for item in trees[0]["tree"]}, {f"updates/{identity}.json" for identity in batch.IDS})
        self.assertEqual(self.api.files()["updates/unrelated.json"], b"untouched")
        self.assertEqual(self.api.files()["README.md"], b"original")
        self.assertTrue(result["commit"]["changed"])
        for index, identity in enumerate(batch.IDS):
            written = json.loads(self.api.files()[f"updates/{identity}.json"])
            self.assertEqual(written["release"]["publishedAt"], self.api.release["published_at"])
            self.assertEqual(written["release"]["apk"]["url"], self.plan["feeds"][index]["release"]["apk"]["url"])

    def test_ascii_download_names_preserve_chinese_labels_and_notes(self):
        batch.promote_batch(self.api, self.plan)
        self.assertEqual({asset["name"]: asset["label"] for asset in self.api.assets},
                         {"11-260029.apk": "堆叠桌面11-260029.apk",
                          "stacked-desktop-redmagic10-250001.apk": "堆叠桌面10-250001.APK"})
        self.assertTrue(all(path.isascii() for method, path, _ in self.api.events if method == "UPLOAD"))
        self.assertEqual(self.api.release["body"], "红魔11系列安装堆叠桌面11-260029.apk\n"
                         "红魔10系列安装堆叠桌面10-250001.apk\n其它机型自测。")
        feed11 = json.loads(self.api.files()[f"updates/{batch.IDS[0]}.json"])
        self.assertTrue(feed11["release"]["apk"]["url"].endswith("/11-260029.apk"))

    def test_failed_label_write_keeps_draft_and_retry_reuses_uploaded_asset(self):
        self.api.fail_label = self.plan["assets"][0]["name"]
        with self.assertRaises(batch.RemoteFailure):
            batch.promote_batch(self.api, self.plan)
        self.assert_no_promotion()
        self.assertTrue(self.api.release["draft"])
        asset_id = self.api.assets[0]["id"]
        self.api.fail_label = None
        batch.promote_batch(self.api, self.plan)
        self.assertEqual(self.api.assets[0]["id"], asset_id)
        self.assertEqual(sum(method == "UPLOAD" and path == "11-260029.apk"
                             for method, path, _ in self.api.events), 1)

    def test_label_write_response_must_preserve_all_asset_identity_fields(self):
        for fault in ({"id": 999}, {"name": "other.apk"}, {"size": 99},
                      {"digest": "sha256:" + "f" * 64}, {"label": "other"}):
            with self.subTest(fault=fault):
                self.api = FakeGitHub(self.plan)
                self.api.label_result_fault = fault
                with self.assertRaises(ValueError):
                    batch.promote_batch(self.api, self.plan)
                self.assert_no_promotion()
                self.assertTrue(self.api.release["draft"])

    def test_public_asset_label_is_never_modified(self):
        batch.promote_batch(self.api, self.plan)
        self.api.assets[0]["label"] = "unexpected label"
        self.api.events = []
        with self.assertRaisesRegex(ValueError, "不修改已公开附件"):
            batch.promote_batch(self.api, self.plan)
        self.assertEqual(self.api.assets[0]["label"], "unexpected label")
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.events))

    def test_label_write_rechecks_draft_immediately_before_mutation(self):
        original_request = self.api.request
        def externally_published(method, path, data=None, missing_ok=False):
            if method == "GET" and path.endswith("/releases/7"):
                self.api.release["draft"] = False
                self.api.release["published_at"] = "2026-10-14T08:00:00Z"
            return original_request(method, path, data, missing_ok)
        with patch.object(self.api, "request", side_effect=externally_published):
            with self.assertRaisesRegex(ValueError, "已公开"):
                batch.promote_batch(self.api, self.plan)
        self.assert_no_promotion()

    def test_second_or_third_upload_failure_keeps_draft_and_feeds(self):
        self.plan, _ = self.triple()
        for index in (1, 2):
            with self.subTest(index=index):
                self.api = FakeGitHub(self.plan)
                self.api.fail_upload = self.plan["assets"][index]["name"]
                with self.assertRaises(ConnectionResetError):
                    batch.promote_batch(self.api, self.plan)
                self.assertTrue(self.api.release["draft"])
                self.assert_no_promotion()

    def test_retry_reuses_completed_draft_assets(self):
        self.plan, self.api = self.triple()
        self.api.fail_upload = self.plan["assets"][2]["name"]
        with self.assertRaises(ConnectionResetError):
            batch.promote_batch(self.api, self.plan)
        completed = self.api.assets[0]["id"], self.api.assets[1]["id"]
        self.api.fail_upload = None
        batch.promote_batch(self.api, self.plan)
        self.assertEqual(completed, (self.api.assets[0]["id"], self.api.assets[1]["id"]))
        self.assertEqual(sum(method == "POST" and path.endswith("/releases") for method, path, _ in self.api.events), 1)
        self.assertFalse(any(method == "DELETE" for method, _, _ in self.api.events))

    def test_corrupt_upload_cannot_be_published(self):
        self.api.corrupt_upload = self.plan["assets"][1]["name"]
        with self.assertRaisesRegex(ValueError, "哈希"):
            batch.promote_batch(self.api, self.plan)
        self.assert_no_promotion()

    def test_public_release_body_or_missing_assets_never_rewritten(self):
        for wrong_body in (False, True):
            with self.subTest(wrong_body=wrong_body):
                self.api = FakeGitHub(self.plan)
                batch.promote_batch(self.api, self.plan)
                if wrong_body:
                    self.api.release["body"] += "\nextra"
                else:
                    self.api.assets.pop()
                self.api.events = []
                with self.assertRaises(ValueError):
                    batch.promote_batch(self.api, self.plan)
                self.assertTrue(all(method == "GET" for method, _, _ in self.api.events))

    def test_starter_attachment_is_preserved_without_delete(self):
        self.api.fail_upload = self.plan["assets"][1]["name"]
        with self.assertRaises(ConnectionResetError):
            batch.promote_batch(self.api, self.plan)
        self.api.assets[0].update(state="starter", size=0)
        self.api.fail_upload = None
        with self.assertRaisesRegex(ValueError, "不自动删除"):
            batch.promote_batch(self.api, self.plan)
        self.assertFalse(any(method == "DELETE" for method, _, _ in self.api.events))
        self.assert_no_promotion()

    def test_unrelated_concurrent_commit_is_preserved(self):
        self.api.race = "unrelated"
        batch.promote_batch(self.api, self.plan)
        self.assertEqual(self.api.files()["README.md"], b"concurrent preserved")
        self.assertEqual(self.api.files()["updates/unrelated.json"], b"untouched")

    def test_newer_concurrent_feed_prevents_downgrade(self):
        self.api.race = "newer"
        with self.assertRaisesRegex(ValueError, "回退"):
            batch.promote_batch(self.api, self.plan)
        feed = json.loads(self.api.files()[f"updates/{batch.IDS[1]}.json"])
        self.assertEqual(feed["release"]["versionCode"], 250002)
        self.assertEqual(sum(method == "PATCH" and path.endswith("/git/refs/heads/main")
                             for method, path, _ in self.api.events), 1)

    def test_unknown_successful_ref_response_is_recovered_by_reading(self):
        self.api.race = "unknown_success"
        batch.promote_batch(self.api, self.plan)
        self.assertEqual(sum(method == "PATCH" and path.endswith("/git/refs/heads/main")
                             for method, path, _ in self.api.events), 1)

    def test_completed_batch_retry_is_read_only(self):
        batch.promote_batch(self.api, self.plan)
        self.api.events = []
        result = batch.promote_batch(self.api, self.plan)
        self.assertFalse(result["commit"]["changed"])
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.events))

    def test_same_version_url_migration_requires_same_hash_and_signature(self):
        for index, identity in enumerate(batch.IDS):
            feed = deepcopy(self.plan["feeds"][index])
            feed["release"]["apk"]["url"] = batch.asset_url("old-tag", "old.apk")
            self.api.files()[f"updates/{identity}.json"] = json.dumps(feed).encode()
        batch.promote_batch(self.api, self.plan)
        self.api = FakeGitHub(self.plan)
        feed = deepcopy(self.plan["feeds"][0])
        feed["release"]["apk"]["sha256"] = "f" * 64
        self.api.files()[f"updates/{batch.IDS[0]}.json"] = json.dumps(feed).encode()
        with self.assertRaisesRegex(ValueError, "versionCode"):
            batch.promote_batch(self.api, self.plan)
        self.assertTrue(all(method == "GET" for method, _, _ in self.api.events))

    def test_encoded_url_identity_and_wrong_paths(self):
        name = "堆叠桌面11-260029.apk"
        encoded = batch.asset_url(self.plan["tag"], name)
        self.assertTrue(batch.matching_asset_url(encoded, self.plan["tag"], name))
        self.assertTrue(batch.matching_asset_url(urllib_unquote(encoded), self.plan["tag"], name))
        for bad in (encoded.replace("liligit1815/lispacerelease", "liligit1815%2Flispacerelease"),
                    encoded.replace("/releases/download/", "%2Freleases%2Fdownload%2F"),
                    encoded.replace("github.com", "evil.example"), encoded + "?secret=1", encoded + "#x",
                    encoded.replace("%E5", "%25E5")):
            self.assertFalse(batch.matching_asset_url(bad, self.plan["tag"], name))

    def test_dry_run_never_reads_credentials_or_connects(self):
        with patch.object(batch, "preflight", return_value=self.plan), patch.object(batch.publish, "token") as token, \
                patch.object(batch, "SafeGitHub") as api, patch("builtins.print"):
            batch.main(["--apk11", "unused.apk", "--apk10", "unused.apk", "--launcher11-root", "root11",
                        "--launcher10-root", "root10", "--dry-run"])
            token.assert_not_called()
            api.assert_not_called()

    def test_get_retries_but_unknown_post_is_not_replayed(self):
        client = batch.SafeGitHub("secret-never-print")
        with patch.object(batch.publish.GitHub, "request", side_effect=[OSError("signed?secret=1"), {"ok": 1}]) as request, \
                patch.object(batch.time, "sleep"):
            self.assertEqual(client.request("GET", batch.BASE + "/git/ref/heads/main"), {"ok": 1})
            self.assertEqual(request.call_count, 2)
        with patch.object(batch.publish.GitHub, "request", side_effect=OSError("signed?secret=1")) as request:
            with self.assertRaises(batch.RemoteFailure) as error:
                client.request("POST", batch.BASE + "/releases", {})
            self.assertEqual(request.call_count, 1)
            self.assertNotIn("secret", str(error.exception))


def manifest_fixture(platform=35, minimum=31, metadata=True):
    def attribute(name, rid, value, dtype=0x10):
        return {"namespace": batch.ANDROID, "name": name, "resourceId": rid, "type": dtype, "value": value}
    def start(name, attrs=()):
        return {"event": "start", "namespace": None, "name": name, "attributes": list(attrs)}
    def end(name):
        return {"event": "end", "namespace": None, "name": name}
    result = [start("manifest", [attribute("compileSdkVersion", 0x01010572, platform)]),
        start("uses-sdk", [attribute("minSdkVersion", 0x0101020c, minimum),
                           attribute("targetSdkVersion", 0x01010270, platform)]), end("uses-sdk"), start("application")]
    if metadata:
        result.extend([start("meta-data", [attribute("name", 0x01010003, batch.SERIES_KEY, 3),
                                          attribute("value", 0x01010024, "redmagic10", 3)]), end("meta-data")])
    result.extend([end("application"), end("manifest")])
    return result


class FamilyIdentityTests(unittest.TestCase):
    def validate(self, events, *, series=10, minimum=31):
        feed = fixture()["feeds"][1 if series == 10 else 0]
        feed["release"]["minSdk"] = minimum
        versions = {key: feed["release"][key] for key in ("versionCode", "versionName")}
        gate = SimpleNamespace(expected_versions=lambda _: versions, strip_exact_update_nodes=lambda _: None)
        baseline = manifest_fixture(35 if series == 10 else 36, 29 if series == 10 else 31, metadata=False)
        with patch.object(batch, "manifest_events", side_effect=[(events, versions), (baseline, {})]), \
                patch.object(batch, "source_sdk", return_value=(31, 35 if series == 10 else 36)):
            batch.verify_family(Path("unused"), gate, Path("unused.apk"), feed, series)

    def test_oem_family_sdk_and_metadata_are_required(self):
        self.validate(manifest_fixture())
        self.validate(manifest_fixture(36, metadata=False), series=11)
        for events in (manifest_fixture(36), manifest_fixture(metadata=False)):
            with self.assertRaises(ValueError):
                self.validate(events)
        with self.assertRaisesRegex(ValueError, "红魔10系列标识"):
            self.validate(manifest_fixture(36), series=11)

    def test_typed_application_metadata_cannot_be_moved_or_forged(self):
        for key, value in (("namespace", None), ("resourceId", 0), ("type", 0x10), ("value", "redmagic11")):
            with self.subTest(key=key):
                events = manifest_fixture()
                events[4]["attributes"][1][key] = value
                with self.assertRaisesRegex(ValueError, "标识类型或值"):
                    self.validate(events)
        events = manifest_fixture()
        events[4:4] = [{"event": "start", "namespace": None, "name": "activity", "attributes": []}]
        events.insert(7, {"event": "end", "namespace": None, "name": "activity"})
        with self.assertRaisesRegex(ValueError, "application 直属"):
            self.validate(events)
        events = manifest_fixture()
        events[6:6] = deepcopy(events[4:6])
        with self.assertRaisesRegex(ValueError, "唯一"):
            self.validate(events)

    def test_source_minimum_and_major_are_never_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, "SDK 系列身份"):
            self.validate(manifest_fixture(minimum=29), minimum=29)
        events = manifest_fixture()
        events[0]["attributes"].append({"name": "versionCodeMajor", "value": 1})
        with self.assertRaisesRegex(ValueError, "versionCodeMajor"):
            self.validate(events)
        events = manifest_fixture()
        events[1]["attributes"][0]["resourceId"] = 0
        with self.assertRaisesRegex(ValueError, "SDK 身份"):
            self.validate(events)


def urllib_unquote(value):
    return batch.urllib.parse.unquote(value)


if __name__ == "__main__":
    unittest.main()
