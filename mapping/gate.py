"""
The status a client sees. Deliberately strict: up to 83 people may be sent on
what this says, so anything uncertain becomes SCOUT FIRST, never PLAN CREWS.

    plan     PLAN CREWS        familiar ground, clear images, region checked on the ground
    scout    SCOUT FIRST       2-3 people check ~30 points before a team goes
    cannot   CAN'T JUDGE HERE  don't send anyone on this map

Thresholds live in config (gate:). They are placeholders until set with
Riziki and Purity.
"""

from __future__ import annotations


def deploy_status(frame: str, season: int, applicability: float, valid_share: float,
                  latest_season: int, cfg: dict) -> tuple[str, list[str]]:
    g = cfg["gate"]
    pct = lambda x: f"{x:.0%}"
    if valid_share < g["min_valid_share"]:
        return "cannot", [f"Only {pct(valid_share)} of this area had clear images this season."]
    if applicability < g["scout_min_applicability"]:
        return "cannot", [f"Only {pct(applicability)} of this area looks like land the model was trained on."]

    status, reasons = "plan", []
    if applicability < g["plan_min_applicability"]:
        status = "scout"
        reasons.append(f"{pct(applicability)} of this area looks like land the model learned from "
                       f"(PLAN CREWS needs {pct(g['plan_min_applicability'])}).")
    if frame not in (g.get("validated_regions") or []):
        status = "scout"
        reasons.append("This region has not been checked on the ground yet.")
    if season < latest_season:
        status = "scout"
        reasons.append(f"This map is from {season}; the {latest_season} season is available.")
    return status, reasons
