"""
From pixels to what a person can act on.

    cells_from_pixels()   ~22 m pixels -> ~111 m cells: Prosopis share + unknown share
    density_codes()       each cell -> 0 none, 1 thin, 2 medium, 3 thick, 9 can't judge
    area_estimate()       Prosopis hectares WITH a range (Olofsson et al. 2014)
    density_ha()          hectares of ground in each density class

WHY A RANGE AND NOT ONE NUMBER
    Every map misses some Prosopis and calls some other things Prosopis, and
    how much depends on the place. In training, every pixel of the held-out
    Baringo blocks is mapped and compared with the labels in ~3,000 ha windows
    (train.area_error). The true area was between p10 and p90 times the
    mapped area in 8 windows out of 10; that is the range shown.
    Outside Baringo the spread is unknown, so the range is a floor, not a
    promise. The standard next step is Olofsson et al. (2014, Remote Sensing
    of Environment 148:42) with random field check points from scout trips.
"""

from __future__ import annotations

import math

import numpy as np


def cells_from_pixels(presence: np.ndarray, known: np.ndarray, cells: int):
    """
    presence  bool (rows, cols): pixel judged Prosopis
    known     bool (rows, cols): pixel had clear images and familiar features
    Returns (cover, unknown): each (cells, cells), shares 0-1. Row 0 = north.
    """
    h, w = presence.shape
    if h % cells or w % cells:
        raise ValueError(f"pixel grid {h}x{w} does not divide into {cells} cells")
    def blocks(a):
        return a.reshape(cells, h // cells, cells, w // cells).astype("float32")
    k = blocks(known).sum(axis=(1, 3))
    p = blocks(presence & known).sum(axis=(1, 3))
    n = (h // cells) * (w // cells)
    with np.errstate(invalid="ignore", divide="ignore"):
        cover = np.where(k > 0, p / k, np.nan)
    return cover, 1 - k / n


def density_codes(cover: np.ndarray, unknown: np.ndarray, cfg: dict) -> str:
    c = cfg["density_cutoffs"]
    code = np.full(cover.shape, "0", dtype="<U1")
    code[cover >= c["thin"]] = "1"
    code[cover >= c["medium"]] = "2"
    code[cover >= c["thick"]] = "3"
    code[(unknown > cfg["unknown_cell_share"]) | ~np.isfinite(cover)] = "9"
    return "".join(code.ravel())


def density_ha(codes: str, cell_ha: float) -> dict:
    return {name: round(codes.count(ch) * cell_ha) for ch, name in (("3", "thick"), ("2", "medium"), ("1", "thin"))}


def area_estimate(mapped_p_ha: float, area_error: dict) -> dict:
    """Prosopis hectares: central value and an 80% range, from mapped hectares."""
    lo, mid, hi = (area_error.get(k) for k in ("p10", "p50", "p90"))
    if lo is None or not all(map(math.isfinite, (lo, mid, hi))):
        return {"prosopis": round(mapped_p_ha), "low": None, "high": None, "mapped": round(mapped_p_ha),
                "method": "mapped pixels only; no error measurement available"}
    return {"prosopis": round(mapped_p_ha * mid), "low": round(mapped_p_ha * lo), "high": round(mapped_p_ha * hi),
            "mapped": round(mapped_p_ha),
            "method": f"80% range from held-out Baringo windows ({area_error.get('n_windows')} windows of "
                      f"~{area_error.get('window_deg', 0.05) * 111:.0f} km)"}
