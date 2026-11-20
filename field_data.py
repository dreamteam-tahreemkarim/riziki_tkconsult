"""
Reads a filled-in field sheet and checks it before anything is calculated.

THREE CALLS, IN ORDER

    rows   = load_field_sheet("sheet.csv")   every row, exactly as written
    checks = validate(rows)                  what is wrong, and where
    plots  = to_plots(rows)                  only the rows fit to use

run_pipeline.py makes these calls for you.

ONE ROW = ONE STEM
    A plant with four stems is four rows with the same plot_id and plant_id.
    Plot-level columns (date, GPS, plot_area_ha, density_class) repeat on
    every row of that plot.

CHECKING THE MAPS (optional plot-level columns)
    map_point      the point's name from the dashboard GPS file, e.g.
                   "N0040_E03600 C01", so the plot can be matched to the map
    cover_pct      share of the plot under Prosopis canopy, 0-100, by eye
    height_method  how height_m was taken: pole, clinometer, rangefinder or eye
    to_checks() turns these into one record per plot for mapping/field_check.py.

A PLOT WITH NO PROSOPIS IN IT
    Write one row with the plot_id, leave d30_cm empty, and put "none" in
    stem_status. The plot then counts as 0 t/ha instead of vanishing. This
    matters: leaving empty plots out makes thin ground look thicker than it is.

ERRORS AND WARNINGS
    An error means the answer would be wrong, so nothing gets calculated.
    A warning means the answer can be calculated, but someone should look.
    Every message names the column and the spreadsheet row (the header is
    row 1), so you can go straight to the cell.

Nothing here needs any installed packages.
"""

from __future__ import annotations

import csv
from datetime import date

from stratify import DENSITY_CLASSES

# --- What the sheet must and should have ------------------------------------

# Without these, there is nothing to calculate.
REQUIRED = ["plot_id", "plant_id", "d30_cm"]

# The answer can be worked out without these, but it is weaker or harder to
# trust. GPS, date and recorder make every row usable as a training label for
# the satellite model later (Darwin Core names, so other tools read them too).
RECOMMENDED = [
    "stem_no", "species_confirmed", "stem_status", "plot_area_ha",
    "eventDate", "recordedBy", "decimalLatitude", "decimalLongitude",
    "coordinateUncertaintyInMeters", "density_class",
    "map_point", "cover_pct", "height_m", "height_method",
]

DEFAULT_PLOT_AREA_HA = 0.1        # KEFRI national inventory plot, 17.84 m radius
D30_FITTED_MIN, D30_FITTED_MAX = 2.5, 18.0   # range the Kenyan equation was fitted on
D30_SANE_MAX = 150.0              # above this it is almost surely mm, or a typo
GPS_GOOD_ENOUGH_M = 30            # one Landsat pixel; worse than this is a weak label
KENYA = {"lat": (-5.0, 5.5), "lon": (33.5, 42.0)}

YES = {"yes", "y", "true", "1"}
NO = {"no", "n", "false", "0", "unsure", "?"}

# Values for stem_status, and what each one means.
STEM_STATUS = {
    "ok": "measured with a tape or calliper",
    "estimated": "diameter judged by eye, not measured",
    "dead": "standing dead stem, still counted as wood",
    "unmeasured": "seen but not measured; set aside, not counted",
    "none": "plot was measured and had no Prosopis in it",
}
SET_ASIDE = {"unmeasured"}

# Values for height_method. Eye estimates are fine for biomass but too rough
# to check a height map that is itself about 3 m off on average.
HEIGHT_METHODS = {
    "pole": "measuring pole or tape held against the plant (best under ~6 m)",
    "clinometer": "clinometer or hypsometer, distance taped",
    "rangefinder": "laser rangefinder or laser hypsometer",
    "eye": "judged by eye",
}
HEIGHT_SANE_MAX_M = 20.0          # Prosopis taller than this is rare: check it
CROWN_SANE_MAX_M = 30.0


class FieldSheetError(ValueError):
    """The file itself could not be read. The message says why."""


# --- 1. Read -----------------------------------------------------------------

