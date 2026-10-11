import copy
import unittest
from protocol import validate_feed, ensure_forward_update

APP = {"id": "redmagic-duo", "packageName": "ls.augment.com", "signingCertificateSha256": "a" * 64}
FEED = {"schemaVersion": 1, "appId": "redmagic-duo", "packageName": "ls.augment.com", "release": {
    "versionCode": 100, "versionName": "1.0", "minSdk": 35, "notes": "说明", "publishedAt": "2026-10-11T00:00:00Z",
    "apk": {"url": "https://github.com/liligit1815/lispacerelease/releases/download/redmagic-duo-v100/redmagic-duo-100.apk",
            "size": 1024, "sha256": "b" * 64, "certificateSha256": "a" * 64}}}


class PublishingSafety(unittest.TestCase):
    def test_reject_cross_app_download_and_identity(self):
        for path, value in (("packageName", "other.app"), ("appId", "iqoo-shoulder")):
            feed = copy.deepcopy(FEED)
            feed[path] = value
            with self.assertRaises(ValueError):
                validate_feed(feed, APP)
        for url in ("http://github.com/liligit1815/lispacerelease/releases/download/v1/a.apk",
                    "https://github.com/other/repo/releases/download/v1/a.apk",
                    "https://github.com.evil.test/liligit1815/lispacerelease/releases/download/v1/a.apk",
                    "https://github.com/liligit1815/lispacerelease/releases/download/v1/a.apk?x=1"):
            feed = copy.deepcopy(FEED)
            feed["release"]["apk"]["url"] = url
            with self.assertRaises(ValueError):
                validate_feed(feed, APP)

    def test_reject_invalid_numbers_certificates_and_missing_release(self):
        for key, value in (("versionCode", True), ("versionCode", 0), ("minSdk", -1)):
            feed = copy.deepcopy(FEED)
            feed["release"][key] = value
            with self.assertRaises(ValueError):
                validate_feed(feed, APP)
        feed = copy.deepcopy(FEED)
        feed["release"]["apk"]["certificateSha256"] = "c" * 64
        with self.assertRaises(ValueError):
            validate_feed(feed, APP)
        with self.assertRaises(ValueError):
            validate_feed({"schemaVersion": 1, "appId": APP["id"], "packageName": APP["packageName"]}, APP)

    def test_no_downgrade_no_overwriting_same_code(self):
        feed = copy.deepcopy(FEED)
        feed["release"]["versionCode"] = 99
        with self.assertRaises(ValueError):
            ensure_forward_update(FEED, feed)
        feed["release"]["versionCode"] = 100
        feed["release"]["apk"]["sha256"] = "d" * 64
        with self.assertRaises(ValueError):
            ensure_forward_update(FEED, feed)
        ensure_forward_update(FEED, copy.deepcopy(FEED))
        feed["release"]["versionCode"] = 101
        ensure_forward_update(FEED, feed)

    def test_no_release_is_valid_and_can_be_promoted(self):
        empty = {"schemaVersion": 1, "appId": APP["id"], "packageName": APP["packageName"], "release": None}
        validate_feed(empty, APP)
        ensure_forward_update(empty, FEED)

    def test_publisher_matches_client_size_and_version_limits(self):
        feed = copy.deepcopy(FEED)
        feed["release"]["apk"]["size"] = 512 * 1024**2 + 1
        with self.assertRaises(ValueError):
            validate_feed(feed, APP)
        feed = copy.deepcopy(FEED)
        feed["release"]["versionName"] = "x" * 101
        with self.assertRaises(ValueError):
            validate_feed(feed, APP)


if __name__ == "__main__":
    unittest.main()
