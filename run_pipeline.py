"""
One call: filled-in sheet goes in, an explained answer comes out.

    import run_pipeline
    result = run_pipeline.run("my_field_sheet.csv")
    print(result.report())

Everything else in this folder is a part this script puts together:

    field_data.py        reads and checks the sheet
    prosopis_residue.py  turns diameters into weight, and weight into carbon
    stratify.py          scales a few measured plots up to a whole area

Nothing here needs any installed packages. If the sheet is wrong, the report
says which file, which row and which column, so you know where to look.
"""

from __future__ import annotations

import csv
import os
import traceback

import field_data as fd
import prosopis_residue as pr
import stratify as st

# Where every number in the output comes from. Printed with the answer so a
# reader never has to ask.
SOURCES = {
    "equation": ("Kariuki, Muturi & Choge, in KEFRI (2020), Proceedings of the "
                 "2nd National Prosopis Management Workshop, Lake Baringo. "
                 "ISBN 978-9914-723-94-6."),
    "big_stems": ("Chojnacky, Heath & Jenkins (2014), Updated generalized "
                  "biomass equations for North American tree species, Forestry "
                  "87:129-151. Applied to P. juliflora by Dastranj et al. "
                  "(2025), Scientific Reports."),
    "economic": ("Sarfo et al. (2026), Availability and spatial distribution of "
                 "crop and forest biomass residues for biochar production in "
                 "Kenya, Scientific Reports."),
    "plot": ("0.1 ha circular plot, 17.84 m radius, after the KEFRI national "
             "inventory design."),
    "comparison": ("Published standing biomass for dense stands: 23.9-97.7 t/ha "
                   "(KEFRI Garissa and Tana zones), 43.7-44.1 t/ha (Magadi)."),
    "permanence": ("Puro Standard Biochar Methodology (2025) requires H/Corg "
                   "strictly below 0.70 for a 200+ year durability claim."),
}

KEY = [
    ("standing biomass", "Everything growing out there, oven dry. Not what you can take."),
    ("recoverable residue", "What is left after you remove what cannot be cut, "
                            "what belongs to someone else, and what is not worth hauling."),
    ("t/ha", "Tonnes per hectare."),
    ("oven dry", "Weight with the water driven out. Freshly cut wood weighs more."),
    ("almost precisely", "Stem was 2.5-18 cm, so the Kenyan equation covered it."),
    ("best estimate", "Stem was 18-60 cm. A published equation covers it, but not a Kenyan one."),
    ("educated guess", "Stem was outside every published range. Right shape, no evidence."),
    ("stratum / class", "A group of grid cells that look alike: thick, medium or thin."),
    ("error bar", "How far the answer could move just from which plots you happened to measure."),
]


class PipelineError(Exception):
    """Something was wrong with the input. The message says where to look."""


class Result:
    """The answer, plus everything needed to understand or question it."""

    def __init__(self):
        self.ok = False
        self.debug: list[str] = []
        self.checks = None
        self.steps: list[tuple] = []
        self.answer: dict = {}
        self.warnings: list[str] = []
        self.next_steps: list[str] = []

    def _log(self, msg):
        self.debug.append(msg)

    # -- the printable report --------------------------------------------
    def report(self, show_debug: bool = True) -> str:
        L, A = [], self.answer

        def rule(t=""):
            L.append("")
            L.append(f"--- {t} " + "-" * max(0, 68 - len(t)) if t else "-" * 72)

        rule("WHAT WAS READ")
        for d in self.debug:
            L.append(f"  {d}")

        rule("CHECKS ON THE SHEET")
        if self.checks is None:
            L.append("  not run")
        else:
            for line in str(self.checks).splitlines():
                L.append(f"  {line}")

        if not self.ok:
            rule("NO ANSWER")
            L.append("  The sheet did not pass. Fix the errors above and run again.")
            L.append("  Nothing below this line was calculated.")
            return "\n".join(L)

        rule("THE ANSWER")
        L.append(f"  area covered            {A['area_ha']:>14,.0f} ha")
        L.append(f"  standing biomass        {A['standing_t']:>14,.0f} t")
        if A.get("standing_low") is not None:
            L.append(f"    could be between      {A['standing_low']:,.0f} and {A['standing_high']:,.0f} t")
        L.append(f"  RECOVERABLE RESIDUE     {A['residue_t']:>14,.0f} t")
        if A.get("biochar_t") is not None:
            L.append(f"  biochar                 {A['biochar_t']:>14,.0f} t")
        if A.get("co2e_t") is not None:
            L.append(f"  CO2e                    {A['co2e_t']:>14,.0f} t")
        else:
            L.append("  biochar and CO2e        not calculated (no kiln numbers given)")

        rule("HOW THAT WAS WORKED OUT")
        for i, (name, value, how) in enumerate(self.steps, 1):
            L.append(f"  {i}. {name}")
            L.append(f"     {value}")
            L.append(f"     {how}")

        rule("HOW SURE WE ARE")
        for w in self.warnings:
            L.append(f"  {w}")
        L.append("")
        L.append("  The error bar above, if there is one, covers ONE thing only: which")
        L.append("  plots you happened to measure. It does NOT cover the equation, the")
        L.append("  fractions, or the kiln. The real uncertainty is wider than it looks.")

        rule("KEY")
        for term, meaning in KEY:
            L.append(f"  {term:<22} {meaning}")

        rule("WHERE THE NUMBERS COME FROM")
        for k, v in SOURCES.items():
            L.append(f"  {k:<12} {v}")

        if self.next_steps:
            rule("WHAT WOULD IMPROVE THIS MOST")
            for i, s in enumerate(self.next_steps, 1):
                L.append(f"  {i}. {s}")

        return "\n".join(L)

    def __str__(self):
        return self.report()