def load_field_sheet(path: str) -> list[dict]:
    """
    Every non-blank row of the sheet as a dict of column -> text.

    Each row also gets "_row", its line number in the spreadsheet, so any
    message can point back to the exact cell. Nothing is changed or dropped
    here except fully blank lines; checking is validate()'s job.
    """
    if path.lower().endswith((".xlsx", ".xls")):
        raise FieldSheetError(
            f"'{path}' is an Excel file. Open it, choose File > Save As > "
            f"CSV UTF-8, and run again with the .csv file.")

    # Excel on Windows sometimes saves in cp1252 instead of UTF-8.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=encoding) as f:
                reader = csv.DictReader(f)
                rows = []
                for raw in reader:
                    row = {(k or "").strip(): (v or "").strip()
                           for k, v in raw.items() if k is not None}
                    if not any(row.values()):
                        continue                  # a line of empty commas
                    row["_row"] = reader.line_num
                    rows.append(row)
            return rows
        except UnicodeDecodeError:
            continue
    raise FieldSheetError(
        f"could not read '{path}' as text. Save it again as CSV UTF-8.")


# --- 2. Check ----------------------------------------------------------------

class Checks:
    """
    Everything validate() found. `ok` is False if there is any error.

    The same problem on many rows is reported once, with all its row
    numbers, so a sheet with one repeated mistake gives one line, not fifty.
    """

    SHOW = 25   # most lines of each kind to print

    def __init__(self, n_rows: int):
        self.n_rows = n_rows
        self._found: dict[tuple, list] = {}

    def add(self, level: str, column: str, message: str, row=None):
        rows = self._found.setdefault((level, column, message), [])
        if row is not None:
            rows.append(row)

    def _lines(self, level: str) -> list[str]:
        out = []
        for (lv, col, msg), rows in self._found.items():
            if lv != level:
                continue
            where = col
            if rows:
                shown = ", ".join(str(r) for r in rows[:8])
                more = f" +{len(rows) - 8} more" if len(rows) > 8 else ""
                where += f", row{'s' if len(rows) > 1 else ''} {shown}{more}"
            out.append(f"{where}: {msg}")
        return out

    @property
    def errors(self) -> list[str]:
        return self._lines("error")

    @property
    def warnings(self) -> list[str]:
        return self._lines("warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def __str__(self) -> str:
        e, w = self.errors, self.warnings
        lines = [f"{self.n_rows} rows checked: {len(e)} error(s), {len(w)} warning(s)"]
        for label, items in (("ERROR  ", e), ("warning", w)):
            lines += [f"{label} {x}" for x in items[: self.SHOW]]
            if len(items) > self.SHOW:
                lines.append(f"{label} ... and {len(items) - self.SHOW} more")
        lines.append("OK to calculate." if self.ok else
                     "Fix the errors, save, and run again.")
        return "\n".join(lines)


def _num(text):
    """The text as a number, or None if it is empty or not a number."""
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _usable(row: dict, drop_unconfirmed: bool) -> bool:
    """True if this stem row should be counted."""
    if row.get("stem_status", "").lower() in SET_ASIDE:
        return False
    if drop_unconfirmed and "species_confirmed" in row:
        return row["species_confirmed"].lower() in YES
    return True


def validate(rows: list[dict]) -> Checks:
    """Check every row. Read the result's lines top to bottom and fix errors first."""
    c = Checks(len(rows))
    if not rows:
        c.add("error", "sheet", "no data rows under the header")
        return c

    columns = set().union(*(set(r) for r in rows)) - {"_row"}
    for col in REQUIRED:
        if col not in columns:
            c.add("error", col, "column is missing (names are case sensitive)")
    if not c.ok:
        return c
    if "species_confirmed" not in columns:
        c.add("warning", "species_confirmed",
              "column is missing, so every stem is assumed to be Prosopis")

    seen_stems: dict[tuple, int] = {}
    plot_area: dict[str, tuple] = {}       # plot -> (value, row it was first seen)
    plot_density: dict[str, tuple] = {}
    plot_cover: dict[str, tuple] = {}
    plot_counts: dict[str, dict] = {}      # plot -> {"used", "set_aside", "none"}
    out_of_range: dict[str, list] = {}
    today = date.today()

    for r in rows:
        n = r["_row"]
        plot = r.get("plot_id", "")
        status = r.get("stem_status", "").lower()

        if not plot:
            c.add("error", "plot_id", "empty", n)
            continue
        counts = plot_counts.setdefault(plot, {"used": 0, "set_aside": 0, "none": 0})

        if status and status not in STEM_STATUS:
            c.add("warning", "stem_status",
                  f"'{r['stem_status']}' is not one of {', '.join(STEM_STATUS)}; "
                  f"the stem is counted as if 'ok'", n)

        # --- plot-level columns: must agree on every row of the plot
        area_text = r.get("plot_area_ha", "")
        if area_text:
            a = _num(area_text)
            if a is None or a <= 0:
                c.add("error", "plot_area_ha", f"'{area_text}' must be a positive number", n)
            elif plot not in plot_area:
                plot_area[plot] = (a, n)
            elif abs(plot_area[plot][0] - a) > 1e-9:
                c.add("error", "plot_area_ha",
                      f"plot {plot} says {a} here but {plot_area[plot][0]} on row "
                      f"{plot_area[plot][1]}; one plot has one area", n)

        dens = r.get("density_class", "").lower()
        if dens:
            if dens not in DENSITY_CLASSES:
                c.add("warning", "density_class",
                      f"'{r['density_class']}' is not one of {', '.join(DENSITY_CLASSES)}", n)
            elif plot not in plot_density:
                plot_density[plot] = (dens, n)
            elif plot_density[plot][0] != dens:
                c.add("warning", "density_class",
                      f"plot {plot} is '{dens}' here but '{plot_density[plot][0]}' on "
                      f"row {plot_density[plot][1]}", n)

        cov_text = r.get("cover_pct", "")
        if cov_text:
            cv = _num(cov_text)
            if cv is None or not 0 <= cv <= 100:
                c.add("error", "cover_pct", f"'{cov_text}' must be a number from 0 to 100", n)
            elif plot not in plot_cover:
                plot_cover[plot] = (cv, n)
            elif abs(plot_cover[plot][0] - cv) > 1e-9:
                c.add("warning", "cover_pct",
                      f"plot {plot} says {cv:g} here but {plot_cover[plot][0]:g} on row "
                      f"{plot_cover[plot][1]}; the first value is used", n)

        _check_gps_and_date(c, r, n, today)
        _check_size(c, r, n)

        # --- an empty plot
        if status == "none":
            counts["none"] += 1
            if r.get("d30_cm"):
                c.add("warning", "d30_cm",
                      "has a diameter but stem_status says 'none'; the diameter is ignored", n)
            continue

        # --- the stem itself
        if not r.get("plant_id"):
            c.add("error", "plant_id", "empty; every stem must belong to a plant", n)

        stem_no = r.get("stem_no", "")
        if stem_no:
            key = (plot, r.get("plant_id", ""), stem_no)
            if key in seen_stems:
                c.add("error", "stem_no",
                      f"plot {plot}, plant {key[1]}, stem {stem_no} already appears on "
                      f"row {seen_stems[key]}; it would be counted twice", n)
            else:
                seen_stems[key] = n

        if "species_confirmed" in r:
            sc = r["species_confirmed"].lower()
            if not sc:
                c.add("warning", "species_confirmed",
                      "empty, so the stem is set aside; write yes or no", n)
            elif sc not in YES | NO:
                c.add("warning", "species_confirmed",
                      f"'{r['species_confirmed']}' is not yes or no; the stem is set aside", n)
        if r.get("identificationQualifier"):
            c.add("warning", "identificationQualifier",
                  "the recorder was not certain of the species; check species_confirmed", n)

        if status in SET_ASIDE:
            counts["set_aside"] += 1
            continue

        d_text = r.get("d30_cm", "")
        d = _num(d_text)
        if not d_text:
            c.add("error", "d30_cm",
                  "empty. If the stem was seen but not measured, write 'unmeasured' "
                  "in stem_status", n)
        elif d is None:
            hint = " (use a full stop for decimals: 8.5, not 8,5)" if "," in d_text else ""
            c.add("error", "d30_cm", f"'{d_text}' is not a number{hint}", n)
        elif d <= 0:
            c.add("error", "d30_cm", f"{d_text} must be above zero", n)
        elif d > D30_SANE_MAX:
            c.add("error", "d30_cm",
                  f"{d_text} cm is not a plausible stem; was it written in mm?", n)
        elif not D30_FITTED_MIN <= d <= D30_FITTED_MAX:
            out_of_range.setdefault(plot, []).append(n)

        at = _num(r.get("measured_at_cm", ""))
        if at is not None and abs(at - 30) > 0.5:
            c.add("warning", "measured_at_cm",
                  f"measured at {at:g} cm, not 30 cm; it is used as if it were D30", n)

        if _usable(r, drop_unconfirmed=True):
            counts["used"] += 1
        else:
            counts["set_aside"] += 1

    # --- whole-plot checks
    for plot, rows_out in out_of_range.items():
        msg = (f"plot {plot}: stem(s) outside the {D30_FITTED_MIN}-{D30_FITTED_MAX} cm "
               f"the Kenyan equation was fitted on. They are left out, so this "
               f"plot's total is a LOWER BOUND")
        for n in rows_out:
            c.add("warning", "d30_cm", msg, n)
    for plot, k in plot_counts.items():
        if k["none"] and k["used"]:
            c.add("warning", "stem_status",
                  f"plot {plot} has a 'none' row and measured stems; the stems are used")
        elif not k["none"] and not k["used"] and k["set_aside"]:
            c.add("warning", "plot_id",
                  f"plot {plot}: every stem was set aside (unconfirmed or unmeasured), "
                  f"so the whole plot is left out, not counted as zero")
        if plot not in plot_area:
            c.add("warning", "plot_area_ha",
                  f"plot {plot} has no area; using {DEFAULT_PLOT_AREA_HA} ha (KEFRI plot)")
    if c.ok and not any(k["used"] or k["none"] for k in plot_counts.values()):
        c.add("error", "sheet", "no stem is left to count once set-aside rows are removed")
    return c


def _check_size(c: Checks, r: dict, n: int):
    """Plant height and crown: optional, but if written they must make sense."""
    for col, top in (("height_m", HEIGHT_SANE_MAX_M), ("crown_a_m", CROWN_SANE_MAX_M), ("crown_b_m", CROWN_SANE_MAX_M)):
        t = r.get(col, "")
        if not t:
            continue
        v = _num(t)
        if v is None or v <= 0:
            c.add("error", col, f"'{t}' must be a positive number of metres", n)
        elif v > 3 * top:
            c.add("error", col, f"{t} m is not plausible; was it written in cm?", n)
        elif v > top:
            c.add("warning", col, f"{t} m is unusually large for Prosopis; check it", n)
    hm = r.get("height_method", "").lower()
    if hm and hm not in HEIGHT_METHODS:
        c.add("warning", "height_method", f"'{r['height_method']}' is not one of {', '.join(HEIGHT_METHODS)}", n)
    elif hm == "eye" and r.get("height_m"):
        c.add("warning", "height_method",
              "height judged by eye: fine for biomass, too rough to check the height map", n)


def _check_gps_and_date(c: Checks, r: dict, n: int, today: date):
    lat_t, lon_t = r.get("decimalLatitude", ""), r.get("decimalLongitude", "")
    if lat_t or lon_t:
        lat, lon = _num(lat_t), _num(lon_t)
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            c.add("error", "decimalLatitude/decimalLongitude",
                  f"'{lat_t}, {lon_t}' is not a valid position in decimal degrees", n)
        elif not (KENYA["lat"][0] <= lat <= KENYA["lat"][1]
                  and KENYA["lon"][0] <= lon <= KENYA["lon"][1]):
            swapped = (KENYA["lat"][0] <= lon <= KENYA["lat"][1]
                       and KENYA["lon"][0] <= lat <= KENYA["lon"][1])
            c.add("warning", "decimalLatitude/decimalLongitude",
                  "outside Kenya" + ("; latitude and longitude look swapped" if swapped else ""), n)

    unc = _num(r.get("coordinateUncertaintyInMeters", ""))
    if unc is not None and unc > GPS_GOOD_ENOUGH_M:
        c.add("warning", "coordinateUncertaintyInMeters",
              f"GPS error over {GPS_GOOD_ENOUGH_M} m; fine for biomass, weak as a "
              f"satellite training label", n)

    d_text = r.get("eventDate", "")
    if d_text:
        try:
            d = date.fromisoformat(d_text[:10])
        except ValueError:
            c.add("warning", "eventDate", f"'{d_text}' is not YYYY-MM-DD", n)
        else:
            if d > today:
                c.add("warning", "eventDate", f"{d_text} is in the future", n)


# --- 3. Group into plots -----------------------------------------------------

def to_plots(rows: list[dict], drop_unconfirmed: bool = True) -> dict:
    """
    The rows fit to use, grouped the way prosopis_residue expects:

        {plot_id: {"area_ha": 0.1, "trees": [[d30, d30, ...], [d30], ...]}}

    One inner list per plant, one number per stem. Stems marked 'unmeasured',
    and (if drop_unconfirmed) stems not confirmed as Prosopis, are left out.
    A plot with a 'none' row and no stems comes back with no trees, so it
    counts as 0 t/ha. A plot whose stems were ALL set aside is left out.

    Run validate() first: this function skips bad rows rather than reporting them.
    """
    plots: dict[str, dict] = {}
    for r in rows:
        pid = r.get("plot_id", "")
        if not pid:
            continue
        p = plots.setdefault(pid, {"area_ha": None, "plants": {}, "empty": False})
        if p["area_ha"] is None:
            p["area_ha"] = _num(r.get("plot_area_ha", ""))
        if r.get("stem_status", "").lower() == "none":
            p["empty"] = True
            continue
        if not _usable(r, drop_unconfirmed):
            continue
        d = _num(r.get("d30_cm", ""))
        if d is None or d <= 0:
            continue
        p["plants"].setdefault(r.get("plant_id", ""), []).append(d)

    return {
        pid: {"area_ha": p["area_ha"] or DEFAULT_PLOT_AREA_HA,
              "trees": list(p["plants"].values())}
        for pid, p in plots.items() if p["plants"] or p["empty"]
    }


def to_checks(rows: list[dict]) -> list[dict]:
    """
    One record per plot with GPS, for checking the satellite maps:

        plot_id, map_point, lat, lon, gps_m, eventDate, recordedBy,
        prosopis (True / False / None if unclear), cover_pct, density_class,
        height_median_m, height_max_m, height_method, n_plants

    Height is per plant (the first stem row of each plant carries it).
    Run validate() first.
    """
    by_plot: dict[str, list] = {}
    for r in rows:
        if r.get("plot_id"):
            by_plot.setdefault(r["plot_id"], []).append(r)
    out = []
    for pid, rs in by_plot.items():
        first = next((r for r in rs if _num(r.get("decimalLatitude")) is not None
                      and _num(r.get("decimalLongitude")) is not None), None)
        if first is None:
            continue
        val = lambda col: next((r[col] for r in rs if r.get(col)), "")
        empty = any(r.get("stem_status", "").lower() == "none" for r in rs)
        confirmed = [r for r in rs if r.get("stem_status", "").lower() != "none"
                     and r.get("species_confirmed", "yes").lower() in YES]
        prosopis = True if confirmed else (False if empty else None)
        heights = {}
        for r in confirmed:
            h = _num(r.get("height_m"))
            if h and h > 0:
                heights.setdefault(r.get("plant_id", ""), h)
        hs = sorted(heights.values())
        out.append({
            "plot_id": pid, "map_point": val("map_point"),
            "lat": _num(first["decimalLatitude"]), "lon": _num(first["decimalLongitude"]),
            "gps_m": _num(val("coordinateUncertaintyInMeters")),
            "eventDate": val("eventDate"), "recordedBy": val("recordedBy"),
            "prosopis": prosopis, "cover_pct": _num(val("cover_pct")),
            "density_class": val("density_class").lower() or None,
            "height_median_m": hs[len(hs) // 2] if hs else None,
            "height_max_m": hs[-1] if hs else None,
            "height_method": val("height_method").lower() or None,
            "n_plants": len({r.get("plant_id") for r in confirmed}),
        })
    return out


__all__ = [
    "REQUIRED", "RECOMMENDED", "STEM_STATUS", "HEIGHT_METHODS", "DEFAULT_PLOT_AREA_HA",
    "FieldSheetError", "Checks", "load_field_sheet", "validate", "to_plots", "to_checks",
]
