"""
Fit the Random Forest the way Mbaabu et al. 2019 did, and check it honestly.

    python -m mapping.train                  uses config/prosopis.yaml
    python -m mapping.train --config config/<plant>.yaml

What it does:
  1. labels.labels_from_dryad()   clean points for the label year (2016)
  2. features.feature_stack()     the same features a scan will use
  3. spatial_cv()                 accuracy with whole ~2 km blocks held out
  4. fit on everything, fit the applicability check, save:
        models/<model_id>.joblib     the model + what a scan needs
        models/<model_id>_card.json  how it was made and how good it is

WHY TWO ACCURACIES
    The paper reports 98% from random cross-validation: test pixels sit next to
    training pixels from the same hand-drawn polygons, so the test is easy.
    spatial_cv() holds out whole blocks of ground, which is what happens when
    the map is pointed at a new place. The card reports both; trust the spatial
    one (Ploton et al. 2020, Nature Communications 11:4540).

THE CUT-OFF
    The forest gives each pixel a vote share. The share above which a pixel is
    called Prosopis is chosen from the held-out predictions so that the mapped
    Prosopis area matches the labels: no systematic over- or under-mapping.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import cohen_kappa_score, confusion_matrix
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold

from .applicability import Applicability
from .config import load_config, repo_path
from .features import feature_stack
from .labels import add_blocks, labels_from_dryad, rasterize_dryad

CV_TREES = 300          # cross-validation only; enough to estimate accuracy, 3x faster


def sample_stack(stack: dict, points) -> np.ndarray:
    """Feature values at each point (nearest pixel). NaN rows where unknown."""
    gb = stack["gbox"]
    a = gb.affine
    cols = np.floor((points.geometry.x.values - a.c) / a.a).astype(int)
    rows = np.floor((points.geometry.y.values - a.f) / a.e).astype(int)
    h, w = stack["array"].shape[1:]
    ok = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
    X = np.full((len(points), stack["array"].shape[0]), np.nan, dtype="float32")
    X[ok] = stack["array"][:, rows[ok], cols[ok]].T
    return X


def scores(y, pred) -> dict:
    cm = confusion_matrix(y, pred, labels=[0, 1])           # rows = truth, cols = predicted
    pa = cm[1, 1] / cm[1].sum() if cm[1].sum() else float("nan")       # producer's (recall)
    ua = cm[1, 1] / cm[:, 1].sum() if cm[:, 1].sum() else float("nan")  # user's (precision)
    return {"overall_accuracy": float(np.trace(cm) / cm.sum()), "kappa": float(cohen_kappa_score(y, pred)),
            "prosopis_producers_accuracy": float(pa), "prosopis_users_accuracy": float(ua),
            "confusion_truth_by_predicted": cm.tolist()}


def area_matching_threshold(y, p) -> float:
    """The vote-share cut-off at which mapped Prosopis share equals the labels' share."""
    grid = np.round(np.arange(0.2, 0.81, 0.01), 2)
    gap = [abs((p >= t).mean() - y.mean()) for t in grid]
    return float(grid[int(np.argmin(gap))])


def spatial_cv(X, y, blocks, folds: int = 5, seed: int = 42) -> dict:
    """Out-of-fold vote shares with whole blocks held out, plus random CV to compare."""
    def run(splitter, groups=None):
        p = np.empty(len(y), dtype=float)
        fold_of = np.empty(len(y), dtype=int)
        models = []
        for k, (tr, te) in enumerate(splitter.split(X, y, groups)):
            rf = RandomForestClassifier(n_estimators=CV_TREES, n_jobs=-1, random_state=seed)
            rf.fit(X[tr], y[tr])
            p[te] = rf.predict_proba(X[te])[:, 1]
            fold_of[te] = k
            models.append(rf)
        return p, fold_of, models
    sp_p, sp_fold, sp_models = run(StratifiedGroupKFold(folds, shuffle=True, random_state=seed), blocks)
    rd_p, _, _ = run(StratifiedKFold(folds, shuffle=True, random_state=seed))
    t = area_matching_threshold(y, sp_p)
    return {"threshold": t, "spatial": scores(y, (sp_p >= t).astype(int)),
            "random": scores(y, (rd_p >= t).astype(int)), "_folds": sp_fold, "_models": sp_models}