# --- helpers -----------------------------------------------------------------

def _nearby_sheets(path: str) -> str:
    """List the data files sitting near where they looked, to help them find it."""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    try:
        names = sorted(f for f in os.listdir(folder)
                       if f.lower().endswith((".csv", ".xlsx", ".xls", ".tsv")))
    except OSError:
        return f"(could not list '{folder}')"
    if not names:
        return f"(no spreadsheet files found in '{folder}')"
    return f"spreadsheets in '{folder}': {', '.join(names[:12])}"


def _read_grid(path: str, res: Result) -> list[dict]:
    if not os.path.exists(path):
        raise PipelineError(
            f"grid sheet not found at '{path}'. Check the spelling and that the "
            f"file sits where you think it does. {_nearby_sheets(path)}")
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    cells, bad = [], []
    for i, r in enumerate(rows, start=2):
        try:
            a = float(str(r.get("area_ha", "")).strip())
        except (TypeError, ValueError):
            bad.append(i)
            continue
        cells.append({"cell_id": r.get("cell_id"),
                      "density_class": str(r.get("density_class", "")).strip().lower(),
                      "area_ha": a})
    res._log(f"grid sheet '{path}': {len(cells)} usable cells, "
             f"{sum(c['area_ha'] for c in cells):,.0f} ha total")
    if bad:
        res._log(f"  grid rows skipped, area_ha not a number: {bad[:10]}")
    return cells


# --- the one call ------------------------------------------------------------

