"""Keep the same-package launcher families and public asset names distinct."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import publish


class RedMagic12Publication(unittest.TestCase):
    def setUp(self):
        registry = json.loads((Path(__file__).resolve().parents[1] / "apps.json").read_text(encoding="utf-8"))
        self.app = next(app for app in registry["apps"] if app["id"] == "stacked-desktop-redmagic12")

    def inspect(self, target=37, app=None):
        app = app or self.app
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "release.apk").write_bytes(b"apk fixture")
            (root / "notes.txt").write_text("新增红魔12桌面支持，请下载12-270001版本。", encoding="utf-8")
            args = SimpleNamespace(apk=root / "release.apk", notes_file=root / "notes.txt")
            badging = ("package: name='com.zte.mifavor.launcher' versionCode='270001' "
                       "versionName='27.0.000.000.2609231706'\nsdkVersion:'31'\n"
                       f"targetSdkVersion:'{target}'\n")
            signature = "Signer #1 certificate SHA-256 digest: " + self.app["signingCertificateSha256"]
            with mock.patch.object(publish, "build_tools", return_value=("aapt", "java", "signer")), \
                    mock.patch.object(publish, "run", side_effect=[badging, signature]):
                return publish.inspect_apk(args, app)

    def test_12_channel_uses_downloadable_12_name(self):
        _, feed, tag, asset = self.inspect()
        self.assertEqual(asset, "12-270001.apk")
        self.assertEqual(tag, "stacked-desktop-redmagic12-v270001")
        self.assertEqual(feed["appId"], "stacked-desktop-redmagic12")
        self.assertTrue(feed["release"]["apk"]["url"].endswith("/" + tag + "/" + asset))

    def test_10_and_11_platforms_cannot_enter_12_channel(self):
        for target in (35, 36):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "桌面系列"):
                self.inspect(target)

    def test_existing_app_filename_default_is_preserved(self):
        app = copy.deepcopy(self.app)
        app.pop("assetPrefix")
        self.assertEqual(self.inspect(app=app)[3], "stacked-desktop-redmagic12-270001.apk")

    def test_invalid_asset_prefix_is_rejected(self):
        app = {**self.app, "assetPrefix": "../12"}
        with self.assertRaisesRegex(ValueError, "附件名称"):
            self.inspect(app=app)


if __name__ == "__main__":
    unittest.main()
