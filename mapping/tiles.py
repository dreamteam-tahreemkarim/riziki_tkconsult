"""
The fixed frame grid. Same formula as frameId() in dashboard/app.js.

A frame is named by its south-west corner in hundredths of a degree:
N0040_E03600 covers 0.40-0.50 N, 36.00-36.10 E (about 11 x 11 km).
Fixed frames mean every scan of an area covers exactly the same ground, so
before/after comparisons are fair.

    python -m mapping.tiles N0040_E03600      prints the frame's bounds
"""

from __future__ import annotations

import math
import re
import sys

FRAME_RE = re.compile(r"^([NS])(\d{4})_([EW])(\d{5})$")


def frame_id(lat: float, lon: float, frame_deg: float = 0.1) -> str:
    per = round(1 / frame_deg)
    s = math.floor(round(lat * per, 9)) / per        # round first: 0.3 * 10 is 2.9999...
    w = math.floor(round(lon * per, 9)) / per
    return (f"{'N' if s >= 0 else 'S'}{abs(round(s * 100)):04d}_"
            f"{'E' if w >= 0 else 'W'}{abs(round(w * 100)):05d}")


def frame_bounds(fid: str, frame_deg: float = 0.1) -> tuple[float, float, float, float]:
    """(south, west, north, east). Raises ValueError for a malformed name."""
    m = FRAME_RE.match(fid or "")
    if not m:
        raise ValueError(f"'{fid}' is not a frame name like N0040_E03600")
    s = (1 if m[1] == "N" else -1) * int(m[2]) / 100
    w = (1 if m[3] == "E" else -1) * int(m[4]) / 100
    return s, w, round(s + frame_deg, 9), round(w + frame_deg, 9)


def in_region(fid: str, region: dict, frame_deg: float = 0.1) -> bool:
    s, w, n, e = frame_bounds(fid, frame_deg)
    eps = 1e-9
    return (s >= region["south"] - eps and n <= region["north"] + eps
            and w >= region["west"] - eps and e <= region["east"] + eps)


def frames_in_bbox(south, west, north, east, frame_deg: float = 0.1) -> list[str]:
    per = round(1 / frame_deg)
    out = []
    i = math.floor(round(south * per, 9))
    while i / per < north:
        j = math.floor(round(west * per, 9))
        while j / per < east:
            out.append(frame_id((i + 0.5) / per, (j + 0.5) / per, frame_deg))
            j += 1
        i += 1
    return out


def cell_ha(lat: float, frame_deg: float, cells: int) -> float:
    """Area of one grid cell at this latitude, in hectares."""
    dx = 111.32 * math.cos(math.radians(lat)) * frame_deg / cells     # km
    dy = 110.57 * frame_deg / cells                                    # km
    return dx * dy * 100


if __name__ == "__main__":
    for f in sys.argv[1:]:
        print(f, frame_bounds(f))
