"""
How well do the maps match what the scouts found on the ground?

    python -m mapping.field_check field_sheets/*.csv [--season 2026]
        -> prints a table and a score; writes dashboard/data/field_check.json

For every plot with GPS: the frame and cell it falls in, what the Prosopis scan
said there (none / thin / medium / thick), and what the height map said. Then:

    presence   of plots where scouts found Prosopis, the share the map caught
               (recall); of plots the map called Prosopis, the share that had it
               (precision)
    class      share of plots where the map's density class matched the scouts'
    height     typical gap between tape-measured height and the height map, m;
               plots measured by eye (height_method = eye) are left out

These are the Kenyan numbers to quote once there are enough plots (aim for 30+ per
class, chosen by the dashboard's check points, not by convenience). Until then the
dashboard shows the worldwide or Baringo test figures.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

from .config import load_config, repo_path
from .tiles import frame_bounds, frame_id

CLASS = {"0": "none", "1": "thin", "2": "medium", "3": "thick", "9": "unknown"}


def _field_data():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import field_data
    return field_data


def map_values(rec: dict, scans_dir: Path, cfg: dict, season: int | None) -> dict:
    """What the scan and the height map say at one plot's position."""
    g = cfg["grid"]
    frame = frame_id(rec["lat"], rec["lon"], g["frame_deg"])
    s, w, n, e = frame_bounds(frame, g["frame_deg"])
    step = g["frame_deg"] / g["cells"]
    r, c = math.floor((n - rec["lat"]) / step), math.floor((rec["lon"] - w) / step)
    i = r * g["cells"] + c
    out = {"frame": frame, "cell": i, "map_class": None, "season": None, "map_tree_m": None, "map_top_m": None}
    d = scans_dir / frame
    seasons = sorted((int(p.stem) for p in d.glob("[0-9][0-9][0-9][0-9].json")), reverse=True) if d.exists() else []
    if season in seasons:
        seasons = [season]
    if seasons:
        sc = json.loads((d / f"{seasons[0]}.json").read_text())
        if not sc.get("failed") and sc.get("grid", {}).get("codes"):
            out["map_class"], out["season"] = CLASS.get(sc["grid"]["codes"][i]), seasons[0]
    if (d / "height.json").exists():
        h = json.loads((d / "height.json").read_text())["grid"]
        out["map_tree_m"], out["map_top_m"] = h["tree_m"][i], h["top_m"][i]
    return out


def score(rows: list[dict]) -> dict:
    """Presence, class and height agreement over the plots that can be compared."""
    pres = [r for r in rows if r["prosopis"] is not None and r["map_class"] not in (None, "unknown")]
    tp = sum(r["prosopis"] and r["map_class"] != "none" for r in pres)
    fp = sum(not r["prosopis"] and r["map_class"] != "none" for r in pres)
    fn = sum(r["prosopis"] and r["map_class"] == "none" for r in pres)
    cls = [r for r in rows if r["density_class"] and r["map_class"] not in (None, "unknown")]
    hgt = [r for r in rows if r["height_median_m"] and r["map_tree_m"] is not None and r["height_method"] != "eye"]
    gaps = sorted(r["map_tree_m"] - r["height_median_m"] for r in hgt)
    div = lambda a, b: round(a / b, 3) if b else None
    return {
        "plots": len(rows),
        "presence": {"n": len(pres), "recall": div(tp, tp + fn), "precision": div(tp, tp + fp)},
        "class": {"n": len(cls), "agreement": div(sum(r["density_class"] == r["map_class"] for r in cls), len(cls))},
        "height": {"n": len(hgt),
                   "mean_abs_gap_m": round(sum(abs(x) for x in gaps) / len(gaps), 2) if gaps else None,
                   "median_gap_m": round(gaps[len(gaps) // 2], 2) if gaps else None,
                   "note": "map minus field; positive = map says taller"},
    }


def run(sheets: list[str], cfg: dict, season: int | None = None) -> dict:
    fd = _field_data()
    scans_dir = Path(repo_path(cfg, cfg["output_dir"])) / "scans"
    rows = []
    for path in sheets:
        sheet = fd.load_field_sheet(path)
        checks = fd.validate(sheet)
        if not checks.ok:
            raise ValueError(f"{path} has errors; fix them first:\n{checks}")
        for rec in fd.to_checks(sheet):
            rows.append({**rec, "sheet": os.path.basename(path), **map_values(rec, scans_dir, cfg, season)})
    out = {"score": score(rows), "plots": rows}
    path = Path(repo_path(cfg, cfg["output_dir"])) / "field_check.json"
    path.write_text(json.dumps(out, indent=1))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare field plots with the Prosopis and height maps.")
    ap.add_argument("sheets", nargs="+")
    ap.add_argument("--season", type=int, default=None, help="scan season to compare with (default: newest)")
    a = ap.parse_args(argv)
    out = run(a.sheets, load_config(), a.season)
    print(f"{'plot':10} {'field':12} {'map':10} {'field h':>8} {'map h':>6}")
    for r in out["plots"]:
        field = "none" if r["prosopis"] is False else (r["density_class"] or ("yes" if r["prosopis"] else "?"))
        fh = "" if r["height_median_m"] is None else f"{r['height_median_m']:g}"
        mh = "" if r["map_tree_m"] is None else f"{r['map_tree_m']}"
        print(f"{r['plot_id']:10} {field:12} {str(r['map_class']):10} {fh:>8} {mh:>6}")
    print(json.dumps(out["score"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
