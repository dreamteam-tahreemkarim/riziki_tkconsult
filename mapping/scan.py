"""
One frame, one season: everything from satellite to the file the dashboard reads.

    python -m mapping.scan --frame N0040_E03600 --season 2026
    python -m mapping.scan --frame N0040_E03600 --season 2026 --request-id <id> --tif

Steps (each is its own module):
    features.feature_stack()          images -> 15 features per ~22 m pixel
    model                             Prosopis vote share per pixel
    applicability                     which pixels are on familiar ground
    summarize                         cells, density classes, area with a range
    gate.deploy_status()              PLAN CREWS / SCOUT FIRST / CAN'T JUDGE HERE
    field_plan.plot_locations()       where crews or scouts should go
    writes dashboard/data/scans/<frame>/<season>.json and rebuilds index.json

IF ANYTHING FAILS, a result is still written: status "cannot" with the reason,
and the request id. The dashboard is waiting on that file, and "Can't judge
here: no usable images" is better than waiting forever.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone

import joblib
import numpy as np

from .applicability import Applicability
from .config import load_config, repo_path
from .features import feature_stack
from .field_plan import plot_locations
from .gate import deploy_status
from .imagery import latest_season
from .index import rebuild
from .summarize import area_estimate, cells_from_pixels, density_codes, density_ha
from .tiles import cell_ha, frame_bounds, in_region


def load_model(cfg: dict) -> dict:
    path = os.path.join(repo_path(cfg, cfg["models_dir"]), f"{cfg['model_id']}.joblib")
    if not os.path.exists(path):
        raise FileNotFoundError(f"model {path} not found; run python -m mapping.train first")
    return joblib.load(path)


def check_request(frame: str, season: int, cfg: dict) -> None:
    frame_bounds(frame)                                    # raises on a malformed name
    if not in_region(frame, cfg["region"], cfg["grid"]["frame_deg"]):
        raise ValueError(f"{frame} is outside the region this map covers")
    first, latest = cfg["seasons"]["first"], latest_season(cfg)
    if not first <= season <= latest:
        raise ValueError(f"season {season} can't be scanned yet or has no images; "
                         f"seasons {first}-{latest} can")


def run_scan(frame: str, season: int, cfg: dict, request_id: str | None = None,
             write_tif: bool = False) -> dict:
    check_request(frame, season, cfg)
    bundle = load_model(cfg)
    s, w, n, e = frame_bounds(frame, cfg["grid"]["frame_deg"])
    stack = feature_stack((w, s, e, n), season, cfg)
    if stack["names"] != bundle["features"]:
        raise ValueError("this model was trained on different features than the config lists")

    arr = stack["array"]
    f, rows, cols = arr.shape
    X = arr.reshape(f, -1).T
    clear = np.isfinite(X).all(axis=1)

    rf = bundle["model"]
    proba = np.full(len(X), np.nan, dtype="float32")
    if clear.any():
        proba[clear] = rf.predict_proba(X[clear])[:, list(rf.classes_).index(1)]
    aoa = Applicability.from_dict(bundle["aoa"])
    familiar = np.zeros(len(X), dtype=bool)
    if clear.any():
        familiar[clear] = aoa.inside(X[clear])

    known = (clear & familiar).reshape(rows, cols)
    presence = (proba >= bundle["threshold"]).reshape(rows, cols) & known
    cells = cfg["grid"]["cells"]
    cover, unknown = cells_from_pixels(presence, known, cells)
    codes = density_codes(cover, unknown, cfg)

    lat_mid = (s + n) / 2
    c_ha = cell_ha(lat_mid, cfg["grid"]["frame_deg"], cells)
    px_ha = c_ha * cells * cells / (rows * cols)
    valid_share = float(clear.mean())
    applicability = float(familiar[clear].mean()) if clear.any() else 0.0
    area = area_estimate(presence.sum() * px_ha, bundle.get("area_error", {}))

    latest = latest_season(cfg)
    status, reasons = deploy_status(frame, season, applicability, valid_share, latest, cfg)
    info = stack["info"]
    dates = info["dry"]["dates"] + info["wet"]["dates"]
    scan = {
        "demo": False,
        "request_id": request_id,
        "frame": frame,
        "season": season,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "images": {
            "sensor": info["sensor"],
            "dry": [info["dry"]["dates"][0], info["dry"]["dates"][-1]],
            "wet": [info["wet"]["dates"][0], info["wet"]["dates"][-1]],
            "to": max(dates), "n_scenes": len(dates),
            "ids": info["dry"]["ids"] + info["wet"]["ids"],
        },
        "model": {"id": cfg["model_id"], "trained_on": bundle["card"]["trained_on"],
                  "spatial_cv_accuracy": round(bundle["card"]["accuracy_spatial_cv"]["overall_accuracy"], 3)},
        "applicability": round(applicability, 3),
        "valid_share": round(valid_share, 3),
        "status": status,
        "status_reasons": reasons,
        "cell_ha": round(c_ha, 4),
        "area_ha": area,
        "density_ha": density_ha(codes, c_ha),
        "grid": {"n": cells, "codes": codes},
        "plots": plot_locations(codes, cells, (s, w, n, e), status, cfg, f"{frame}:{season}"),
    }
    out_dir = os.path.join(repo_path(cfg, cfg["output_dir"]), "scans", frame)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{season}.json"), "w") as fh:
        json.dump(scan, fh, separators=(",", ":"))
    if write_tif:
        write_cover_tif(os.path.join(out_dir, f"{season}_prosopis.tif"), proba, known.ravel(), stack["gbox"], rows, cols)
    rebuild(cfg)
    return scan


def write_cover_tif(path, proba, known, gbox, rows, cols):
    """Prosopis vote share 0-100 per pixel, 255 = unknown. EPSG:4326, opens in QGIS."""
    import rasterio
    band = np.where(known, np.round(np.nan_to_num(proba) * 100), 255).astype("uint8").reshape(rows, cols)
    with rasterio.open(path, "w", driver="GTiff", height=rows, width=cols, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=gbox.affine, nodata=255, compress="deflate") as dst:
        dst.write(band, 1)


def write_failure(frame: str, season, request_id: str | None, message: str, cfg: dict) -> None:
    """A result that says what went wrong, so the dashboard isn't left waiting."""
    try:
        frame_bounds(frame)
        season = int(season)
    except (ValueError, TypeError):
        return                                             # nothing sensible to write
    out_dir = os.path.join(repo_path(cfg, cfg["output_dir"]), "scans", frame)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{season}.json")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = None
    if os.path.exists(path):
        try:
            old = json.load(open(path))
        except (OSError, json.JSONDecodeError):
            old = None
    if old and old.get("grid"):
        # Keep the last good scan, answer this request with it, and say the re-scan failed.
        old.update(request_id=request_id, refresh_failed={"at": now, "message": message})
        result = old
    else:
        result = {"demo": False, "request_id": request_id, "frame": frame, "season": season,
                  "created": now, "status": "cannot", "status_reasons": [message], "failed": True}
    with open(path, "w") as fh:
        json.dump(result, fh, separators=(",", ":"))
    rebuild(cfg)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scan one frame for one season.")
    ap.add_argument("--frame", required=True)
    ap.add_argument("--season", required=True, type=int)
    ap.add_argument("--request-id", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--tif", action="store_true", help="also write a GeoTIFF for QGIS")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    try:
        sc = run_scan(a.frame, a.season, cfg, a.request_id or None, a.tif)
    except Exception as err:                                # noqa: BLE001
        traceback.print_exc()
        msg = str(err) if isinstance(err, (ValueError, FileNotFoundError)) or "images" in str(err) \
            else "The scan failed on the server. Try again later; if it keeps failing, tell Tahreem."
        write_failure(a.frame, a.season, a.request_id or None, msg, cfg)
        return 1
    print(f"{sc['frame']} {sc['season']}: {sc['status']}, Prosopis {sc['area_ha']['low']}-"
          f"{sc['area_ha']['high']} ha, familiar ground {sc['applicability']:.0%}, clear {sc['valid_share']:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
