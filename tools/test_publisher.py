"""Publication must never point clients at an incomplete or corrupted upload."""
import base64
import copy
import json
from pathlib import Path
import unittest
from publish import promote
from test_protocol import APP as PROTOCOL_APP, FEED

APP = {**PROTOCOL_APP, "name": "红魔 Duo"}


class FailedUpload:
    def __init__(self, corrupt=False, published=False):
        self.corrupt = corrupt
        self.published = published
        self.events = []

    def request(self, method, path, data=None, missing_ok=False):
        self.events.append((method, path))
        if method == "GET" and "/contents/" in path:
            empty = {"schemaVersion": 1, "appId": APP["id"], "packageName": APP["packageName"], "release": None}
            return {"sha": "original-sha", "content": base64.b64encode(json.dumps(empty).encode()).decode()}
        if method == "GET" and "/releases/tags/" in path:
            if self.published:
                return {"id": 1, "draft": False, "assets": [], "upload_url": "https://uploads.github.com/unused"}
            return None
        if method == "POST" and path.endswith("/releases"):
            return {"id": 1, "draft": True, "assets": [], "upload_url": "https://uploads.github.com/unused"}
        raise AssertionError("Unexpected publication mutation: " + method + " " + path)

    def upload(self, url, apk, asset_name):
        self.events.append(("UPLOAD", url))
        if not self.corrupt:
            raise ConnectionResetError("Simulated network interruption")
        return {"state": "uploaded", "size": FEED["release"]["apk"]["size"],
                "digest": "sha256:" + "c" * 64, "browser_download_url": FEED["release"]["apk"]["url"]}


class PublicationFailureSafety(unittest.TestCase):
    def test_interrupted_upload_keeps_existing_manifest(self):
        api = FailedUpload()
        with self.assertRaises(ConnectionResetError):
            promote(api, APP, Path("unused.apk"), copy.deepcopy(FEED), "redmagic-duo-v100", "redmagic-duo-100.apk")
        self.assertFalse(any(method in ("PUT", "PATCH") for method, _ in api.events))

    def test_corrupted_upload_is_not_promoted_or_published(self):
        api = FailedUpload(corrupt=True)
        with self.assertRaisesRegex(ValueError, "附件校验失败"):
            promote(api, APP, Path("unused.apk"), copy.deepcopy(FEED), "redmagic-duo-v100", "redmagic-duo-100.apk")
        self.assertFalse(any(method in ("PUT", "PATCH") for method, _ in api.events))

    def test_existing_public_release_is_not_silently_rewritten(self):
        api = FailedUpload(published=True)
        with self.assertRaisesRegex(ValueError, "不自动修改历史发布"):
            promote(api, APP, Path("unused.apk"), copy.deepcopy(FEED), "redmagic-duo-v100", "redmagic-duo-100.apk")
        self.assertFalse(any(method == "UPLOAD" for method, _ in api.events))


if __name__ == "__main__":
    unittest.main()
