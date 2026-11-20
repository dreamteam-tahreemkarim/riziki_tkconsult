"""
Where crews should go, chosen at random within each density class.

    PLAN CREWS     plots: plots_per_class in each of thick / medium / thin.
                   Feeds stratify.py (at least two per class, three to five ideal).
    otherwise      check points: checks_per_class in each map class, INCLUDING
                   "none". Random, not convenient: that is what makes them an
                   honest test of the map, and later training labels.

Random within class is the standard design (stratified random sampling): it
is what lets stratify.estimate_total() and summarize.area_estimate() give
honest error bars.
"""

from __future__ import annotations

import random

CLASS_NAME = {"3": "thick", "2": "medium", "1": "thin", "0": "none"}


def plot_locations(codes: str, n: int, bounds, status: str, cfg: dict, seed_text: str = "") -> list[dict]:
    s, w, north, e = bounds
    step_lat, step_lon = (north - s) / n, (e - w) / n
    fp = cfg["field_plan"]
    if status == "plan":
        kind, per_class = "plot", {c: fp["plots_per_class"] for c in "321"}
    else:
        kind, per_class = "check", {c: fp["checks_per_class"] for c in "3210"}
    rnd = random.Random(f"{fp['seed']}:{seed_text}")
    out = []
    for code, k in per_class.items():
        cells = [i for i, ch in enumerate(codes) if ch == code]
        for i in sorted(rnd.sample(cells, min(k, len(cells)))):
            r, c = divmod(i, n)
            out.append({
                "id": f"{'P' if kind == 'plot' else 'C'}{len(out) + 1:02d}",
                "lat": round(north - (r + 0.5) * step_lat, 5),
                "lon": round(w + (c + 0.5) * step_lon, 5),
                "class": CLASS_NAME[code], "kind": kind,
            })
    return out
