"""
The ONE place features are defined. Training and scanning both call
feature_stack(), and the model card records the names, so a model can never be
fed something different from what it learned on.

Default (Mbaabu et al. 2019): blue, green, red, nir, swir1, swir2 and NDVI for
the dry season and the wet season, plus elevation = 15 features.
"""

from __future__ import annotations

import numpy as np
import odc.stac
import xarray as xr

from .composite import geobox, seasonal_composite
from .imagery import catalog, season_windows

INDICES = {
    "ndvi": lambda b: (b["nir"] - b["red"]) / (b["nir"] + b["red"]),
    "vari": lambda b: (b["green"] - b["red"]) / (b["green"] + b["red"] - b["blue"]),
    "rvi": lambda b: b["nir"] / b["red"],
}


class NoImagery(RuntimeError):
    """No usable scene for a season window. The message says which."""


def feature_names(cfg: dict) -> list[str]:
    f = cfg["features"]
    return [f"{s}_{n}" for s in ("dry", "wet") for n in f["per_season"]] + list(f["static"])


def load_elevation(bbox, cfg: dict, cat=None) -> xr.DataArray:
    cat = cat or catalog(cfg)
    items = list(cat.search(collections=[cfg["catalog"]["dem_collection"]], bbox=list(bbox)).items())
    if not items:
        raise NoImagery("no elevation tiles found for this area")
    ds = odc.stac.load(items, bands=["data"], geobox=geobox(bbox, cfg), resampling="bilinear",
                       dtype="float32", nodata=np.nan)
    return ds["data"].max("time", skipna=True)          # tiles do not overlap: max = mosaic


def _season_layer(comp: xr.Dataset, name: str) -> np.ndarray:
    if name in INDICES:
        with np.errstate(divide="ignore", invalid="ignore"):
            v = INDICES[name](comp).values
    else:
        v = comp[name].values
    v = v.astype("float32")
    v[~np.isfinite(v)] = np.nan
    return v


def feature_stack(bbox, season: int, cfg: dict, cat=None) -> dict:
    """
    Everything the model needs for one area and one season:
        names   feature names, in order
        array   float32 (n_features, rows, cols); NaN where unknown
        gbox    the pixel grid (row 0 = north edge)
        info    which scenes were used, for the scan record
    """
    cat = cat or catalog(cfg)
    windows = season_windows(season, cfg)
    comps, info = {}, {"sensor": cfg["catalog"]["sensor"]}
    for s in ("dry", "wet"):
        comp, sinfo = seasonal_composite(bbox, *windows[s], cfg, cat)
        info[s] = sinfo
        if comp is None:
            a, b = windows[s]
            raise NoImagery(f"No usable {s}-season images between {a} and {b}.")
        comps[s] = comp
    layers = [_season_layer(comps[s], n) for s in ("dry", "wet") for n in cfg["features"]["per_season"]]
    for n in cfg["features"]["static"]:
        if n != "elevation":
            raise ValueError(f"unknown static feature '{n}'")
        layers.append(load_elevation(bbox, cfg, cat).values.astype("float32"))
    arr = np.stack(layers)
    info["valid_share"] = float(np.isfinite(arr).all(axis=0).mean())
    return {"names": feature_names(cfg), "array": arr, "gbox": geobox(bbox, cfg), "info": info}
