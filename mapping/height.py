"""
Tree height and canopy cover per dashboard cell, from the open Meta/WRI canopy height map.

    python -m mapping.height N0040_E03600 [--request-id ID]
        -> dashboard/data/scans/<frame>/height.json

WHAT IT IS
    CHMv2 (Meta + World Resources Institute, 2026) is an AI estimate of canopy height
    at 1.2 m, made from very-high-resolution satellite photos (mostly 2018-2020) and
    trained against airborne lidar. Free to use with credit (CC BY 4.0).
    It is a snapshot, not a time series: the photo dates over each frame go into the
    output. Published error against independent lidar: about 3 m on average, no overall
    bias (Brandt et al. 2026). Not yet checked in Kenya. It counts every tree, Prosopis
    or not, so pair it with a Prosopis scan.

PER CELL (same 100 x 100 grid as the scans, row 0 = north)
    cover    % of the cell under canopy taller than 1 m
    tree_m   median height of that canopy, m
    top_m    95th percentile height in the cell (the taller trees), m

HOW
    The map is a set of 1.2 m GeoTIFF tiles (web-mercator quadkeys). Every pixel is
    put in the cell its centre falls in and counted in a per-cell histogram of whole
    metres; the numbers above are read off the histograms. dashboard/height.js does
    exactly the same in the browser ("This device"), so both give the same answer.

PAUSE AND RESUME
    Work goes in strips of 512 pixel rows (the tiles' own rows). After each strip the
    histograms are saved to .cache/height/<frame>.npz. Stop at any time (Ctrl-C, lost
    connection) and run the same command again to carry on. A dropped connection is
    retried by itself, waiting longer each time.
"""

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from .config import load_config, repo_path
from .tiles import frame_bounds

CHM = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3"
SOURCE = {
    "name": "Meta/WRI Canopy Height Maps v2 (CHMv2)",
    "url": "https://registry.opendata.aws/dataforgood-fb-forestsv2/",
    "licence": "CC BY 4.0",
    "paper": "Brandt et al. 2026, arXiv:2603.06382",
}
ACCURACY = {"mae_m": 3.0, "bias_m": 0.0, "tested_against": "independent airborne lidar, worldwide",
            "checked_in_kenya": False}

ZOOM = 10                     # CHMv2 tiles are quadkeys of length 10
TILE_PX = 32768               # pixels across one quadkey tile
BLOCK = 512                   # internal TIFF tile size = one strip
R = 6378137.0                 # web-mercator sphere radius
BINS = 65                     # heights 0-63 m (63 = 63 or more), bin 64 = no data
NODATA = 255                  # value the source uses for "no data"
TREE_M = 1                    # canopy taller than this counts as tree cover
RETRY_WAIT = (5, 15, 45, 120, 300)   # seconds; then give up


# ------------------------------------------------------------------ geometry
def quadkey(x: int, y: int, z: int = ZOOM) -> str:
    return "".join(str((1 if x >> (z - 1 - i) & 1 else 0) + (2 if y >> (z - 1 - i) & 1 else 0))
                   for i in range(z))


def tile_xy(lat: float, lon: float, z: int = ZOOM) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def tiles_for(bounds) -> list[tuple[int, int]]:
    """(x, y) of every quadkey tile the frame touches, north to south, west to east."""
    s, w, n, e = bounds
    x0, y0 = tile_xy(n - 1e-9, w + 1e-9)
    x1, y1 = tile_xy(s + 1e-9, e - 1e-9)
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def tile_origin(x: int, y: int, z: int = ZOOM) -> tuple[float, float, float]:
    """Top-left corner (metres) and pixel size of a tile."""
    size = 2 * math.pi * R / 2 ** z
    return -math.pi * R + x * size, math.pi * R - y * size, size / TILE_PX


def merc_x(lon):
    return np.radians(lon) * R


def merc_y(lat):
    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * R


