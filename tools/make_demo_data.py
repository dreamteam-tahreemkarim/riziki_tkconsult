"""
Writes DEMO scan files for the dashboard, in exactly the format the real
scan.py will write. Every file is marked "demo": true and the dashboard says
so in a banner. Nothing here is a real map.

    python tools/make_demo_data.py [out_dir]   default: dashboard/data_demo/
    (Point DATA_URL in dashboard/config.js at "data_demo/" to look at it.)

THE FILES (this is the contract between the scanner and the dashboard)

    data/index.json                       one small file the dashboard reads first
    data/scans/<frame>/<season>.json      one per scan

FRAMES
    A fixed 0.1 degree grid (about 11 x 11 km in Kenya). A frame is named by
    its south-west corner in hundredths of a degree: N0040_E03600 covers
    0.40-0.50 N, 36.00-36.10 E. The dashboard computes the same names from
    coordinates, so there is no frame file to download.

GRID INSIDE A FRAME
    n x n cells, row by row from the NORTH-WEST corner, one character each:
      0 no Prosopis   1 thin   2 medium   3 thick   9 can't judge (model unsure or no image)
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
from datetime import datetime, timezone

FRAME_DEG = 0.1
N = 100                                   # cells per side: ~111 m, ~1.2 ha each
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "dashboard", "data_demo")


def frame_id(lat: float, lon: float) -> str:
    """Name of the frame containing a point. Same formula as app.js frameId()."""
    per = round(1 / FRAME_DEG)                       # 10 frames per degree
    s = math.floor(round(lat * per, 9)) / per        # round first: 0.3 * 10 is 2.9999...
    w = math.floor(round(lon * per, 9)) / per
    return (f"{'N' if s >= 0 else 'S'}{abs(round(s * 100)):04d}_"
            f"{'E' if w >= 0 else 'W'}{abs(round(w * 100)):05d}")


def frame_bounds(fid: str) -> tuple[float, float, float, float]:
    """(south, west, north, east) of a frame."""
    a, b = fid.split("_")
    s = (1 if a[0] == "N" else -1) * int(a[1:]) / 100
    w = (1 if b[0] == "E" else -1) * int(b[1:]) / 100
    return s, w, s + FRAME_DEG, w + FRAME_DEG


def cell_ha(fid: str) -> float:
    s, w, n, e = frame_bounds(fid)
    lat = math.radians((s + n) / 2)
    dx = 111.32 * math.cos(lat) * FRAME_DEG / N          # km
    dy = 110.57 * FRAME_DEG / N                          # km
    return dx * dy * 100                                 # km2 -> ha


def pattern(seed: int, cleared=None, spread=0.0, unsure_corner=0.05):
    """A made-up density grid: thicket along a winding river, thinning outward."""
    rnd = random.Random(seed)
    blobs = [(rnd.uniform(10, 90), rnd.uniform(10, 90), rnd.uniform(6, 14)) for _ in range(5)]
    codes = []
    for r in range(N):
        river_c = 50 + 18 * math.sin(r / 13 + seed)
        for c in range(N):
            d = abs(c - river_c)
            score = math.exp(-d / (9 + 6 * spread))
            for (br, bc, rad) in blobs:
                score += 0.8 * math.exp(-((r - br) ** 2 + (c - bc) ** 2) / (2 * rad ** 2))
            score += rnd.uniform(-0.12, 0.12) + spread * 0.15
            if cleared and cleared[0] <= r < cleared[1] and cleared[2] <= c < cleared[3]:
                score = 0.05
            if r > N * (1 - unsure_corner * 4) and c > N * (1 - unsure_corner * 4):
                codes.append("9")
            elif score > 0.95:
                codes.append("3")
            elif score > 0.6:
                codes.append("2")
            elif score > 0.3:
                codes.append("1")
            else:
                codes.append("0")
    return "".join(codes)


def scan(fid, season, codes, status, reasons, applicability, images, plots_kind):
    ha = cell_ha(fid)
    counts = {k: codes.count(k) for k in "01239"}
    dens = {"thick": counts["3"] * ha, "medium": counts["2"] * ha, "thin": counts["1"] * ha}
    total = sum(dens.values())
    rnd = random.Random(fid + str(season))
    s, w, n, e = frame_bounds(fid)
    plots = []
    per_class = {"plot": {"3": 4, "2": 4, "1": 3}, "check": {"3": 8, "2": 8, "1": 7, "0": 7}}[plots_kind]
    for code, k in per_class.items():
        cells = [i for i, ch in enumerate(codes) if ch == code]
        for i in rnd.sample(cells, min(k, len(cells))):
            r, c = divmod(i, N)
            plots.append({
                "id": f"{'P' if plots_kind == 'plot' else 'C'}{len(plots) + 1:02d}",
                "lat": round(n - (r + 0.5) * FRAME_DEG / N, 5),
                "lon": round(w + (c + 0.5) * FRAME_DEG / N, 5),
                "class": {"3": "thick", "2": "medium", "1": "thin", "0": "none"}[code],
                "kind": plots_kind,
            })
    return {
        "demo": True,
        "frame": fid,
        "season": season,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "images": images,
        "model": {"id": "prosopis_s2_v1", "trained_on": "Baringo, Eschen et al. 2021 labels (2016)"},
        "applicability": applicability,
        "status": status,
        "status_reasons": reasons,
        "cell_ha": round(ha, 4),
        "area_ha": {"prosopis": round(total), "low": round(total * 0.86), "high": round(total * 1.14)},
        "density_ha": {k: round(v) for k, v in dens.items()},
        "grid": {"n": N, "codes": codes},
        "plots": plots,
    }


def main():
    idx = os.path.join(OUT, "index.json")
    if os.path.exists(idx) and not json.load(open(idx)).get("demo", False):
        raise SystemExit(f"{OUT} holds real scans; refusing to overwrite. Pick another folder.")
    os.makedirs(os.path.join(OUT, "scans"), exist_ok=True)
    s2_2026 = {"sensor": "Sentinel-2 L2A", "dry": ["2026-01-06", "2026-03-24"],
               "wet": ["2026-04-11", "2026-08-28"], "to": "2026-08-28", "n_scenes": 12}
    s2_2016 = {"sensor": "Sentinel-2 L2A", "dry": ["2016-01-08", "2016-02-17"],
               "wet": ["2016-06-06", "2016-08-25"], "to": "2016-08-25", "n_scenes": 9}
    scans = [
        scan("N0040_E03590", 2026, pattern(1, spread=0.4), "scout",
             ["This region has not been checked on the ground yet."], 0.95, s2_2026, "check"),
        scan("N0040_E03600", 2026, pattern(2, spread=0.5, cleared=(30, 52, 20, 48)), "plan",
             [], 0.97, s2_2026, "plot"),
        scan("N0040_E03600", 2016, pattern(2, spread=0.0), "plan", [], 0.97, s2_2016, "plot"),
        scan("N0040_E03610", 2026, pattern(3, unsure_corner=0.2), "cannot",
             ["Only 61% of this area looks like land the model was trained on."], 0.61, s2_2026, "check"),
    ]
    frames = {}
    for sc in scans:
        d = os.path.join(OUT, "scans", sc["frame"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{sc['season']}.json"), "w") as f:
            json.dump(sc, f, separators=(",", ":"))
        fr = frames.setdefault(sc["frame"], {"scans": []})
        fr["scans"].append(sc["season"])
    for fid, fr in frames.items():
        latest = max(fr["scans"])
        sc = next(s for s in scans if s["frame"] == fid and s["season"] == latest)
        fr.update(scans=sorted(fr["scans"], reverse=True), status=sc["status"],
                  images_to=sc["images"]["to"], newest_image=sc["images"]["to"])
    frames["N0040_E03590"]["newest_image"] = "2026-08-30"     # one frame has a newer image
    index = {
        "demo": True,
        "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_season": 2026,
        "next_update": "2027-09",
        "frames": frames,
    }
    with open(os.path.join(OUT, "index.json"), "w") as f:
        json.dump(index, f, indent=1)
    print("wrote", len(scans), "scans for", len(frames), "frames to", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
