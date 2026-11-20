"""
Finding images, the season calendar, and whether anything newer exists.

    python -m mapping.imagery freshness              update dashboard/data/index.json
    python -m mapping.imagery list N0040_E03600 2026 list the scenes a scan would use

"MOST RECENT" MEANS THE LATEST COMPLETE SEASON
    The model tells evergreen Prosopis from deciduous Vachellia by how green
    each stays in the dry season compared with the wet one. So a scan needs a
    dry-season picture and a wet-season picture, not the latest single photo.
    freshness_report() says, per frame, whether a newer usable image has been
    published than the ones the last scan used.
"""

from __future__ import annotations

import calendar
import json
import os
import sys
from datetime import date, datetime, timezone

import planetary_computer
import pystac_client
from shapely.geometry import box, shape

from .config import load_config, repo_path
from .tiles import frame_bounds


def catalog(cfg: dict) -> pystac_client.Client:
    url = cfg["catalog"]["url"]
    # Planetary Computer hands out links that work for about an hour.
    modifier = planetary_computer.sign_inplace if "planetarycomputer" in url else None
    return pystac_client.Client.open(url, modifier=modifier)


def season_windows(season: int, cfg: dict) -> dict:
    """{"dry": (first day, last day), "wet": (...)} for season year `season`."""
    out = {}
    for name in ("dry", "wet"):
        w = cfg["seasons"][name]
        y = season + w["year_offset"]
        last = calendar.monthrange(y, w["end_month"])[1]
        out[name] = (date(y, w["start_month"], 1), date(y, w["end_month"], last))
    return out


def season_end(season: int, cfg: dict) -> date:
    return max(w[1] for w in season_windows(season, cfg).values())


def latest_season(cfg: dict, today: date | None = None) -> int:
    """The newest season whose windows have all closed."""
    today = today or date.today()
    return today.year if today > season_end(today.year, cfg) else today.year - 1


def next_update(cfg: dict, today: date | None = None) -> str:
    """Year-month from which the next season can be scanned, e.g. '2027-09'."""
    end = season_end(latest_season(cfg, today) + 1, cfg)
    return f"{end.year + (end.month == 12)}-{end.month % 12 + 1:02d}"


def search_scenes(bbox, start: date, end: date, cfg: dict, cat=None, max_cloud=None) -> list:
    """Scenes over bbox (west, south, east, north) between two dates, newest first."""
    cat = cat or catalog(cfg)
    max_cloud = cfg["catalog"]["max_cloud"] if max_cloud is None else max_cloud
    search = cat.search(
        collections=[cfg["catalog"]["collection"]], bbox=list(bbox),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
    )
    return sorted(search.items(), key=lambda i: i.datetime, reverse=True)


def coverage(item, bbox) -> float:
    """Share of bbox (west, south, east, north) inside the scene's footprint."""
    area = box(*bbox)
    return shape(item.geometry).intersection(area).area / area.area


def pick_scenes(items: list, n: int, bbox=None) -> list:
    """
    The n best scenes, in date order: ones that cover the area first, then the
    least cloudy. A tile is imaged from two orbits, and one of them often
    misses the frame entirely even with 0% cloud.
    """
    def rank(i):
        cov = coverage(i, bbox) if bbox is not None else 1.0
        return (-round(cov, 1), i.properties.get("eo:cloud_cover", 100))
    best = [i for i in sorted(items, key=rank) if bbox is None or coverage(i, bbox) > 0.05][:n]
    return sorted(best, key=lambda i: i.datetime)


def newest_usable_scene(bbox, start: date, end: date, cfg: dict, cat=None):
    items = search_scenes(bbox, start, end, cfg, cat)
    return items[0] if items else None


def freshness_report(cfg: dict | None = None, index_path: str | None = None) -> dict:
    """
    For every scanned frame, record the newest usable image published for the
    latest season. The dashboard compares it with the images the last scan
    used and says "up to date" or "newer images published".
    """
    cfg = cfg or load_config()
    index_path = index_path or os.path.join(repo_path(cfg, cfg["output_dir"]), "index.json")
    index = json.load(open(index_path)) if os.path.exists(index_path) else {"frames": {}}
    latest = latest_season(cfg)
    windows = season_windows(latest, cfg).values()
    start, end = min(w[0] for w in windows), max(w[1] for w in windows)
    cat = catalog(cfg)
    for fid, fr in index.get("frames", {}).items():
        s, w, n, e = frame_bounds(fid, cfg["grid"]["frame_deg"])
        items = [i for i in search_scenes((w, s, e, n), start, end, cfg, cat) if coverage(i, (w, s, e, n)) > 0.5]
        fr["newest_image"] = items[0].datetime.date().isoformat() if items else None
    index.update(
        demo=False,
        checked=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        latest_season=latest,
        next_update=next_update(cfg),
    )
    with open(index_path, "w") as f:
        json.dump(index, f, indent=1)
    return index


if __name__ == "__main__":
    cfg = load_config()
    if sys.argv[1:2] == ["freshness"]:
        idx = freshness_report(cfg)
        print(f"checked {len(idx.get('frames', {}))} frames; latest season {idx['latest_season']}")
    elif sys.argv[1:2] == ["list"]:
        fid, season = sys.argv[2], int(sys.argv[3])
        s, w, n, e = frame_bounds(fid)
        for name, (a, b) in season_windows(season, cfg).items():
            items = search_scenes((w, s, e, n), a, b, cfg)
            use = pick_scenes(items, cfg["catalog"]["max_scenes_per_season"], (w, s, e, n))
            print(f"{name} {a}..{b}: {len(items)} scenes, using {len(use)}:")
            for i in use:
                print(f"   {i.datetime.date()}  cloud {i.properties.get('eo:cloud_cover', 0):.0f}%  "
                      f"covers {coverage(i, (w, s, e, n)):.0%}  {i.id}")
    else:
        print(__doc__)
