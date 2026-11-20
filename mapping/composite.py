"""
From a list of scenes to one cloud-free picture per season.

    load_scenes()          read the bands for one area, on the frame's pixel grid
    to_reflectance()       raw numbers -> reflectance, the same way for every year
    mask_clouds()          drop cloud, shadow and bad pixels using the scene's own map
    seasonal_composite()   the median of the clear pixels over the season

WHY to_reflectance() MATTERS FOR BEFORE/AFTER
    Since 25 January 2022 (ESA processing baseline 04.00) Sentinel-2 L2A
    numbers carry a +1000 offset. Skip it and every scan after 2022 looks
    brighter than every scan before, and a comparison shows change that is not
    there. Each scene's baseline is read from its own metadata.
"""

from __future__ import annotations

import numpy as np
import odc.stac
import xarray as xr
from odc.geo.geobox import GeoBox

from .imagery import pick_scenes, search_scenes

# Sentinel-2 scene classification (SCL) values kept as clear:
# 4 vegetation, 5 bare soil, 6 water, 7 unclassified, 11 snow.
# Everything else (no data, saturated, dark/shadow, cloud, cirrus) is dropped.
SCL_CLEAR = [4, 5, 6, 7, 11]

odc.stac.configure_rio(cloud_defaults=True, GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="2")


def geobox(bbox, cfg: dict) -> GeoBox:
    """The pixel grid for bbox (west, south, east, north), in degrees, north up."""
    return GeoBox.from_bbox(tuple(bbox), "EPSG:4326", resolution=cfg["grid"]["pixel_deg"])


def baseline(item) -> float:
    try:
        return float(item.properties.get("s2:processing_baseline", "0"))
    except ValueError:
        return 0.0


def to_reflectance(dn: xr.DataArray, offset: xr.DataArray | float) -> xr.DataArray:
    """Digital number -> surface reflectance (0-1). DN 0 means no data."""
    dn = dn.astype("float32").where(dn > 0)
    return (dn - offset) / 10000.0


def mask_clouds(ds: xr.Dataset, names: list[str]) -> xr.Dataset:
    clear = ds["SCL"].isin(SCL_CLEAR)
    return xr.Dataset({n: ds[n].where(clear) for n in names})


def load_scenes(items: list, gbox: GeoBox, cfg: dict) -> xr.Dataset:
    """Bands for these scenes on the grid, as reflectance, clouds removed. Dims: time, y, x."""
    assets = list(cfg["bands"])
    raw = odc.stac.load(
        items, bands=assets + ["SCL"], geobox=gbox, groupby="solar_day",
        resampling={"SCL": "nearest", "*": "average"}, dtype="uint16", nodata=0,
    )
    # One offset per day, from that day's scene metadata.
    day_offset = {}
    for it in items:
        day_offset[np.datetime64(it.datetime.date())] = 1000.0 if baseline(it) >= 4.0 else 0.0
    days = raw.time.values.astype("datetime64[D]")
    offset = xr.DataArray([day_offset.get(d, 0.0) for d in days], dims="time", coords={"time": raw.time})
    ds = xr.Dataset({name: to_reflectance(raw[a], offset) for a, name in cfg["bands"].items()})
    ds["SCL"] = raw["SCL"]
    return mask_clouds(ds, list(cfg["bands"].values()))


def seasonal_composite(bbox, start, end, cfg: dict, cat=None):
    """
    Median of the clear pixels between two dates.
    Returns (Dataset or None, info). None means no usable scene at all.
    """
    gbox = geobox(bbox, cfg)
    items = search_scenes(bbox, start, end, cfg, cat)
    use = pick_scenes(items, cfg["catalog"]["max_scenes_per_season"], bbox)
    info = {"window": [start.isoformat(), end.isoformat()], "found": len(items), "n_scenes": len(use),
            "dates": [i.datetime.date().isoformat() for i in use], "ids": [i.id for i in use]}
    if not use:
        return None, info
    ds = load_scenes(use, gbox, cfg)
    med = ds.median("time", skipna=True)
    info["clear_share"] = float(np.isfinite(med[list(cfg["bands"].values())[0]]).mean())
    return med, info
