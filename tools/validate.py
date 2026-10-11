"""Check all update manifests without GitHub credentials or network access."""
import json
from pathlib import Path
from protocol import validate_feed, require

root = Path(__file__).resolve().parents[1]
registry = json.loads((root / "apps.json").read_text(encoding="utf-8"))
require(registry["repository"] == "liligit1815/lispacerelease", "仓库不匹配")
ids = [app["id"] for app in registry["apps"]]
require(len(ids) == len(set(ids)), "软件标识重复")
for app in registry["apps"]:
    path = root / "updates" / (app["id"] + ".json")
    validate_feed(json.loads(path.read_text(encoding="utf-8")), app)
    print(f"OK {app['id']}")
require({path.stem for path in (root / "updates").glob("*.json")} == set(ids), "清单与软件目录不一致")