def pixel_window(bounds, x0m, y0m, res) -> tuple[int, int, int, int]:
    """(col0, col1, row0, row1) of the tile pixels whose centres can fall in the frame."""
    s, w, n, e = bounds
    c0 = max(0, math.floor((merc_x(w) - x0m) / res))
    c1 = min(TILE_PX, math.ceil((merc_x(e) - x0m) / res))
    r0 = max(0, math.floor((y0m - merc_y(n)) / res))
    r1 = min(TILE_PX, math.ceil((y0m - merc_y(s)) / res))
    return c0, c1, r0, r1


def cell_index(cols, rows, x0m, y0m, res, bounds, cells):
    """Cell column / row (-1 = outside) for pixel columns and rows, from pixel centres."""
    s, w, n, e = bounds
    step = (e - w) / cells
    lon = np.degrees((x0m + (cols + 0.5) * res) / R)
    lat = np.degrees(2 * np.arctan(np.exp((y0m - (rows + 0.5) * res) / R)) - np.pi / 2)
    ci = np.floor((lon - w) / step).astype(int)
    ri = np.floor((n - lat) / step).astype(int)
    ci[(ci < 0) | (ci >= cells)] = -1
    ri[(ri < 0) | (ri >= cells)] = -1
    return ci, ri


def units(bounds) -> list[tuple[int, int, int]]:
    """Work units (tile x, tile y, strip) in reading order: one per 512-row strip per tile."""
    out = []
    for x, y in tiles_for(bounds):
        x0m, y0m, res = tile_origin(x, y)
        c0, c1, r0, r1 = pixel_window(bounds, x0m, y0m, res)
        if c1 > c0 and r1 > r0:
            out += [(x, y, k) for k in range(r0 // BLOCK, (r1 - 1) // BLOCK + 1)]
    return out


# ---------------------------------------------------------------- histograms
def add_strip(hist, data, cols, rows, x0m, y0m, res, bounds, cells):
    """Count one strip of pixel values into hist (cells*cells, BINS)."""
    ci, ri = cell_index(cols, rows, x0m, y0m, res, bounds, cells)
    keep_c, keep_r = ci >= 0, ri >= 0
    if not keep_c.any() or not keep_r.any():
        return
    v = data[np.ix_(keep_r, keep_c)].astype(np.int64)
    b = np.where(v == NODATA, BINS - 1, np.minimum(v, BINS - 2))
    cell = ri[keep_r][:, None] * cells + ci[keep_c][None, :]
    hist += np.bincount((cell * BINS + b).ravel(), minlength=hist.size).reshape(hist.shape).astype(hist.dtype)


def pct(h, q: float) -> int:
    """Smallest value whose cumulative share reaches q (h = counts per whole metre)."""
    c = np.cumsum(h)
    return int(np.searchsorted(c, q * c[-1]))


def stats(hist, cells) -> dict:
    """cover %, tree_m, top_m per cell from the histograms; None where under half the cell has data."""
    cover, tree, top = [], [], []
    for h in hist:
        seen, valid = h.sum(), h[:BINS - 1]
        n = valid.sum()
        if seen == 0 or n < 0.5 * seen:
            cover.append(None), tree.append(None), top.append(None)
            continue
        t = valid[TREE_M + 1:]
        cover.append(int(math.floor(100 * t.sum() / n + 0.5)))   # half up, as in height.js
        tree.append(pct(t, 0.5) + TREE_M + 1 if t.sum() else 0)
        top.append(pct(valid, 0.95))
    return {"n": cells, "cover": cover, "tree_m": tree, "top_m": top}


# ------------------------------------------------------------------- reading
def gdal_env() -> dict:
    """Behind a TLS-inspecting proxy GDAL needs its own CA setting; elsewhere nothing."""
    ca = os.environ.get("CURL_CA_BUNDLE")
    return {"GDAL_CURL_CA_BUNDLE": ca} if ca else {}


def read_strip(src, c0, c1, ra, rb, say=print):
    """One strip of pixels; waits and retries if the connection drops."""
    for i, wait in enumerate((0,) + RETRY_WAIT):
        if wait:
            say(f"  connection problem, trying again in {wait} s")
            time.sleep(wait)
        try:
            return src.read(1, window=Window(c0, ra, c1 - c0, rb - ra))
        except rasterio.errors.RasterioIOError:
            if i == len(RETRY_WAIT):
                raise
    raise RuntimeError("unreachable")


def image_dates(bounds) -> dict:
    """Share of the frame covered by each photo date, from the tiles' metadata files."""
    import requests
    from shapely.geometry import box, shape

    s, w, n, e = bounds
    frame = box(w, s, e, n)
    shares = Counter()
    for x, y in tiles_for(bounds):
        fc = requests.get(f"{CHM}/metadata/{quadkey(x, y)}.geojson", timeout=60).json()
        for f in fc["features"]:
            a = shape(f["geometry"]).intersection(frame).area
            if a > 0:
                shares[f["properties"]["acq_date"]] += a / frame.area
    return {d: round(v, 3) for d, v in sorted(shares.items())}


def estimate(frame: str, cells: int, frame_deg: float, cache_dir: Path, say=print) -> dict:
    """Per-cell stats for one frame. Resumes from cache_dir/<frame>.npz if it exists."""
    bounds = frame_bounds(frame, frame_deg)
    todo = units(bounds)
    ck = cache_dir / f"{frame}.npz"
    hist, done = np.zeros((cells * cells, BINS), dtype=np.uint32), set()
    if ck.exists():
        z = np.load(ck)
        hist, done = z["hist"], {tuple(u) for u in z["done"].tolist()}
        say(f"  carrying on: {len(done)} of {len(todo)} strips already done")
    cache_dir.mkdir(parents=True, exist_ok=True)
    open_tiles = {}
    with rasterio.Env(**gdal_env()):
        for k, (x, y, strip) in enumerate(todo, 1):
            if (x, y, strip) in done:
                continue
            if (x, y) not in open_tiles:
                open_tiles[(x, y)] = rasterio.open(f"/vsicurl/{CHM}/chm/{quadkey(x, y)}.tif")
            src = open_tiles[(x, y)]
            x0m, y0m, res = tile_origin(x, y)
            c0, c1, r0, r1 = pixel_window(bounds, x0m, y0m, res)
            ra, rb = max(r0, strip * BLOCK), min(r1, (strip + 1) * BLOCK)
            data = read_strip(src, c0, c1, ra, rb, say)
            add_strip(hist, data, np.arange(c0, c1), np.arange(ra, rb), x0m, y0m, res, bounds, cells)
            done.add((x, y, strip))
            np.savez_compressed(ck, hist=hist, done=np.array(sorted(done)))
            say(f"  strip {k} of {len(todo)}")
        for src in open_tiles.values():
            src.close()
    out = stats(hist, cells)
    ck.unlink(missing_ok=True)
    return out


def run(frame: str, cfg: dict, request_id: str | None = None, say=print) -> dict:
    g = cfg["grid"]
    grid = estimate(frame, g["cells"], g["frame_deg"], Path(repo_path(cfg, ".cache/height")), say)
    out = {
        "frame": frame,
        "request_id": request_id,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "cloud" if os.environ.get("GITHUB_ACTIONS") else "computer",
        "source": SOURCE,
        "accuracy": ACCURACY,
        "image_dates": image_dates(frame_bounds(frame, g["frame_deg"])),
        "tree_threshold_m": TREE_M,
        "grid": grid,
    }
    path = Path(repo_path(cfg, cfg["output_dir"])) / "scans" / frame / "height.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":")))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tree height per cell for one or more frames.")
    ap.add_argument("frames", nargs="+")
    ap.add_argument("--request-id", default=None)
    a = ap.parse_args(argv)
    cfg = load_config()
    for frame in a.frames:
        print(frame)
        r = run(frame, cfg, a.request_id)
        print(f"  done; photos from {', '.join(r['image_dates'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
