"""
Checks the density cut-offs in config/prosopis.yaml against a reference map of Prosopis
dense and sparse stands: by default the 2016 dense/sparse classes in the Dryad package
(sublocations_mgmt_scenarios.shp, field soc_cl_16: 1 dense, 2 sparse).

    python tools/check_density_cutoffs.py [--season 2016] [--ref data/labels/sublocations_mgmt_scenarios.shp]
                                          [--field soc_cl_16] [frames ...]

Runs the model on each frame (about a minute a frame; nothing is written to dashboard/),
gives each ~1.2 ha cell the reference class covering most of it, and prints how the
map's none / thin / medium / thick compare, plus the cut-offs that would fit best.
Swap in crews' by-eye classes (a shapefile with 1 = dense, 2 = sparse) when they exist.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mapping.applicability import Applicability   # noqa: E402
from mapping.config import load_config, repo_path   # noqa: E402
from mapping.features import feature_stack   # noqa: E402
from mapping.scan import load_model   # noqa: E402
from mapping.summarize import cells_from_pixels   # noqa: E402
from mapping.tiles import frame_bounds   # noqa: E402

FINE = 10          # reference raster: 10 x 10 sub-pixels per cell


def frame_cells(frame, season, cfg, bundle, ref, field):
    from rasterio import features
    from rasterio.transform import from_bounds
    cells = cfg["grid"]["cells"]
    s, w, n, e = frame_bounds(frame, cfg["grid"]["frame_deg"])
    stack = feature_stack((w, s, e, n), season, cfg)
    X = stack["array"].reshape(stack["array"].shape[0], -1).T
    rows, cols = stack["array"].shape[1:]
    clear = np.isfinite(X).all(axis=1)
    proba = np.full(len(X), np.nan, dtype="float32")
    rf = bundle["model"]
    proba[clear] = rf.predict_proba(X[clear])[:, list(rf.classes_).index(1)]
    familiar = np.zeros(len(X), dtype=bool)
    familiar[clear] = Applicability.from_dict(bundle["aoa"]).inside(X[clear])
    known = (clear & familiar).reshape(rows, cols)
    cover, unknown = cells_from_pixels((proba >= bundle["threshold"]).reshape(rows, cols) & known, known, cells)

    m = cells * FINE
    sub = ref.cx[w:e, s:n]
    if not len(sub):
        return None
    fine = features.rasterize(((g, int(v)) for g, v in zip(sub.geometry, sub[field])), out_shape=(m, m),
                              transform=from_bounds(w, s, e, n, m, m), fill=255, dtype="uint8")
    blk = fine.reshape(cells, FINE, cells, FINE)
    dense, sparse, outside = ((blk == k).mean(axis=(1, 3)) for k in (1, 2, 255))
    other = 1 - dense - sparse - outside
    ok = (outside < 0.1) & (unknown <= cfg["unknown_cell_share"]) & np.isfinite(cover)
    label = np.select([(dense >= sparse) & (dense >= other), (sparse > dense) & (sparse >= other)], [2, 1], 0)
    return cover[ok], label[ok]


def classes(cover, thin, thick):
    """0 none, 1 sparse (thin or medium), 2 dense (thick)."""
    return np.select([cover >= thick, cover >= thin], [2, 1], 0)


def balanced(pred, label):
    return float(np.mean([((pred == k) & (label == k)).sum() / max(1, (label == k).sum()) for k in range(3)
                          if (label == k).any()]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("frames", nargs="*", default=["N0040_E03600", "N0050_E03600"])
    ap.add_argument("--season", type=int, default=2016)
    ap.add_argument("--ref", default=None)
    ap.add_argument("--field", default="soc_cl_16")
    a = ap.parse_args(argv)
    import geopandas as gpd
    cfg = load_config()
    ref_path = a.ref or repo_path(cfg, "data/labels/sublocations_mgmt_scenarios.shp")
    ref = gpd.read_file(ref_path).to_crs(4326)
    bundle = load_model(cfg)
    got = [x for x in (frame_cells(f, a.season, cfg, bundle, ref, a.field) for f in a.frames) if x is not None]
    if not got:
        sys.exit("None of these frames overlaps the reference map.")
    cover = np.concatenate([g[0] for g in got])
    label = np.concatenate([g[1] for g in got])
    c = cfg["density_cutoffs"]
    four = np.select([cover >= c["thick"], cover >= c["medium"], cover >= c["thin"]], [3, 2, 1], 0)
    print(f"{len(cover)} cells; reference none / sparse / dense: {np.bincount(label, minlength=3).tolist()}")
    print(f"correlation of the map's Prosopis share with the reference class: {np.corrcoef(cover, label)[0, 1]:.2f}")
    print("share of each reference class the map calls none / thin / medium / thick:")
    for k, name in enumerate(("none", "sparse", "dense")):
        row = np.bincount(four[label == k], minlength=4)
        print(f"  {name:6s} " + "  ".join(f"{x:.0%}" for x in row / max(1, row.sum())))
    th = four == 3
    print(f"of the cells mapped thick: {np.mean(label[th] == 2):.0%} dense, {np.mean(label[th] == 1):.0%} sparse, "
          f"{np.mean(label[th] == 0):.0%} no Prosopis")
    now = balanced(classes(cover, c["thin"], c["thick"]), label)
    grid = np.round(np.arange(0.02, 0.96, 0.01), 2)
    t1, t2 = max(((x, y) for x in grid for y in grid if y > x), key=lambda t: balanced(classes(cover, *t), label))
    print(f"balanced accuracy, none / sparse / dense: {now:.2f} with the cut-offs now "
          f"(thin {c['thin']}, thick {c['thick']}); best fit {balanced(classes(cover, t1, t2), label):.2f} "
          f"at thin {t1}, thick {t2}")


if __name__ == "__main__":
    main()
