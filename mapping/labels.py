"""
Training points: where we know Prosopis is (1) or is not (0).

TODAY: labels_from_dryad()
    Eschen et al. 2021, Dryad doi:10.5061/dryad.4xgxd258c (CC0), built from
    Mbaabu et al. 2019's own maps. 230,777 polygons over four sublocations near
    Marigat; PSINCE = the year Prosopis was first mapped there (1995, 2002,
    2009, 2016), 0 = not invaded by 2016.
    Put the shapefile bundle (.shp .shx .dbf .prj) in data/labels/.

    Two things to know about it:
      - Points are drawn at RANDOM over the whole mapped area, so about 14%
        land on Prosopis, as on the ground. Tested on every pixel of held-out
        blocks, the earlier "clean interior, 50/50" design mapped 55% too much
        Prosopis; random points got the area within ~8%, with slightly better
        agreement. It also makes cross-validation error rates representative,
        which the area range in summarize.area_estimate() relies on.
      - It is a model's output, not field truth. A model trained on it learns
        to copy that map, mistakes included. labels_from_field() is the fix.

NEXT: labels_from_field()
    Crew and scout sheets (field_sheet_TEMPLATE.csv). Every plot with GPS
    becomes a point: 1 if confirmed Prosopis stems were measured, 0 if the plot
    was marked stem_status = none.
"""

from __future__ import annotations

import math
import os
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point


def load_dryad(path: str) -> gpd.GeoDataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Download the shapefile bundle from "
            f"https://doi.org/10.5061/dryad.4xgxd258c into data/labels/.")
    g = gpd.read_file(path, columns=["PSINCE"])
    g["geometry"] = g.geometry.make_valid()                 # ~1,200 polygons need repair
    return g[~g.geometry.is_empty]


def labels_from_dryad(path: str, year: int = 2016, n_points: int = 8000,
                      edge_buffer_m: float = 0, seed: int = 42) -> gpd.GeoDataFrame:
    """
    n_points random points over the mapped area, labelled 1 (Prosopis by
    `year`) or 0. edge_buffer_m > 0 keeps only points whose surroundings carry
    one label (cleaner, but biases the map toward Prosopis; see above).
    """
    g = load_dryad(path)
    g["label"] = ((g.PSINCE > 0) & (g.PSINCE <= year)).astype(int)
    g = g[["label", "geometry"]]
    rng = np.random.default_rng(seed)
    x0, y0, x1, y1 = g.total_bounds
    kept = []
    for _ in range(60):                                    # batches of candidate points
        if sum(len(k) for k in kept) >= n_points:
            break
        pts = gpd.GeoDataFrame(geometry=[Point(x, y) for x, y in zip(
            rng.uniform(x0, x1, 20000), rng.uniform(y0, y1, 20000))], crs=g.crs)
        hit = gpd.sjoin(pts, g, predicate="within", how="inner")
        hit = hit[~hit.index.duplicated()]
        if edge_buffer_m > 0:
            ring = gpd.GeoDataFrame(geometry=hit.geometry.buffer(edge_buffer_m), crs=g.crs, index=hit.index)
            near = gpd.sjoin(ring, g, predicate="intersects", how="inner")
            clean = near.groupby(level=0)["label"].nunique() == 1
            hit = hit.loc[clean[clean].index]
        kept.append(hit[["label", "geometry"]])
    out = gpd.GeoDataFrame(pd.concat(kept).iloc[:n_points], crs=g.crs).to_crs(4326).reset_index(drop=True)
    out["source"] = "dryad"
    out["year"] = year
    return out


def rasterize_dryad(path: str, year: int, gbox) -> tuple:
    """(label, footprint) arrays on a pixel grid: 1 = Prosopis by `year`, footprint = mapped at all."""
    from rasterio.features import rasterize
    g = load_dryad(path).to_crs(4326)
    shape, tr = (gbox.shape.y, gbox.shape.x), gbox.affine
    inv = g[(g.PSINCE > 0) & (g.PSINCE <= year)]
    label = rasterize(((x, 1) for x in inv.geometry), out_shape=shape, transform=tr, fill=0, dtype="uint8")
    foot = rasterize(((x, 1) for x in g.geometry), out_shape=shape, transform=tr, fill=0, dtype="uint8")
    return label, foot.astype(bool)


def labels_from_field(csv_paths: list[str], year: int | None = None) -> gpd.GeoDataFrame:
    """One point per field plot that has GPS."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import field_data as fd

    rows = []
    for path in csv_paths:
        sheet = fd.load_field_sheet(path)
        checks = fd.validate(sheet)
        if not checks.ok:
            raise ValueError(f"{path} has errors; fix them first:\n{checks}")
        by_plot: dict[str, list] = {}
        for r in sheet:
            by_plot.setdefault(r["plot_id"], []).append(r)
        for pid, rs in by_plot.items():
            first = next((r for r in rs if r.get("decimalLatitude") and r.get("decimalLongitude")), None)
            if first is None:
                continue
            empty = any(r.get("stem_status", "").lower() == "none" for r in rs)
            present = any(r.get("species_confirmed", "yes").lower() in fd.YES and r.get("d30_cm") for r in rs)
            if not (present or empty):
                continue
            rows.append({
                "label": int(present), "plot_id": pid, "source": "field",
                "density_class": (first.get("density_class") or "").lower() or None,
                "eventDate": first.get("eventDate"),
                "year": year or int((first.get("eventDate") or "0")[:4] or 0),
                "geometry": Point(float(first["decimalLongitude"]), float(first["decimalLatitude"])),
            })
    return gpd.GeoDataFrame(rows, crs=4326)


def add_blocks(points: gpd.GeoDataFrame, block_km: float = 2) -> gpd.GeoDataFrame:
    """A block id per point (a ~block_km square grid) for spatial cross-validation."""
    deg = block_km / 111.0
    points = points.copy()
    points["block"] = [f"{math.floor(p.y / deg)}_{math.floor(p.x / deg)}" for p in points.geometry]
    return points


if __name__ == "__main__":
    from .config import load_config, repo_path
    cfg = load_config()
    lc = cfg["labels"]
    pts = labels_from_dryad(repo_path(cfg, lc["dryad_path"]), lc["year"], lc["n_points"],
                            lc["edge_buffer_m"], lc["seed"])
    print(pts.label.value_counts().to_dict(), pts.total_bounds)
