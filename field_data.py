"""
Field sheet in, clean plot data out.

ONE ROW PER STEM. Plant details repeat down the rows of that plant.

That shape is not a compromise, it is the shape a phone form already produces.
KoboToolbox and ODK store a multi-stem plant as a "repeat group", and the
standard way to read one is to flatten it into exactly this: one row per child
record, parent columns repeated. So a sheet filled in by hand today and a form
filled in on a phone next year land in the same format, with no reformatting
in between.

The repetition also buys free error checking. If crown width is typed as 4.2
on one row of a plant and 4.8 on another, somebody mistyped, and `validate`
says so. A wide sheet with stems across columns cannot catch that.

Column names follow Darwin Core where a standard term exists (eventDate,
recordedBy, decimalLatitude, coordinateUncertaintyInMeters,
identificationQualifier) so the data can go to GBIF or a Kobo form later
without being renamed.

No third-party dependencies, deliberately: this has to run on whatever laptop
is in the office. pandas will read the same CSV happily if you prefer it.
"""

from __future__ import annotations

import csv
import datetime as _dt
import math
from collections import defaultdict

# --- The schema --------------------------------------------------------------

REQUIRED = ["plot_id", "plant_id", "stem_no", "d30_cm"]

RECOMMENDED = [
    "eventDate", "recordedBy", "decimalLatitude", "decimalLongitude",
    "coordinateUncertaintyInMeters", "species_confirmed",
    "crown_a_m", "crown_b_m", "height_m", "plot_area_ha",
]

OPTIONAL = [
    "measured_at_cm", "dbh_cm", "stem_status", "plant_status",
    "boundary_plant", "identificationQualifier", "notes",
]

ALL_COLUMNS = REQUIRED + RECOMMENDED + OPTIONAL

# Columns that describe the PLANT, so they must be identical on every row of
# that plant. Disagreement means a transcription error.
PLANT_LEVEL = [
    "eventDate", "recordedBy", "decimalLatitude", "decimalLongitude",
    "coordinateUncertaintyInMeters", "species_confirmed", "crown_a_m",
    "crown_b_m", "height_m", "plant_status", "boundary_plant",
    "identificationQualifier",
]

COLUMN_HELP = {
    "plot_id": "Plot identifier, e.g. BAR-01. Same for every row in that plot.",
    "plant_id": "Plant number within the plot, starting at 1.",
    "stem_no": "Stem number within the plant, starting at 1.",
    "d30_cm": "Stem diameter 30 cm above the ground, in cm, ONE DECIMAL.",
    "eventDate": "Date measured, YYYY-MM-DD.",
    "recordedBy": "Who measured and who recorded, e.g. 'A. Kiprop / J. Lokai'.",
    "decimalLatitude": "GPS latitude at the base of the plant, decimal degrees, WGS84.",
    "decimalLongitude": "GPS longitude at the base of the plant, decimal degrees, WGS84.",
    "coordinateUncertaintyInMeters": "GPS accuracy the device reported. Over 10 m is a problem.",
    "species_confirmed": "yes / no / unsure. Unsure means photograph it and flag it.",
    "crown_a_m": "Widest canopy spread, metres.",
    "crown_b_m": "Canopy spread at 90 degrees to crown_a, metres.",
    "height_m": "Ground to highest LIVING foliage, metres.",
    "plot_area_ha": "Plot area in hectares. 0.1 for the standard 17.84 m radius circle.",
    "measured_at_cm": "Height the tape actually went, if something blocked 30 cm. Blank means 30.",
    "dbh_cm": "Diameter at 1.3 m, if reachable. Optional, for comparison with other studies.",
    "stem_status": "ok / oversize / unmeasured / leaning.",
    "plant_status": "live / dead / coppiced.",
    "boundary_plant": "yes if the base sits on the plot line.",
    "identificationQualifier": "Darwin Core. Use 'cf.' when the species is probable but unconfirmed.",
    "notes": "Anything odd. Free text. Always better than tidying a record.",
}

