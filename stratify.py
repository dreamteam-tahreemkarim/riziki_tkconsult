"""
From a few measured plots to a number for the whole area.

THE IDEA, IN PLAIN WORDS

You cannot measure every hectare. So you do three things:

  1. Split the area into grid cells.
  2. Sort every cell into a class: thick, medium or thin. The field team can do
     this by eye, or the satellite map can do it, or both.
  3. Measure a few plots inside each class, and assume the rest of the cells in
     that class look like the plots you measured.

That last assumption is the whole method. It works because cells in the same
class really are similar to each other, which is exactly why you sorted them
into classes first.

WHY NOT JUST MEASURE A FEW PLOTS ANYWHERE AND TAKE THE AVERAGE

Because a thick riverine thicket and a thin hillside can differ by five times.
Plots scattered at random land mostly in whatever class covers the most ground,
and the average ends up meaning nothing in particular. Sorting first means a
thick cell is predicted from thick plots, and you get a tighter answer from the
same amount of walking.

HOW MANY PLOTS

At least two per class, always. With one plot in a class there is no way to
know how much plots in that class vary, so there is no honest error bar, and
this module will say so rather than invent one.

Three to five per class is a sensible target. If plots within a class disagree
wildly, that class is really two classes, and splitting it is cheaper than
measuring more.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# A rubric the field team can apply standing in the stand, with no equipment.
# Deliberately coarse. Three classes people agree on beat seven they argue over.
DENSITY_CLASSES = {
    "thick": "Cannot walk through without cutting. Canopy closed overhead, "
             "little bare ground, stems crowd together.",
    "medium": "Can walk through, but has to be picked around. Canopy patchy, "
              "ground visible between plants.",
    "thin": "Walk straight through. Plants well apart, mostly open ground "
            "between them.",
    "none": "No mesquite. Grass, bare ground, crops, water or buildings.",
}

HEIGHT_CLASSES = {
    "tall": "Taller than a person reaching up, roughly over 3 m.",
    "short": "Head height or less, roughly under 3 m.",
}


# --- Plot geometry -----------------------------------------------------------

def plot_radius_m(area_ha: float = 0.1) -> float:
    """
    Radius of a circular plot of the given area.

    0.1 ha is 1,000 square metres, which is a circle of 17.84 m radius. That is
    the KEFRI national inventory plot.
    """
    if area_ha <= 0:
        raise ValueError("area_ha must be positive")
    return math.sqrt(area_ha * 10_000 / math.pi)


def slope_corrected_radius_m(area_ha: float = 0.1, slope_degrees: float = 0.0) -> dict:
    """
    How far to run the tape on sloping ground.

    A plot's area is always its FLAT area, looked at from above. If the team
    runs the tape 17.84 m along a hillside, the flat area inside it is smaller
    than 0.1 ha, so fewer plants get counted and the result comes out low.

    The fix is to pay out more tape on a slope. This returns how much.

    Baringo has both floodplain and hillslope, and the published Magadi work
    reports them separately, so this is not a corner case there.
    """
    if not 0 <= slope_degrees < 90:
        raise ValueError("slope_degrees must be between 0 and 90")
    flat = plot_radius_m(area_ha)
    along_ground = flat / math.cos(math.radians(slope_degrees))
    return {
        "flat_radius_m": flat,
        "tape_distance_m": along_ground,
        "extra_tape_m": along_ground - flat,
        "error_if_uncorrected": 1 - math.cos(math.radians(slope_degrees)),
        "slope_degrees": slope_degrees,
    }


# --- Strata ------------------------------------------------------------------

@dataclass
class Stratum:
    """
    One density class: how much ground it covers, and what the plots in it said.

    area_ha     total hectares of this class, added up across every grid cell
    plot_t_ha   one number per measured plot in this class, tonnes per hectare
    """
    name: str
    area_ha: float
    plot_t_ha: list[float] = field(default_factory=list)

    @property
    def n_plots(self) -> int:
        return len(self.plot_t_ha)

    @property
    def mean_t_ha(self) -> float:
        return sum(self.plot_t_ha) / self.n_plots if self.n_plots else 0.0

    @property
    def sd_t_ha(self) -> float | None:
        """Spread between plots. None with fewer than two plots."""
        if self.n_plots < 2:
            return None
        m = self.mean_t_ha
        return math.sqrt(sum((x - m) ** 2 for x in self.plot_t_ha) / (self.n_plots - 1))

    @property
    def total_t(self) -> float:
        return self.area_ha * self.mean_t_ha

    @property
    def cv(self) -> float | None:
        """How much plots disagree, as a share of the average."""
        sd, m = self.sd_t_ha, self.mean_t_ha
        return sd / m if (sd is not None and m > 0) else None


def estimate_total(strata: list[Stratum], confidence: float = 0.95) -> dict:
    """
    Add the classes up into one number for the whole area, with an error bar.

    Returns the total, and a range the true value is likely to sit in. If any
    class has fewer than two plots, the range is withheld rather than guessed,
    and `can_give_range` comes back False with a note saying which class to go
    and measure again.
    """
    if not strata:
        raise ValueError("no strata given")
    measured = [s for s in strata if s.n_plots > 0]
    unmeasured = [s for s in strata if s.n_plots == 0 and s.area_ha > 0]
    thin = [s for s in measured if s.n_plots < 2]

    total = sum(s.total_t for s in measured)
    total_area = sum(s.area_ha for s in strata)

    out = {
        "total_t": total,
        "total_area_ha": total_area,
        "mean_t_ha_overall": total / total_area if total_area else 0.0,
        "n_plots_total": sum(s.n_plots for s in measured),
        "strata": [
            {
                "name": s.name, "area_ha": s.area_ha, "n_plots": s.n_plots,
                "mean_t_ha": s.mean_t_ha, "sd_t_ha": s.sd_t_ha,
                "cv": s.cv, "total_t": s.total_t,
                "share_of_total": s.total_t / total if total else 0.0,
            } for s in strata
        ],
        "can_give_range": not thin and not unmeasured,
        "warnings": [],
    }

    for s in unmeasured:
        out["warnings"].append(
            f"'{s.name}' covers {s.area_ha:,.0f} ha but has no plots in it. "
            f"That ground is counted as zero, so the total is too low."
        )
    for s in thin:
        out["warnings"].append(
            f"'{s.name}' has only {s.n_plots} plot. Two plots minimum, or there "
            f"is no way to say how much plots in that class vary."
        )
    for s in measured:
        if s.cv is not None and s.cv > 0.5:
            out["warnings"].append(
                f"plots inside '{s.name}' disagree a lot (spread is "
                f"{s.cv:.0%} of its average). That class is probably really two "
                f"classes. Splitting it beats measuring more plots in it."
            )

    if out["can_give_range"]:
        # Standard stratified estimator: each class contributes its own variance,
        # weighted by the square of its area.
        var = sum((s.area_ha ** 2) * (s.sd_t_ha ** 2) / s.n_plots for s in measured)
        se = math.sqrt(var)
        z = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(confidence, 1.96)
        out.update({
            "se_t": se,
            "low_t": max(0.0, total - z * se),
            "high_t": total + z * se,
            "confidence": confidence,
            "relative_error": se / total if total else 0.0,
        })
    return out


def plots_needed(stratum: Stratum, target_relative_error: float = 0.15) -> int | None:
    """
    How many plots this class needs to hit a target error, judging from what
    the plots already measured say about how much it varies.

    Returns None when there are fewer than two plots, because there is nothing
    to judge from yet. Go and measure a second one.
    """
    cv = stratum.cv
    if cv is None:
        return None
    return max(2, math.ceil((cv / target_relative_error) ** 2))


def from_grid(
    cells: list[dict],
    plot_results: dict[str, list[float]],
) -> list[Stratum]:
    """
    Build strata from a grid assessment sheet plus the measured plots.

    cells
        One dict per grid cell, from the grid sheet the field team fills in:
        {"cell_id": "G-01", "density_class": "thick", "area_ha": 12.5}

    plot_results
        {density_class: [t_per_ha from each plot measured in that class]}
    """
    by_class: dict[str, float] = {}
    for c in cells:
        k = str(c.get("density_class", "")).strip().lower()
        a = c.get("area_ha")
        if not k or k == "none" or not isinstance(a, (int, float)):
            continue
        by_class[k] = by_class.get(k, 0.0) + float(a)
    for k in plot_results:
        by_class.setdefault(k.lower(), 0.0)
    return [Stratum(name=k, area_ha=v, plot_t_ha=list(plot_results.get(k, [])))
            for k, v in sorted(by_class.items())]


__all__ = [
    "DENSITY_CLASSES", "HEIGHT_CLASSES", "Stratum", "estimate_total",
    "plots_needed", "from_grid", "plot_radius_m", "slope_corrected_radius_m",
]
