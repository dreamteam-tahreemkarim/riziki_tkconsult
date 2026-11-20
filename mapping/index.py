"""
dashboard/data/index.json: the one small file the dashboard reads first.

It is rebuilt from the scan and height files, so two jobs finishing at the same time
can never leave it wrong: whichever commits second simply rebuilds it again.
Freshness fields (checked, latest_season, next_update, newest_image) are kept.

    python -m mapping.index rebuild
"""

from __future__ import annotations

import glob
import json
import os
import sys

from .config import load_config, repo_path
from .imagery import latest_season, next_update


def rebuild(cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    out = repo_path(cfg, cfg["output_dir"])
    path = os.path.join(out, "index.json")
    old = json.load(open(path)) if os.path.exists(path) else {}
    frames: dict = {}
    for f in sorted(glob.glob(os.path.join(out, "scans", "*", "[0-9][0-9][0-9][0-9].json"))):   # season scans only
        try:
            sc = json.load(open(f))
        except (OSError, json.JSONDecodeError):
            continue
        if sc.get("demo") or sc.get("failed"):        # failed attempts only answer their request
            continue
        fr = frames.setdefault(sc["frame"], {"scans": []})
        fr["scans"].append(sc["season"])
    # Frames with a tree-height estimate: the newest photo date behind it.
    for f in sorted(glob.glob(os.path.join(out, "scans", "*", "height.json"))):
        try:
            h = json.load(open(f))
            frames.setdefault(h["frame"], {"scans": []})["height"] = max(h.get("image_dates") or {"?": 0})
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    for fid, fr in frames.items():
        fr["scans"] = sorted(set(fr["scans"]), reverse=True)
        if not fr["scans"]:                           # height only, no Prosopis scan yet
            fr["status"] = "none"
            continue
        sc = json.load(open(os.path.join(out, "scans", fid, f"{fr['scans'][0]}.json")))
        fr["status"] = sc.get("status", "cannot")
        fr["images_to"] = sc.get("images", {}).get("to")
        seen = [d for d in (old.get("frames", {}).get(fid, {}).get("newest_image"), fr["images_to"]) if d]
        fr["newest_image"] = max(seen) if seen else None
    index = {
        "demo": False,
        "checked": old.get("checked"),
        "latest_season": latest_season(cfg),
        "next_update": next_update(cfg),
        "frames": frames,
    }
    os.makedirs(out, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(index, fh, indent=1)
    return index


if __name__ == "__main__":
    if sys.argv[1:2] == ["rebuild"]:
        idx = rebuild()
        print(f"index: {len(idx['frames'])} frames")
    else:
        print(__doc__)