# Plausibility limits. Outside these it is almost certainly a typo or a unit
# mix-up, not an unusual plant.
SANE = {
    "d30_cm": (0.3, 120.0),
    "dbh_cm": (0.3, 120.0),
    "crown_a_m": (0.1, 30.0),
    "crown_b_m": (0.1, 30.0),
    "height_m": (0.1, 25.0),
    "coordinateUncertaintyInMeters": (0.0, 500.0),
}

TYPICAL_STEMS_PER_PLANT = 7.8   # Cape Verde 1,882 stems / 240 trees; Iran 7.8 at 15 yr
GPS_ACCURACY_LIMIT_M = 10.0


class FieldDataError(Exception):
    """The sheet cannot be read at all."""


# --- Loading -----------------------------------------------------------------

def _num(v):
    """Parse a number, tolerating blanks, commas and stray spaces."""
    if v is None:
        return None
    raw = str(v).strip()
    if raw == "" or raw.lower() in {"na", "n/a", "-", "none", "null"}:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return raw  # hand back exactly what was typed; validate() reports it


def load_field_sheet(path: str) -> list[dict]:
    """
    Read a field sheet CSV into a list of row dicts.

    Tolerates a UTF-8 BOM (Excel adds one), blank trailing rows, and extra
    columns you have added for your own use. Does NOT validate: call
    `validate` on the result and read the report before calculating anything.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise FieldDataError(f"{path} appears to be empty.")
        headers = [h.strip() for h in reader.fieldnames]
        rows = []
        for i, raw in enumerate(reader, start=2):   # row 1 is the header
            row = {h: raw.get(orig) for h, orig in zip(headers, reader.fieldnames)}
            if all((v is None or str(v).strip() == "") for v in row.values()):
                continue                              # blank spacer row
            row = {k: (_num(v) if k in SANE or k in ("stem_no", "plot_area_ha",
                                                     "measured_at_cm",
                                                     "decimalLatitude",
                                                     "decimalLongitude")
                       else (str(v).strip() if v is not None else None))
                   for k, v in row.items()}
            row["_row"] = i
            rows.append(row)
    if not rows:
        raise FieldDataError(f"{path} has a header but no data rows.")
    return rows


# --- Validation --------------------------------------------------------------

class Report:
    """What the sheet says, and what is wrong with it."""

    def __init__(self):
        self.errors: list[str] = []     # must fix: calculation would be wrong
        self.warnings: list[str] = []   # should look at: probably a field slip
        self.notes: list[str] = []      # informational
        self.stats: dict = {}

    @property
    def ok(self) -> bool:
        return not self.errors

    def __str__(self) -> str:
        out = []
        s = self.stats
        if s:
            out.append(
                f"{s.get('n_rows', 0)} stem rows, {s.get('n_plants', 0)} plants, "
                f"{s.get('n_plots', 0)} plot(s), "
                f"{s.get('stems_per_plant', 0):.1f} stems per plant on average"
            )
        for tag, items in (("ERROR", self.errors), ("WARNING", self.warnings),
                           ("NOTE", self.notes)):
            for m in items:
                out.append(f"  {tag:<8} {m}")
        if self.ok and not self.warnings:
            out.append("  Nothing to fix. Safe to calculate.")
        elif self.ok:
            out.append("  No blocking errors. Read the warnings, then calculate.")
        else:
            out.append("  FIX THE ERRORS FIRST. Do not calculate from this sheet.")
        return "\n".join(out)


def validate(rows: list[dict]) -> Report:
    """
    Check a loaded sheet and say what is wrong with it.

    Errors block calculation. Warnings are things that are probably a slip in
    the field and are worth a look, but will not produce a wrong number on
    their own.
    """
    r = Report()
    present = set().union(*(set(x) for x in rows))

    missing = [c for c in REQUIRED if c not in present]
    if missing:
        r.errors.append(f"required column(s) missing: {', '.join(missing)}")
        return r   # nothing further makes sense

    for c in RECOMMENDED:
        if c not in present:
            r.notes.append(f"no '{c}' column. {COLUMN_HELP.get(c, '')}")

    seen, decimals_used = {}, False
    for row in rows:
        n, rid = row["_row"], f"{row.get('plot_id')}/{row.get('plant_id')}"

        # required values actually filled in
        for c in REQUIRED:
            if row.get(c) in (None, ""):
                r.errors.append(f"row {n}: '{c}' is blank")

        d = row.get("d30_cm")
        if isinstance(d, str):
            r.errors.append(f"row {n}: d30_cm '{d}' is not a number")
        elif d is not None:
            lo, hi = SANE["d30_cm"]
            if not lo <= d <= hi:
                r.errors.append(
                    f"row {n} ({rid}): d30_cm {d} is outside {lo}-{hi} cm. "
                    f"Check for a typo or a metres/centimetres mix-up.")
            if d != int(d):
                decimals_used = True

        # other numeric columns
        for c, (lo, hi) in SANE.items():
            if c == "d30_cm":
                continue
            v = row.get(c)
            if isinstance(v, str):
                r.warnings.append(f"row {n}: '{c}' value '{v}' is not a number")
            elif v is not None and not lo <= v <= hi:
                r.warnings.append(f"row {n} ({rid}): {c} {v} looks implausible "
                                  f"(expected {lo}-{hi})")

        # duplicate stem identity
        key = (row.get("plot_id"), row.get("plant_id"), row.get("stem_no"))
        if key in seen:
            r.errors.append(f"row {n}: stem {key} already appears on row {seen[key]}. "
                            f"Duplicated stem, or a stem number reused.")
        seen[key] = n

        # date format
        ed = row.get("eventDate")
        if ed:
            try:
                _dt.date.fromisoformat(str(ed)[:10])
            except ValueError:
                r.warnings.append(f"row {n}: eventDate '{ed}' is not YYYY-MM-DD")

        # GPS accuracy
        acc = row.get("coordinateUncertaintyInMeters")
        if isinstance(acc, (int, float)) and acc > GPS_ACCURACY_LIMIT_M:
            r.warnings.append(f"row {n} ({rid}): GPS accuracy {acc:.0f} m is worse "
                              f"than {GPS_ACCURACY_LIMIT_M:.0f} m")

        # species confirmation
        sc = (row.get("species_confirmed") or "").lower()
        if sc in {"no", "unsure", "n", "?"}:
            r.warnings.append(f"row {n} ({rid}): species not confirmed "
                              f"('{row.get('species_confirmed')}'). Excluded from "
                              f"totals until someone confirms it.")

        # tape moved off 30 cm
        mh = row.get("measured_at_cm")
        if isinstance(mh, (int, float)) and abs(mh - 30.0) > 0.01:
            r.notes.append(f"row {n} ({rid}): measured at {mh:.0f} cm, not 30 cm "
                           f"(recorded, as the SOP asks)")

    if not decimals_used:
        r.warnings.append(
            "every d30_cm is a whole number. The tape reads to one decimal and "
            "the SOP asks for it, so somebody is probably rounding.")

    # --- per-plant checks
    plants = defaultdict(list)
    for row in rows:
        plants[(row.get("plot_id"), row.get("plant_id"))].append(row)

    thin_plants = []
    for (plot, plant), rs in plants.items():
        nums = sorted(x.get("stem_no") for x in rs if x.get("stem_no") is not None)
        want = list(range(1, len(nums) + 1))
        if nums and [float(x) for x in nums] != [float(x) for x in want]:
            r.warnings.append(f"{plot}/{plant}: stem numbers are {nums}, expected "
                              f"{want}. A stem may have been skipped.")
        if len(rs) <= 2:
            thin_plants.append(f"{plot}/{plant} ({len(rs)})")

        # plant-level columns must agree across the plant's rows
        for c in PLANT_LEVEL:
            vals = {x.get(c) for x in rs if x.get(c) not in (None, "")}
            if len(vals) > 1:
                r.errors.append(
                    f"{plot}/{plant}: '{c}' differs across the plant's rows "
                    f"({sorted(map(str, vals))}). One of them is a typo.")

    if thin_plants:
        r.warnings.append(
            f"{len(thin_plants)} plant(s) with 2 or fewer stems: "
            f"{', '.join(thin_plants[:6])}{' ...' if len(thin_plants) > 6 else ''}. "
            f"Typical is about {TYPICAL_STEMS_PER_PLANT:.0f}, so stems may have "
            f"been missed in the thicket.")

    plots = {row.get("plot_id") for row in rows}
    r.stats = {
        "n_rows": len(rows), "n_plants": len(plants), "n_plots": len(plots),
        "stems_per_plant": len(rows) / len(plants) if plants else 0.0,
        "plots": sorted(map(str, plots)),
    }
    if r.stats["stems_per_plant"] < 4 and len(plants) > 3:
        r.warnings.append(
            f"average {r.stats['stems_per_plant']:.1f} stems per plant, against a "
            f"typical {TYPICAL_STEMS_PER_PLANT:.0f}. Worth asking the crew whether "
            f"stems are being missed.")
    return r


# --- Reshaping for the calculator --------------------------------------------

def to_plots(rows: list[dict], drop_unconfirmed: bool = True) -> dict:
    """
    Turn validated rows into the shape `prosopis_residue` expects:
    {plot_id: {"trees": [[d30, d30, ...], ...], "area_ha": float}}

    drop_unconfirmed
        True leaves out plants whose species_confirmed is no or unsure. That is
        the safe default: a native acacia counted as mesquite inflates the
        total and poisons the training data behind the map.
    """
    skip_sp = {"no", "unsure", "n", "?"}
    out: dict = {}
    for row in rows:
        if drop_unconfirmed and (row.get("species_confirmed") or "").lower() in skip_sp:
            continue
        if (row.get("stem_status") or "").lower() == "unmeasured":
            continue
        d = row.get("d30_cm")
        if not isinstance(d, (int, float)):
            continue
        plot = str(row.get("plot_id"))
        p = out.setdefault(plot, {"trees": defaultdict(list), "area_ha": None})
        p["trees"][str(row.get("plant_id"))].append(float(d))
        a = row.get("plot_area_ha")
        if isinstance(a, (int, float)) and a > 0:
            p["area_ha"] = float(a)
    for plot, p in out.items():
        p["trees"] = [stems for _, stems in sorted(p["trees"].items())]
        if p["area_ha"] is None:
            p["area_ha"] = 0.1          # the standard KEFRI plot
    return out


def write_template(path: str, example_rows: bool = True) -> None:
    """Write an empty field sheet with the right headers, ready to fill in."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ALL_COLUMNS)
        w.writeheader()
        if example_rows:
            base = {
                "plot_id": "BAR-01", "eventDate": "2026-10-15",
                "recordedBy": "A. Kiprop / J. Lokai", "plot_area_ha": 0.1,
                "decimalLatitude": 0.4712, "decimalLongitude": 36.0211,
                "coordinateUncertaintyInMeters": 4, "species_confirmed": "yes",
                "crown_a_m": 4.2, "crown_b_m": 3.6, "height_m": 5.1,
                "plant_status": "live", "boundary_plant": "no",
            }
            for stem, d in enumerate([6.2, 8.5, 11.0, 4.3], start=1):
                w.writerow({**base, "plant_id": 1, "stem_no": stem,
                            "d30_cm": d, "stem_status": "ok"})


__all__ = [
    "REQUIRED", "RECOMMENDED", "OPTIONAL", "ALL_COLUMNS", "COLUMN_HELP",
    "PLANT_LEVEL", "SANE", "load_field_sheet", "validate", "Report",
    "to_plots", "write_template", "FieldDataError",
]
