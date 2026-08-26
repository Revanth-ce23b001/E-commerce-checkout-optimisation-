"""Sweep the two assumptions the answer actually turns on.

Everything economic in this phase is derived. Everything behavioural is assumed.
Two of the assumptions carry most of the variance:

1. **The COD-to-prepaid switch rate.** Every carrot's benefit is proportional to
   it, and it is the entire mechanism of G.
2. **The abandonment rate under a fee.** It sets the sign of the fee levers,
   because §10.2's insight cuts both ways: above p-star a lost order is a
   *saving*, below p-star it is a straight loss. A fee applied across a mixed
   population wins or loses on which side of p-star its abandonment lands.

Two things are reported, and they are different questions.

``surface``   ΔCM/session across the 2-D grid, for one lever at one depth. This
              answers "is this lever robust", and where it crosses §12.1's ship
              bar of ₹1.50.
``crossover`` targeted ΔCM minus flat ΔCM across the same grid. This answers
              "does targeting still earn its keep", which is H10, and it is the
              one that can come back negative.

A note on how the abandonment sweep is anchored. §10.1's prior is −5 to −10%
relative in the targeted cohort. The DGP carries a second, independent anchor
nobody has used: ``conversion_model.shipping_fee_charged_gt0 = -0.45``, a planted
conversion penalty for a fee appearing at checkout that never fired, because
``params.yaml`` sets ``shipping_fee_charged: 0``. Converted at the window's own
conversion rate it is a much larger effect than the Phase 1 prior, and it is
marked on the sweep rather than substituted for it: it was planted for a
*shipping* fee and nobody calibrated it against ₹39 of COD fee.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import library, simulate

SHIP_BAR = 1.50   # blueprint §12.1, ΔCM per session


def dgp_fee_anchor(conversion: float, logit_shift: float) -> float:
    """The planted fee penalty, expressed as a relative conversion loss.

    ``conversion`` is the window's realised checkout conversion. Shifting its
    logit by ``logit_shift`` gives the conversion a fee-bearing checkout would
    have had, and the relative drop is the abandonment rate the DGP implies.
    """
    conversion = float(np.clip(conversion, 1e-9, 1 - 1e-9))
    logit = np.log(conversion / (1 - conversion))
    after = 1.0 / (1.0 + np.exp(-(logit + logit_shift)))
    return float((conversion - after) / conversion)


def _point(sim, lever, frame, tier, mask, share, switch, abandon):
    resp = library.response(lever, share, "central")
    resp["switch"] = float(switch)
    resp["abandon"] = float(abandon)
    if lever.id == "G":
        resp["abandon"] = float(1.0 - switch)
    if resp["switch"] + resp["abandon"] > 1.0:
        resp["abandon"] = max(0.0, 1.0 - resp["switch"])
    result = sim.run(mask, library.switchable(lever, frame), resp)
    summary = simulate.summarise(sim, result, tier, "sweep")
    row = summary[summary["tier"] == "ALL"].iloc[0]
    low = summary[summary["tier"] == "LOW"].iloc[0]
    return {"d_cm_per_session": float(row["d_cm_per_session"]),
            "d_conversion_rel_pct": float(row["d_conversion_rel_pct"]),
            "low_conv_rel_pct": float(low["d_conversion_rel_pct"]),
            "d_rto_pp": float(row["d_rto_pp"])}


def surface(sim, lever, frame, tier, score, boundaries, cfg, depth: str,
            mask=None) -> pd.DataFrame:
    """ΔCM/session over the switch x abandonment grid, for one lever and depth."""
    grid_s = cfg["sensitivity"]["switch_share_grid"]
    grid_a = cfg["sensitivity"]["abandon_relative_grid"]
    if mask is None:
        mask = library.eligible(lever, frame, tier, score, boundaries, depth)
    share = library.pp_denominator(frame, lever, tier, score, boundaries)

    rows = []
    for s in grid_s:
        for a in grid_a:
            if s + a > 1.0:
                continue
            point = _point(sim, lever, frame, tier, mask, share, s, a)
            rows.append({"lever": lever.id, "depth": depth, "switch": s,
                         "abandon": a, **point,
                         "clears_ship_bar": point["d_cm_per_session"] >= SHIP_BAR})
    return pd.DataFrame(rows)


def crossover(targeted: pd.DataFrame, flat: pd.DataFrame) -> pd.DataFrame:
    """Where the targeted arm stops beating the comparator, cell by cell."""
    keys = ["lever", "switch", "abandon"]
    merged = targeted.merge(flat, on=keys, suffixes=("_targeted", "_flat"))
    merged["targeting_premium"] = (merged["d_cm_per_session_targeted"]
                                   - merged["d_cm_per_session_flat"]).round(4)
    merged["targeted_wins"] = merged["targeting_premium"] > 0
    return merged[keys + ["d_cm_per_session_targeted", "d_cm_per_session_flat",
                          "targeting_premium", "targeted_wins",
                          "low_conv_rel_pct_flat", "low_conv_rel_pct_targeted"]]


def breakeven_abandonment(surf: pd.DataFrame, switch: float) -> float:
    """Highest abandonment rate at which the lever still clears the ship bar."""
    block = surf[np.isclose(surf["switch"], switch)].sort_values("abandon")
    ok = block[block["d_cm_per_session"] >= SHIP_BAR]
    return float(ok["abandon"].max()) if len(ok) else float("nan")


def run(sim, levers, frame, tier, score, boundaries, cfg, cells) -> dict:
    """The sweep set the report needs. B and G only — the fee and the gate.

    A, C, E and F carry no abandonment term at all under §10.1's priors, so a
    2-D sweep of them would vary one axis against a constant. Their sensitivity
    is the switch axis alone, which the ``bands`` table already covers.
    """
    out = {}
    for key in ("B", "G"):
        lever = levers[key]
        depth = cells[key]["_best_depth"]
        t = surface(sim, lever, frame, tier, score, boundaries, cfg, depth)
        f = surface(sim, lever, frame, tier, score, boundaries, cfg, "all")
        m = surface(sim, lever, frame, tier, score, boundaries, cfg,
                    "random_matched", mask=cells[key]["random_matched"]["mask"])
        out[key] = {"depth": depth, "targeted": t, "flat": f, "matched": m,
                    "crossover": crossover(t, f),
                    "crossover_matched": crossover(t, m)}
    out["dgp_fee_anchor"] = dgp_fee_anchor(
        sim.frame.shape[0] / sim.sessions,
        float(cfg["sensitivity"]["dgp_fee_conversion_logit"]))
    return out