def area_error(stack, label, foot, block_fold: dict, models, threshold, block_km, window_deg=0.05) -> dict:
    """
    How far off is the mapped Prosopis AREA, locally? Every pixel of every
    held-out block is mapped by the fold model that never saw that block, then
    mapped and true area are compared in windows of ~3,000 ha. The spread of
    true / mapped across windows becomes the range shown with every scan.
    """
    gb = stack["gbox"]
    a = gb.affine
    rows, cols = label.shape
    lat = a.f + (np.arange(rows) + 0.5) * a.e
    lon = a.c + (np.arange(cols) + 0.5) * a.a
    deg = block_km / 111.0
    bi, bj = np.floor(lat / deg).astype(int), np.floor(lon / deg).astype(int)
    fold = np.full((rows, cols), -1, dtype=int)
    for key, k in block_fold.items():
        i, j = map(int, key.split("_"))
        fold[np.ix_(bi == i, bj == j)] = k
    F = stack["array"].shape[0]
    flat = stack["array"].reshape(F, -1).T
    ok = (foot & np.isfinite(stack["array"]).all(axis=0) & (fold >= 0)).ravel()
    pred = np.zeros(rows * cols, dtype=bool)
    for k, m in enumerate(models):
        idx = np.flatnonzero(ok & (fold.ravel() == k))
        for c in range(0, len(idx), 200000):
            part = idx[c:c + 200000]
            pred[part] = m.predict_proba(flat[part])[:, 1] >= threshold
    pred, ok = pred.reshape(rows, cols), ok.reshape(rows, cols)
    w = max(1, int(round(window_deg / a.a)))
    ratios = []
    for r0 in range(0, rows - w + 1, w):
        for c0 in range(0, cols - w + 1, w):
            m = ok[r0:r0 + w, c0:c0 + w]
            if m.mean() < 0.5:
                continue
            mapped = pred[r0:r0 + w, c0:c0 + w][m].mean()
            true = label[r0:r0 + w, c0:c0 + w][m].mean()
            if mapped >= 0.02:
                ratios.append(true / mapped)
    q = np.percentile(ratios, [10, 50, 90]) if ratios else [np.nan] * 3
    agree = float((pred[ok] == (label[ok] == 1)).mean())
    return {"p10": round(float(q[0]), 3), "p50": round(float(q[1]), 3),
            "p90": round(float(q[2]), 3), "n_windows": len(ratios), "window_deg": window_deg,
            "pixel_agreement_all_heldout_pixels": round(agree, 3)}


