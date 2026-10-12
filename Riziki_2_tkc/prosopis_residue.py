"""
Prosopis juliflora residue estimation, from field measurements to biochar carbon.

The chain this module implements:

    D30 per stem  ->  dry weight per stem  ->  tree  ->  plot  ->  per hectare
                  ->  harvestable  ->  surplus  ->  economically viable residue
                  ->  biochar mass  ->  CO2e

ALLOMETRY SOURCE
    Kariuki, Muturi & Choge (KEFRI), recommended in:
    KEFRI (2020), "Unlocking the Economic Potentials of Prosopis in the Face of
    Changing Climate", Proceedings of the 2nd National Prosopis Management
    Workshop, Lake Baringo, May 2015. ISBN 978-9914-723-94-6.

        ln(DW kg)        = 0.2933 * D30 - 0.03     R2 = 0.92
        ln(FW kg)        = 0.2920 * D30 + 0.59     R2 = 0.94
        ln(V m3 * 1000)  = 0.3025 * D30 + 0.32     R2 = 0.92

    Calibration: 45 stems from 30 trees at Nadapal (Turkwel riverine forest),
    D30 range 2.5-18.0 cm, felled and oven-dried at 105 C to constant weight.
    Validated at Marigat (Baringo) and Bura.

TWO THINGS THIS MODULE REFUSES TO DO QUIETLY
    1. Extrapolate past 18.0 cm D30. The model is exponential in diameter, not
       a power law, so it does not degrade gracefully outside its calibration
       range -- it explodes. 18 cm predicts ~190 kg; 25 cm predicts ~1,480 kg
       for a single stem. Out-of-range stems raise, or are flagged and excluded.
    2. Hide the log back-transformation bias. exp() of a log-model prediction
       gives the MEDIAN, not the mean, and underestimates total biomass.
       Correcting it needs the model's residual standard error, which the
       published summary does not report. Until Riziki obtains it from KEFRI or
       refits on the original data, every total carries `se_known=False`.

UNITS
    D30, DBH          centimetres
    stem/tree weight  kilograms, oven-dry
    plot, hectare     tonnes (Mg)
    areas             hectares
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# --- Allometric coefficients (KEFRI; see module docstring) -------------------

DW_SLOPE, DW_INTERCEPT = 0.2933, -0.03
FW_SLOPE, FW_INTERCEPT = 0.2920, 0.59
VOL_SLOPE, VOL_INTERCEPT = 0.3025, 0.32

# Calibration range of the source study. Hard limits, not suggestions.
D30_MIN_CM, D30_MAX_CM = 2.5, 18.0

# Stoichiometric: elemental carbon mass -> carbon dioxide equivalent. Fixed.
CO2_PER_C = 44.0 / 12.0

# Economically viable fraction of surplus residue in Kenya, from Sarfo et al.,
# "Availability and spatial distribution of crop and forest biomass residues
# for biochar production in Kenya", Scientific Reports (2026).
# Riziki should replace these with their own haulage economics.
EVF_LOW, EVF_HIGH = 0.48, 0.59

# --- Published power laws for stems ABOVE the KEFRI ceiling ------------------
#
# Both take COLLAR diameter (at ground level), not D30. Both are power laws,
# which extrapolate far more safely than KEFRI's exponential form.
#
# CHOJNACKY: Chojnacky, Heath & Jenkins (2014), "Updated generalized biomass
#   equations for North American tree species", Forestry 87:129-151.
#   ln(AGB kg) = 2.426 * ln(Dcollar) - 2.9255,  applicable 3-60 cm.
#   Prosopis sits in a combined woodland Fabaceae/Rosaceae group (R2 = 0.89,
#   residuals reaching about +70%), fitted largely on North American data.
#   Applied to P. juliflora directly by Dastranj et al. (2025), Sci Rep.
# WEST: West et al. modified semi-empirical model as reported in the same
#   2025 study.  AGB kg = 0.0295 * Dcollar ** 2.67
#
CHOJNACKY = (2.426, -2.9255)
WEST = (0.0295, 2.67)
CHOJNACKY_MAX_CM = 60.0


class OutOfCalibrationRange(ValueError):
    """Raised when a D30 falls outside the range the equation was fitted on."""


# --- Stem level --------------------------------------------------------------

def _smearing_factor(residual_se: float | None) -> float:
    """
    Baskerville (1972) correction for back-transforming a log-linear model.

    E[Y] = exp(a + b*x) * exp(sigma^2 / 2)

    Without sigma you get the conditional median, which is biased low.
    Returns 1.0 (no correction) when the residual SE is unknown.
    """
    if residual_se is None:
        return 1.0
    if residual_se < 0:
        raise ValueError("residual_se must be non-negative")
    return math.exp(residual_se ** 2 / 2.0)


def stem_dry_weight_kg(
    d30_cm: float,
    residual_se: float | None = None,
    strict: bool = True,
) -> float:
    """
    Oven-dry weight of ONE stem from its diameter 30 cm above ground.

    d30_cm      : stem diameter at 30 cm above the soil surface, in cm
    residual_se : residual standard error of the fitted log model, if known.
                  Supplying it applies the back-transformation correction.
    strict      : True raises on out-of-range input; False returns math.nan.

    A stem is counted separately when it divides from its neighbours at or
    below 30 cm. Anything branching above 30 cm belongs to the stem it grows
    from and is not measured separately.
    """
    if d30_cm <= 0:
        raise ValueError(f"d30_cm must be positive, got {d30_cm}")
    if not (D30_MIN_CM <= d30_cm <= D30_MAX_CM):
        if strict:
            raise OutOfCalibrationRange(
                f"D30 = {d30_cm} cm is outside the calibration range "
                f"{D30_MIN_CM}-{D30_MAX_CM} cm. This equation is exponential in "
                f"diameter and extrapolates catastrophically. Record and flag "
                f"this stem for separate treatment, or pass strict=False."
            )
        return math.nan
    return math.exp(DW_SLOPE * d30_cm + DW_INTERCEPT) * _smearing_factor(residual_se)


def stem_fresh_weight_kg(d30_cm: float, residual_se: float | None = None,
                         strict: bool = True) -> float:
    """Fresh weight of one stem. Useful as a same-day field cross-check."""
    if not (D30_MIN_CM <= d30_cm <= D30_MAX_CM):
        if strict:
            raise OutOfCalibrationRange(f"D30 = {d30_cm} cm out of range.")
        return math.nan
    return math.exp(FW_SLOPE * d30_cm + FW_INTERCEPT) * _smearing_factor(residual_se)


def stem_volume_m3(d30_cm: float, residual_se: float | None = None,
                   strict: bool = True) -> float:
    """Stem volume in cubic metres. Only needed if wood is sold by measure."""
    if not (D30_MIN_CM <= d30_cm <= D30_MAX_CM):
        if strict:
            raise OutOfCalibrationRange(f"D30 = {d30_cm} cm out of range.")
        return math.nan
    return math.exp(VOL_SLOPE * d30_cm + VOL_INTERCEPT) * _smearing_factor(residual_se) / 1000.0


# --- Stems above the KEFRI ceiling -------------------------------------------

def oversize_stem_bracket(collar_cm: float) -> dict:
    """
    A BRACKETED estimate for a stem too thick for the KEFRI equation.

    Returns a low-high range from two independent published power laws rather
    than a single number, because no Kenyan equation covers this range and the
    two canonical ones disagree by roughly 1.8x throughout it. Report the range.
    Never collapse it to a midpoint and present that as a measurement.

    collar_cm
        Diameter at GROUND LEVEL (root collar), not D30. This matters: stems
        taper, so collar diameter exceeds D30 on the same stem. Passing a D30
        value here biases the estimate LOW, which is the safe direction but is
        still an error. Riziki can measure the conversion for nothing during
        harvest by recording both on every felled stem.

    Why bracket rather than refuse: excluding thick stems is defensible in a
    dense thicket, where about 1% of stems exceed 18 cm. In a mature riverine
    stand roughly a fifth of stems do, and they carry about half the biomass.
    Silently dropping half the feedstock is worse than reporting a wide range.
    """
    if collar_cm <= 0:
        raise ValueError(f"collar_cm must be positive, got {collar_cm}")
    b1, b0 = CHOJNACKY
    chojnacky = math.exp(b1 * math.log(collar_cm) + b0)
    a, b = WEST
    west = a * collar_cm ** b
    lo, hi = sorted((chojnacky, west))
    return {
        "low_kg": lo,
        "high_kg": hi,
        "spread": hi / lo,
        "chojnacky_kg": chojnacky,
        "west_kg": west,
        "beyond_published_range": collar_cm > CHOJNACKY_MAX_CM,
        "basis": "collar diameter, two published power laws, NOT a Kenyan fit",
    }


# --- The friendly front door: always answers ---------------------------------
#
# Three confidence labels, by how much evidence stands behind the number:
#
#   "almost precisely"  2.5-18.0 cm. KEFRI, fitted on real Kenyan P. juliflora
#                       that was felled and oven-dried. Right species, right
#                       country, measured the same way the field sheet does.
#   "best estimate"     18.0-60.0 cm. USDA Chojnacky et al. (2014), a published
#                       power law covering 3-60 cm, applied to P. juliflora by
#                       Dastranj et al. (2025). Published, but not Kenyan, and
#                       it wants collar diameter where we are handing it D30.
#   "educated guess"    Outside both. The same power laws, pushed past the range
#                       anyone has published. Honest shape, no evidence. Fair
#                       shot, not a measurement.
#
LABEL_PRECISE = "almost precisely"
LABEL_BEST = "best estimate"
LABEL_GUESS = "educated guess"


def stem_estimate(d_cm: float, residual_se: float | None = None) -> dict:
    """
    Dry weight of one stem, for ANY diameter. Always returns a number.

    Enter the diameter you measured. The function picks the best available
    equation for that size and tells you how much to trust the answer.

    Returns a dict with:
        kg      the number to use
        low     bottom of the range (same as kg inside the KEFRI band)
        high    top of the range
        label   "almost precisely" | "best estimate" | "educated guess"
        source  which equation answered
        why     one line on what that label means here
    """
    if d_cm <= 0:
        raise ValueError(
            f"diameter must be a positive number, got {d_cm}. "
            "This looks like a typo rather than an unusual stem."
        )

    if D30_MIN_CM <= d_cm <= D30_MAX_CM:
        kg = stem_dry_weight_kg(d_cm, residual_se=residual_se, strict=True)
        out = {
            "kg": kg, "low": kg, "high": kg,
            "label": LABEL_PRECISE,
            "source": "KEFRI (2020), Kenyan P. juliflora, felled and oven-dried",
            "why": "inside the range the equation was actually fitted on",
        }
        # Near the top of the KEFRI band its exponential form has run away from
        # the published power laws, and there were few calibration stems up
        # there to hold it down. Surface the disagreement instead of hiding it.
        if d_cm > 12.0:
            b = oversize_stem_bracket(d_cm)
            alt = (b["low_kg"] + b["high_kg"]) / 2
            out["cross_check_kg"] = alt
            out["cross_check_ratio"] = kg / alt
            out["why"] += (f"; but the USDA power laws put this stem nearer "
                           f"{alt:,.0f} kg, so treat {kg:,.0f} kg as the high end")
        return out

    b = oversize_stem_bracket(d_cm)
    kg = (b["low_kg"] + b["high_kg"]) / 2
    if not b["beyond_published_range"] and d_cm > D30_MAX_CM:
        return {
            "kg": kg, "low": b["low_kg"], "high": b["high_kg"],
            "label": LABEL_BEST,
            "source": "USDA Chojnacky (2014) and West, averaged",
            "why": "published equations cover this size, but not from Kenya",
        }
    return {
        "kg": kg, "low": b["low_kg"], "high": b["high_kg"],
        "label": LABEL_GUESS,
        "source": "USDA Chojnacky (2014) and West, pushed past their range",
        "why": ("no published equation covers this size; the shape is right "
                "but nothing has been measured out here"),
    }


def tree_estimate(d30_list: list[float], residual_se: float | None = None) -> dict:
    """
    Whole plant, for ANY mix of stem sizes. Always returns a number.

    Takes the weakest label among its stems, because a total is only as
    trustworthy as the shakiest number inside it.
    """
    stems = [stem_estimate(d, residual_se=residual_se) for d in d30_list]
    order = [LABEL_PRECISE, LABEL_BEST, LABEL_GUESS]
    worst = max((order.index(s["label"]) for s in stems), default=0)
    return {
        "kg": sum(s["kg"] for s in stems),
        "low": sum(s["low"] for s in stems),
        "high": sum(s["high"] for s in stems),
        "label": order[worst],
        "stems": stems,
        "n_stems": len(stems),
    }


# --- Tree level --------------------------------------------------------------

@dataclass
class TreeResult:
    """Result of summing one multi-stemmed plant."""
    dry_weight_kg: float
    stems_used: int
    stems_flagged: list[float] = field(default_factory=list)
    se_known: bool = False
    # Set only when oversize stems were bracketed rather than excluded.
    low_kg: float | None = None
    high_kg: float | None = None
    bracketed_stems: list[float] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """False when any stem was excluded, so the total is a lower bound."""
        return not self.stems_flagged

    def __str__(self) -> str:
        if self.low_kg is not None:
            s = (f"{self.low_kg:,.1f} to {self.high_kg:,.1f} kg oven-dry from "
                 f"{self.stems_used} stem(s)")
            if self.bracketed_stems:
                s += (f"; {len(self.bracketed_stems)} stem(s) over 18 cm valued "
                      f"by published power laws, not by the Kenyan equation")
            if not self.se_known:
                s += "; no back-transform correction (residual SE unknown)"
            return s
        s = f"{self.dry_weight_kg:,.1f} kg oven-dry from {self.stems_used} stem(s)"
        if self.stems_flagged:
            s += (f"; {len(self.stems_flagged)} stem(s) EXCLUDED as out of range "
                  f"({', '.join(f'{d} cm' for d in self.stems_flagged)}) "
                  f"-- total is a LOWER BOUND")
        if not self.se_known:
            s += "; no back-transform correction (residual SE unknown)"
        return s


def tree_dry_weight(
    d30_list: list[float],
    residual_se: float | None = None,
    bracket_oversize: bool = False,
) -> TreeResult:
    """
    Total oven-dry weight of one multi-stemmed plant.

    Pass EVERY stem measured at 30 cm.

    bracket_oversize = False (default)
        Stems over 18 cm are excluded and listed in `stems_flagged`. The total
        is an explicit lower bound. Right for dense thicket, where thick stems
        are rare.

    bracket_oversize = True
        Stems over 18 cm are valued with `oversize_stem_bracket`, and the
        result carries `low_kg` and `high_kg` as well as a central figure.
        Right for mature stands, where excluding them would discard most of the
        biomass. The returned range is wide on purpose: report it as a range.
    """
    total, used, flagged = 0.0, 0, []
    over_lo = over_hi = 0.0
    for d in d30_list:
        try:
            total += stem_dry_weight_kg(d, residual_se=residual_se, strict=True)
            used += 1
        except OutOfCalibrationRange:
            flagged.append(d)
            if bracket_oversize:
                b = oversize_stem_bracket(d)
                over_lo += b["low_kg"]
                over_hi += b["high_kg"]
                used += 1
    r = TreeResult(total + (over_lo + over_hi) / 2 if bracket_oversize else total,
                   used, [] if bracket_oversize else flagged,
                   se_known=residual_se is not None)
    if bracket_oversize:
        r.low_kg = total + over_lo
        r.high_kg = total + over_hi
        r.bracketed_stems = flagged
    return r


# --- Plot level --------------------------------------------------------------

def plot_biomass_t_per_ha(
    trees: list[list[float]],
    plot_area_ha: float = 0.1,
    residual_se: float | None = None,
) -> dict:
    """
    Scale a measured plot to tonnes of oven-dry standing biomass per hectare.

    trees        : one list of stem D30 values per plant in the plot
    plot_area_ha : 0.1 ha is the KEFRI national inventory plot
                   (a circle of 17.84 m radius)

    Returns the per-hectare figure plus the counts an auditor will ask for.
    """
    if plot_area_ha <= 0:
        raise ValueError("plot_area_ha must be positive")
    results = [tree_dry_weight(t, residual_se=residual_se) for t in trees]
    total_kg = sum(r.dry_weight_kg for r in results)
    flagged = [d for r in results for d in r.stems_flagged]
    return {
        "t_per_ha": total_kg / 1000.0 / plot_area_ha,
        "plot_total_kg": total_kg,
        "n_trees": len(trees),
        "n_stems_used": sum(r.stems_used for r in results),
        "n_stems_flagged": len(flagged),
        "flagged_d30": flagged,
        "stems_per_ha": sum(r.stems_used for r in results) / plot_area_ha,
        "se_known": residual_se is not None,
        "lower_bound": bool(flagged),
    }


# --- Standing biomass to recoverable residue ---------------------------------

def residue_cascade(
    gross_t: float,
    harvest_fraction: float,
    surplus_available_factor: float,
    economic_viable_factor: float,
) -> dict:
    """
    Gross standing biomass -> residue actually available to Riziki.

    Each factor removes a different slice, and NONE has a default, because
    every one of them is Riziki's to measure rather than cite:

    harvest_fraction
        Of the standing stems, the share crews physically cut and haul out.
        Losses: stems too small to bother with, terrain, thicket density.

    surplus_available_factor (SAF)
        The share NOT already claimed by someone else. In Baringo this is the
        commercially important one and it is not close to 1.0: KEFRI recorded
        Charcoal Producers Associations moving hundreds of thousands of bags a
        year, with community income in the hundreds of millions of shillings.
        Prosopis there is an existing livelihood, not an unclaimed resource.

    economic_viable_factor (EVF)
        The share worth collecting once haulage distance is priced in.
        Sarfo et al. (2026) use 0.48-0.59 for Kenya; see EVF_LOW / EVF_HIGH.
    """
    for name, v in (
        ("harvest_fraction", harvest_fraction),
        ("surplus_available_factor", surplus_available_factor),
        ("economic_viable_factor", economic_viable_factor),
    ):
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1, got {v}")
    harvestable = gross_t * harvest_fraction
    surplus = harvestable * surplus_available_factor
    viable = surplus * economic_viable_factor
    return {
        "gross_t": gross_t,
        "harvestable_t": harvestable,
        "surplus_t": surplus,
        "recoverable_residue_t": viable,
        "overall_fraction": viable / gross_t if gross_t else 0.0,
    }


@dataclass
class KilnProfile:
    """
    One kiln, running one feedstock class, characterised from real batches.

    Riziki's kilns are their own design, so no published yield applies to them.
    This is the object that replaces the placeholders once batches are weighed.
    Keep one profile per kiln per feedstock class: larger pieces pyrolyse more
    slowly, which tends to RAISE char yield and can LOWER char carbon content,
    so thick mesquite stems and chipped material do not share a profile.
    """
    name: str
    char_mass_yield: float
    char_carbon_fraction: float
    h_c_organic: float | None = None
    feedstock_class: str | None = None
    n_batches: int = 0
    yield_low: float | None = None
    yield_high: float | None = None
    carbon_low: float | None = None
    carbon_high: float | None = None

    @property
    def puro_eligible(self) -> bool | None:
        """
        H/Corg strictly below 0.70 is Puro's threshold for the 200+ year
        durability claim. None means it has not been measured yet.
        """
        if self.h_c_organic is None:
            return None
        return self.h_c_organic < 0.70

    def __str__(self) -> str:
        s = f"{self.name}"
        if self.feedstock_class:
            s += f" [{self.feedstock_class}]"
        s += (f": yield {self.char_mass_yield:.1%}, "
              f"carbon {self.char_carbon_fraction:.1%}")
        if self.yield_low is not None:
            s += f" (yield ranged {self.yield_low:.1%}-{self.yield_high:.1%})"
        if self.n_batches:
            s += f", from {self.n_batches} batch(es)"
        e = self.puro_eligible
        if e is None:
            s += "; H/Corg NOT MEASURED, permanence class unknown"
        elif e:
            s += f"; H/Corg {self.h_c_organic:.2f}, under the 0.70 threshold"
        else:
            s += (f"; H/Corg {self.h_c_organic:.2f} FAILS the 0.70 threshold "
                  f"-- would not qualify as durable removal")
        return s


def fit_kiln_profile(
    name: str,
    batches: list[dict],
    feedstock_class: str | None = None,
) -> KilnProfile:
    """
    Build a kiln profile from weighed batches. This is the inverse of guessing.

    batches: one dict per run, each with
        dry_feedstock_t   dry mass charged, tonnes
        char_t            char recovered, tonnes
        carbon_fraction   optional, from lab assay of that batch
        h_c_organic       optional, from lab assay of that batch

    Moisture matters: charge mass must be converted to DRY mass first, or the
    yield comes out low and the error is invisible.
    """
    if not batches:
        raise ValueError("need at least one weighed batch to fit a profile")
    ys = []
    for i, b in enumerate(batches):
        f, c = b.get("dry_feedstock_t"), b.get("char_t")
        if not f or f <= 0 or c is None or c < 0:
            raise ValueError(f"batch {i}: need positive dry_feedstock_t and char_t")
        ys.append(c / f)
    cs = [b["carbon_fraction"] for b in batches if b.get("carbon_fraction") is not None]
    hs = [b["h_c_organic"] for b in batches if b.get("h_c_organic") is not None]
    return KilnProfile(
        name=name,
        char_mass_yield=sum(ys) / len(ys),
        char_carbon_fraction=sum(cs) / len(cs) if cs else float("nan"),
        h_c_organic=max(hs) if hs else None,   # worst case governs eligibility
        feedstock_class=feedstock_class,
        n_batches=len(batches),
        yield_low=min(ys), yield_high=max(ys),
        carbon_low=min(cs) if cs else None,
        carbon_high=max(cs) if cs else None,
    )


def biochar_and_co2e(
    residue_t: float,
    char_mass_yield: float | None = None,
    char_carbon_fraction: float | None = None,
    permanence_factor: float = 1.0,
    profile: KilnProfile | None = None,
) -> dict:
    """
    Recoverable residue -> biochar -> CO2e.

    char_mass_yield
        Dry feedstock mass in, char mass out. MEASURE THIS ON RIZIKI'S OWN
        KILNS. Published charcoal figures (traditional earth kiln 0.10-0.20,
        improved earth kiln ~0.30, Casamance 0.32-0.38) are an order-of-
        magnitude anchor only: charcoal for fuel and biochar for carbon removal
        are different processes with different targets.

    char_carbon_fraction
        Carbon content of the char, by laboratory assay of actual output.

    permanence_factor
        The share of char carbon a methodology credits as durable. Driven by
        the H/C organic molar ratio, which is the single most consequential lab
        number in the whole chain. Left at 1.0 until Riziki's target registry
        and their own H/C data set it.

    profile
        A KilnProfile fitted from weighed batches, used INSTEAD of the two loose
        parameters. This is what the call should look like once Riziki has run
        their own kilns, and the yield range travels with the answer.
    """
    if profile is not None:
        char_mass_yield = profile.char_mass_yield
        char_carbon_fraction = profile.char_carbon_fraction
    if char_mass_yield is None or char_carbon_fraction is None:
        raise ValueError(
            "supply either a fitted KilnProfile or both char_mass_yield and "
            "char_carbon_fraction. There is no sensible default: no published "
            "figure describes Riziki's own kilns."
        )
    for name, v in (
        ("char_mass_yield", char_mass_yield),
        ("char_carbon_fraction", char_carbon_fraction),
        ("permanence_factor", permanence_factor),
    ):
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1, got {v}")
    char_t = residue_t * char_mass_yield
    carbon_t = char_t * char_carbon_fraction
    out = {
        "residue_t": residue_t,
        "biochar_t": char_t,
        "carbon_t": carbon_t,
        "co2e_t": carbon_t * CO2_PER_C * permanence_factor,
        "permanence_applied": permanence_factor,
    }
    if profile is not None:
        out["kiln"] = profile.name
        if profile.yield_low is not None:
            out["co2e_low_t"] = (residue_t * profile.yield_low
                                 * char_carbon_fraction * CO2_PER_C * permanence_factor)
            out["co2e_high_t"] = (residue_t * profile.yield_high
                                  * char_carbon_fraction * CO2_PER_C * permanence_factor)
        if profile.puro_eligible is False:
            out["warning"] = (f"H/Corg {profile.h_c_organic:.2f} is at or above "
                              f"0.70, so this char would not qualify for a "
                              f"durable-removal claim under Puro.")
        elif profile.puro_eligible is None:
            out["warning"] = ("H/Corg not measured, so the permanence class is "
                              "unknown and this CO2e figure is not yet sellable.")
    return out


# --- Whole chain -------------------------------------------------------------

def estimate_area(
    trees: list[list[float]],
    area_ha: float,
    harvest_fraction: float,
    surplus_available_factor: float,
    economic_viable_factor: float,
    plot_area_ha: float = 0.1,
    residual_se: float | None = None,
    char_mass_yield: float | None = None,
    char_carbon_fraction: float | None = None,
    permanence_factor: float = 1.0,
) -> dict:
    """
    One measured plot, scaled to a mapped area, all the way through.

    Stops at recoverable residue unless the two kiln parameters are supplied,
    so a CO2e figure can never appear by accident.
    """
    plot = plot_biomass_t_per_ha(trees, plot_area_ha, residual_se)
    gross_t = plot["t_per_ha"] * area_ha
    cascade = residue_cascade(
        gross_t, harvest_fraction, surplus_available_factor, economic_viable_factor
    )
    out = {"plot": plot, "area_ha": area_ha, **cascade}
    if char_mass_yield is not None and char_carbon_fraction is not None:
        out["carbon"] = biochar_and_co2e(
            cascade["recoverable_residue_t"],
            char_mass_yield,
            char_carbon_fraction,
            permanence_factor,
        )
    out["caveats"] = _caveats(plot)
    return out


def _caveats(plot: dict) -> list[str]:
    notes = []
    if not plot["se_known"]:
        notes.append(
            "No back-transform correction applied: the residual standard error "
            "of the KEFRI model is unpublished, so totals are median-based and "
            "biased LOW. Obtain it from KEFRI or refit before commercial use."
        )
    if plot["lower_bound"]:
        notes.append(
            f"{plot['n_stems_flagged']} stem(s) exceeded the 18.0 cm calibration "
            f"ceiling and were excluded. The total is a lower bound."
        )
    notes.append(
        "Single-plot extrapolation carries no confidence interval. Stratify and "
        "replicate plots before quoting an area figure."
    )
    return notes


__all__ = [
    "stem_dry_weight_kg", "stem_fresh_weight_kg", "stem_volume_m3",
    "KilnProfile", "fit_kiln_profile",
    "stem_estimate", "tree_estimate",
    "LABEL_PRECISE", "LABEL_BEST", "LABEL_GUESS",
    "oversize_stem_bracket", "tree_dry_weight", "TreeResult",
    "plot_biomass_t_per_ha", "residue_cascade", "biochar_and_co2e",
    "estimate_area", "OutOfCalibrationRange", "D30_MIN_CM", "D30_MAX_CM",
    "CHOJNACKY", "WEST", "CHOJNACKY_MAX_CM",
    "EVF_LOW", "EVF_HIGH", "CO2_PER_C",
]