def run(
    field_sheet: str,
    grid_sheet: str | None = None,
    plot_classes: dict | None = None,
    class_plot_totals: dict | None = None,
    area_ha: float | None = None,
    # --- how much of the standing wood you can actually use
    residue_fraction: float | None = None,        # ONE dial, if you prefer
    harvest_fraction: float = 0.70,
    surplus_available_factor: float = 0.60,
    economic_viable_factor: float = pr.EVF_LOW,
    # --- the kiln
    biochar_ratio: float | None = None,           # t biochar per t residue
    char_carbon_fraction: float | None = None,
    kiln_batches: list[dict] | None = None,
    permanence_factor: float = 1.0,
) -> Result:
    """
    Read a filled-in field sheet and give a residue number for the whole area.

    THE ONLY THING YOU MUST SUPPLY is `field_sheet`.

    Scaling up, pick ONE:
      grid_sheet + class_plot_totals   proper stratified scale-up
      area_ha                          simple: measured t/ha times the area
      neither                          answer is for the measured plots only

    How much wood you can use, pick ONE:
      residue_fraction                 one number, 0 to 1, if you already know it
      the three factors                harvest x surplus x economic (the default)

    The kiln, pick ONE:
      kiln_batches                     weighed runs; the yield is fitted from them
      biochar_ratio + char_carbon_fraction
      neither                          stops at residue, no carbon number
    """
    res = Result()
    try:
        # ---------- 1. read and check the sheet
        if not os.path.exists(field_sheet):
            raise PipelineError(
                f"field sheet not found at '{field_sheet}'. Check the spelling, "
                f"and that the file is where you think it is. "
                f"{_nearby_sheets(field_sheet)}")
        rows = fd.load_field_sheet(field_sheet)
        res._log(f"field sheet '{field_sheet}': {len(rows)} stem rows read")
        found = sorted(set().union(*(set(r) for r in rows)) - {"_row"})
        res._log(f"  columns found: {', '.join(found)}")
        absent = [c for c in fd.REQUIRED if c not in found]
        if absent:
            raise PipelineError(
                f"the sheet is missing required column(s): {', '.join(absent)}. "
                f"Required columns are {', '.join(fd.REQUIRED)}. Column names are "
                f"case sensitive and must be on the first row.")
        soft = [c for c in fd.RECOMMENDED if c not in found]
        if soft:
            res._log(f"  columns not present (optional): {', '.join(soft)}")

        res.checks = fd.validate(rows)
        if not res.checks.ok:
            res._log("STOPPED: the sheet has errors that would make the answer wrong.")
            return res

        # ---------- 2. each measured plot
        plots = fd.to_plots(rows, drop_unconfirmed=True)
        dropped = len(rows) - sum(len(t) for p in plots.values() for t in p["trees"])
        if dropped:
            res._log(f"  {dropped} stem row(s) set aside (species unconfirmed, or "
                     f"marked unmeasured). Still in your sheet, just not counted.")
        plot_t_ha, n_stems = {}, 0
        for pid, p in plots.items():
            r = pr.plot_biomass_t_per_ha(p["trees"], plot_area_ha=p["area_ha"])
            plot_t_ha[pid] = r["t_per_ha"]
            n_stems += r["n_stems_used"]
        res.steps.append((
            "Every stem became a weight, every plant the sum of its stems.",
            "  " + ";  ".join(f"{k}: {v:,.1f} t/ha" for k, v in plot_t_ha.items()),
            f"{n_stems} stems across {len(plots)} plot(s). {SOURCES['plot']}"))

        # ---------- 3. scale up
        if grid_sheet and class_plot_totals:
            cells = _read_grid(grid_sheet, res)
            grid_total = sum(c["area_ha"] for c in cells)
            strata = st.from_grid(cells, class_plot_totals)
            sc = st.estimate_total(strata)
            bare = grid_total - sc["total_area_ha"]
            if bare > 0.5:
                res._log(f"  {bare:,.0f} ha of the grid is classed 'none' (no "
                         f"mesquite), so it is left out of the area below. "
                         f"Nothing has gone missing.")
            standing = sc["total_t"]
            total_area = sc["total_area_ha"]
            lo, hi = sc.get("low_t"), sc.get("high_t")
            res.warnings += sc["warnings"]
            detail = ";  ".join(
                f"{s['name']} {s['area_ha']:,.0f} ha x {s['mean_t_ha']:.1f} t/ha"
                for s in sc["strata"] if s["area_ha"] > 0)
            res.steps.append((
                "Each class of ground was given the average of the plots measured in it.",
                f"  {detail}",
                "Stratified estimate. Cells in a class are assumed to look like "
                "the plots measured in that class."))
            for s in strata:
                need = st.plots_needed(s, 0.15)
                if need and s.n_plots < need:
                    res.next_steps.append(
                        f"Measure {need - s.n_plots} more plot(s) in '{s.name}' "
                        f"to get within 15%.")
        elif area_ha:
            mean = sum(plot_t_ha.values()) / len(plot_t_ha)
            standing, total_area, lo, hi = mean * area_ha, area_ha, None, None
            res.steps.append((
                "The measured plots were averaged and applied to the whole area.",
                f"  {mean:,.1f} t/ha x {area_ha:,.0f} ha = {standing:,.0f} t",
                "SIMPLE scale-up, no classes. Fine for a rough figure. A grid "
                "sheet with classes gives a tighter answer and an error bar."))
            res.warnings.append(
                "No grid sheet, so no error bar. Thick and thin ground were "
                "treated as if they were the same, which they are not.")
        else:
            total_area = sum(p["area_ha"] for p in plots.values())
            standing = sum(plot_t_ha[k] * plots[k]["area_ha"] for k in plots)
            lo = hi = None
            res.steps.append((
                "No area given, so this covers the measured plots only.",
                f"  {standing:,.2f} t on {total_area:,.2f} ha",
                "Add a grid sheet or an area_ha to get a figure for real ground."))
            res.warnings.append("This is the plots themselves, not an area estimate.")

        # ---------- 4. standing -> residue
        if residue_fraction is not None:
            if not 0 <= residue_fraction <= 1:
                raise PipelineError("residue_fraction must be between 0 and 1")
            residue = standing * residue_fraction
            res.steps.append((
                "One overall fraction was applied to get usable residue.",
                f"  {standing:,.0f} t x {residue_fraction:.0%} = {residue:,.0f} t",
                "Your own single ratio, used instead of the three separate factors."))
        else:
            c = pr.residue_cascade(standing, harvest_fraction,
                                   surplus_available_factor, economic_viable_factor)
            residue = c["recoverable_residue_t"]
            res.steps.append((
                "Three fractions were taken off in turn.",
                f"  {standing:,.0f} t  x{harvest_fraction:.2f} can be cut"
                f"  x{surplus_available_factor:.2f} not already claimed"
                f"  x{economic_viable_factor:.2f} worth hauling  = {residue:,.0f} t",
                f"Economic fraction from {SOURCES['economic']} The other two are "
                f"placeholders until Riziki measures them."))
            res.warnings.append(
                "Harvest and surplus fractions are PLACEHOLDERS. The surplus one "
                "matters most: charcoal producers already work these stands, so a "
                "lot of this wood is spoken for.")

        # ---------- 5. residue -> carbon
        biochar = co2e = None
        if kiln_batches:
            kiln = pr.fit_kiln_profile("your kiln", kiln_batches)
            out = pr.biochar_and_co2e(residue, profile=kiln,
                                      permanence_factor=permanence_factor)
            biochar, co2e = out["biochar_t"], out["co2e_t"]
            res.steps.append((
                "Your own kiln runs set the biochar yield.",
                f"  {residue:,.0f} t x {kiln.char_mass_yield:.1%} = {biochar:,.0f} t biochar",
                str(kiln)))
            if "warning" in out:
                res.warnings.append(out["warning"])
        elif biochar_ratio is not None and char_carbon_fraction is not None:
            out = pr.biochar_and_co2e(residue, biochar_ratio, char_carbon_fraction,
                                      permanence_factor)
            biochar, co2e = out["biochar_t"], out["co2e_t"]
            res.steps.append((
                "A single biochar ratio was applied.",
                f"  {residue:,.0f} t x {biochar_ratio:.0%} = {biochar:,.0f} t biochar, "
                f"then x{char_carbon_fraction:.0%} carbon x 44/12 = {co2e:,.0f} t CO2e",
                "44/12 converts carbon to carbon dioxide and is fixed. "
                f"{SOURCES['permanence']}"))
            res.warnings.append(
                "Biochar ratio and carbon fraction were given by hand. Fit them "
                "from weighed kiln runs as soon as there are any.")
        else:
            res.next_steps.append(
                "Weigh some kiln runs (dry feedstock in, char out, plus a lab "
                "carbon and H/Corg test) so a carbon figure can be produced.")

        # ---------- done
        res.answer = {"area_ha": total_area, "standing_t": standing,
                      "standing_low": lo, "standing_high": hi,
                      "residue_t": residue, "biochar_t": biochar, "co2e_t": co2e,
                      "plot_t_ha": plot_t_ha}
        res.warnings.append(
            "No back-transform correction: the KEFRI model's residual standard "
            "error is unpublished, so totals lean LOW.")
        res.next_steps.append(
            "Ask KEFRI for the residual standard error of their model, or refit it.")
        res.next_steps.append(
            "Measure Riziki's own thick stems during harvest. That one dataset "
            "fixes the 18 cm ceiling, the missing error term, and site calibration "
            "at the same time.")
        res.ok = True
        return res

    except PipelineError as e:
        res._log(f"STOPPED: {e}")
        return res
    except Exception as e:                                    # noqa: BLE001
        res._log(f"UNEXPECTED PROBLEM: {type(e).__name__}: {e}")
        res._log("Full trace below. Send this to whoever maintains the scripts.")
        for line in traceback.format_exc().splitlines()[-6:]:
            res._log(f"  {line}")
        return res


__all__ = ["run", "Result", "PipelineError", "SOURCES", "KEY"]
