"""
Two seasons of the same frame, cell by cell. Same logic as compareScans() in
dashboard/app.js, for reports and the Rising Tigers write-up.

    cleared   was Prosopis, now not (de-stumping shows up here)
    spread    was not, now Prosopis
    still     Prosopis both times
    unknown   either season can't judge that cell

    python -m mapping.change N0040_E03600 2016 2026
"""

from __future__ import annotations

import json
import os
import sys

PRESENT = set("123")


def compare_scans(a: dict, b: dict) -> dict:
    older, newer = sorted((a, b), key=lambda s: s["season"])
    A, B = older["grid"]["codes"], newer["grid"]["codes"]
    if len(A) != len(B):
        raise ValueError("the two scans use different grids")
    tally = {"cleared": 0, "spread": 0, "still": 0, "unknown": 0}
    for x, y in zip(A, B):
        if x == "9" or y == "9":
            tally["unknown"] += 1
        elif x in PRESENT and y not in PRESENT:
            tally["cleared"] += 1
        elif x not in PRESENT and y in PRESENT:
            tally["spread"] += 1
        elif x in PRESENT:
            tally["still"] += 1
    ha = newer.get("cell_ha") or older.get("cell_ha")
    return {"frame": newer["frame"], "older": older["season"], "newer": newer["season"],
            "ha": {k: round(v * ha) for k, v in tally.items()}}


if __name__ == "__main__":
    from .config import load_config, repo_path
    cfg = load_config()
    fid, y1, y2 = sys.argv[1], sys.argv[2], sys.argv[3]
    d = os.path.join(repo_path(cfg, cfg["output_dir"]), "scans", fid)
    print(compare_scans(json.load(open(os.path.join(d, f"{y1}.json"))), json.load(open(os.path.join(d, f"{y2}.json")))))