def train(cfg: dict) -> dict:
    lc, mc = cfg["labels"], cfg["model"]
    print("1/4 labels ...", flush=True)
    pts = labels_from_dryad(repo_path(cfg, lc["dryad_path"]), lc["year"], lc["n_points"],
                            lc["edge_buffer_m"], lc["seed"])
    pts = add_blocks(pts, mc["block_km"])
    w, s, e, n = pts.total_bounds
    pad = cfg["grid"]["pixel_deg"] * 5
    bbox = (w - pad, s - pad, e + pad, n + pad)
    print(f"2/4 features for {lc['year']} over {np.round(bbox, 3).tolist()} ...", flush=True)
    stack = feature_stack(bbox, lc["year"], cfg)
    X = sample_stack(stack, pts)
    ok = np.isfinite(X).all(axis=1)
    X, y, blocks = X[ok], pts.label.values[ok], pts.block.values[ok]
    print(f"   {ok.sum()} of {len(ok)} points have every feature ({np.bincount(y).tolist()} by class)")

    print("3/4 cross-validation ...", flush=True)
    cv = spatial_cv(X, y, blocks, mc["folds"], lc["seed"])

    print("   area error on every held-out pixel ...", flush=True)
    label, foot = rasterize_dryad(repo_path(cfg, lc["dryad_path"]), lc["year"], stack["gbox"])
    block_fold = dict(zip(blocks, cv["_folds"]))
    area_err = area_error(stack, label, foot, block_fold, cv["_models"], cv["threshold"], mc["block_km"])

    print("4/4 final model + applicability ...", flush=True)
    rf = RandomForestClassifier(n_estimators=mc["n_estimators"], n_jobs=-1, random_state=lc["seed"])
    rf.fit(X, y)
    aoa = Applicability.fit(X, rf.feature_importances_, cv["_folds"])
    inside_train = float(np.mean(aoa.di(X) <= aoa.threshold))

    out_dir = repo_path(cfg, cfg["models_dir"])
    os.makedirs(out_dir, exist_ok=True)
    mid = cfg["model_id"]
    card = {
        "model_id": mid,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "species": cfg["species"],
        "sensor": cfg["catalog"]["sensor"], "catalog": cfg["catalog"]["url"], "collection": cfg["catalog"]["collection"],
        "season": lc["year"], "images": stack["info"],
        "labels": {"source": "Eschen et al. 2021, Dryad doi:10.5061/dryad.4xgxd258c (from Mbaabu et al. 2019 maps)",
                   "n_by_class": np.bincount(y).tolist(), "edge_buffer_m": lc["edge_buffer_m"],
                   "extent_wsen": [round(v, 4) for v in bbox]},
        "trained_on": f"Baringo (Marigat sublocations), Eschen et al. 2021 labels ({lc['year']})",
        "features": stack["names"],
        "importance": dict(zip(stack["names"], np.round(rf.feature_importances_, 4).tolist())),
        "model": {"type": "RandomForestClassifier", "n_estimators": mc["n_estimators"], "threshold": cv["threshold"]},
        "accuracy_spatial_cv": cv["spatial"], "accuracy_random_cv": cv["random"],
        "cv": {"folds": mc["folds"], "block_km": mc["block_km"], "trees_per_fold": CV_TREES},
        "area_error": area_err,
        "applicability": {"threshold_di": aoa.threshold, "training_points_inside": inside_train},
        "caveats": [
            "Labels are a published map, not field points: the model copies that map's errors.",
            "Accuracy is agreement with that map on random pixels of held-out ~2 km blocks.",
            "Spatial CV is within Baringo; accuracy elsewhere is unknown until field checks.",
            "Trained on one season (2016); later seasons rely on the same spectral pattern holding.",
        ],
    }
    joblib.dump({"model": rf, "features": stack["names"], "threshold": cv["threshold"],
                 "aoa": aoa.to_dict(), "area_error": area_err,
                 "card": card}, os.path.join(out_dir, f"{mid}.joblib"), compress=3)
    with open(os.path.join(out_dir, f"{mid}_card.json"), "w") as f:
        json.dump(card, f, indent=1)
    return card


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    card = train(load_config(ap.parse_args().config))
    sp, rd = card["accuracy_spatial_cv"], card["accuracy_random_cv"]
    print(f"spatial CV: accuracy {sp['overall_accuracy']:.3f}, kappa {sp['kappa']:.3f}, "
          f"Prosopis found {sp['prosopis_producers_accuracy']:.3f}, Prosopis right {sp['prosopis_users_accuracy']:.3f}, "
          f"cut-off {card['model']['threshold']}")
    print(f"random CV (paper's method): accuracy {rd['overall_accuracy']:.3f}, kappa {rd['kappa']:.3f}")
    ae = card["area_error"]
    print(f"area: true Prosopis was {ae['p10']:.0%}-{ae['p90']:.0%} of mapped in 8 of 10 "
          f"held-out windows (n={ae['n_windows']}); every held-out pixel agrees {ae['pixel_agreement_all_heldout_pixels']:.1%}")
    print("top features:", sorted(card["importance"].items(), key=lambda kv: -kv[1])[:5])
