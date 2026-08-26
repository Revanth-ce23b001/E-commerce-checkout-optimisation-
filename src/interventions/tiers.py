"""[D] — the two tier boundaries, solved from the cost model.

Blueprint §8.3 is emphatic that the tiers are economic and not percentiles, and
it derives the HIGH line properly: p\* is where a COD order's expected margin
crosses zero. It then gives the LOW/MED line as **0.10** and justifies it with
"COD order EV ≈ +₹65" — which is a readout of the line, not a derivation of it.
₹65 has no economic meaning; 0.10 was chosen and ₹65 followed.

So the LOW line is solved here instead, on a question that does have an answer:

> **At what predicted RTO probability does it first become possible for a paid
> intervention to create value?**

§6.6 already frames it — *"how much can we afford to pay to convert a COD order
to prepaid? Up to ₹87.6."* That affordable spend is a function of p. Below some
p it is smaller than the cheapest paid lever in the library, and below that point
no incentive can pay for itself **even with perfect targeting, zero leakage and a
100% switch rate**. That is a real floor, not a preference: it is the p at which
the LOW tier's defining property — *"any friction here is pure loss"* — extends
to any spend as well as any friction.

The anchor is §10.2's ₹30 incentive. It is a lever parameter, so the boundary
moves with it, and ``solve`` reports the boundary across §10.1's stated ₹25–50
range rather than pretending one number falls out of nowhere.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

GRID = np.linspace(0.001, 0.60, 1200)


def affordable_curve(econ, effect, grid=GRID) -> pd.DataFrame:
    """Mean affordable COD→prepaid spend, as a function of predicted RTO risk.

    Each order keeps its own cost structure and its own month-end status; only
    the assumed COD probability is swept. The prepaid counterpart of each point
    is the planted counterfactual, so the curve prices the *causal* benefit of a
    switch rather than the observed rate gap.
    """
    beta = effect.beta
    acquired = beta["is_cod"] + beta["month_end_x_cod"] * effect.is_month_end
    rows = []
    for p in grid:
        logit_p = np.log(p / (1.0 - p))
        p_cod = np.full(econ.n, float(p))
        p_pre = 1.0 / (1.0 + np.exp(-(logit_p - acquired)))
        rows.append({
            "p_cod": float(p),
            "p_prepaid": float(p_pre.mean()),
            "affordable_spend": float(
                econ.max_affordable_switch_spend(p_cod, p_pre).mean()),
            "ev_cod": float(econ.expected(p_cod, np.ones(econ.n, bool)).mean()),
        })
    return pd.DataFrame(rows)


def _cross(curve: pd.DataFrame, column: str, level: float) -> float:
    """First p at which ``column`` crosses ``level`` from below. Linear interp."""
    x = curve["p_cod"].to_numpy()
    y = curve[column].to_numpy()
    above = np.flatnonzero(y >= level)
    if above.size == 0 or above[0] == 0:
        return float("nan")
    i = above[0]
    span = y[i] - y[i - 1]
    if span == 0:
        return float(x[i])
    return float(x[i - 1] + (level - y[i - 1]) / span * (x[i] - x[i - 1]))


def solve(econ, effect, config: dict, pstar: float) -> dict:
    """The boundary set, plus the sensitivity of the derived one to its anchor."""
    curve = affordable_curve(econ, effect)
    anchor = float(config["carrot_anchor_rupees"])
    carrot = _cross(curve, "affordable_spend", anchor)

    sweep = pd.DataFrame([
        {"incentive_rupees": float(v),
         "low_med_boundary": round(_cross(curve, "affordable_spend", float(v)), 4)}
        for v in config["carrot_anchor_sweep"]
    ])

    # p* solved independently as a check: the p at which a COD order's expected
    # margin crosses zero, on THIS population's realised costs rather than on
    # the 1,000-rupee exemplar. It should land on the truth file's derived value.
    pstar_rederived = _cross(
        curve.assign(neg_ev=-curve["ev_cod"]), "neg_ev", 0.0)

    return {
        "curve": curve,
        "low_med": carrot,
        "med_high": float(pstar),
        "anchor_rupees": anchor,
        "anchor_sweep": sweep,
        "blueprint_med_floor": float(config["blueprint_med_floor"]),
        "pstar_rederived_on_population": pstar_rederived,
    }


def assign(score: np.ndarray, low_med: float, med_high: float) -> pd.Series:
    """LOW / MED / HIGH against the two derived lines. Never percentiles."""
    score = np.asarray(score, dtype=float)
    return pd.Series(np.where(score >= med_high, "HIGH",
                              np.where(score >= low_med, "MED", "LOW")))


def describe(tiers: pd.Series, frame: pd.DataFrame, boundaries: dict) -> pd.DataFrame:
    """What each tier actually contains, once the lines are drawn."""
    cod = (frame["payment_method"] == "COD").to_numpy()
    rows = []
    definition = {
        "LOW": "p < {:.4f}".format(boundaries["low_med"]),
        "MED": "{:.4f} <= p < {:.4f}".format(boundaries["low_med"],
                                             boundaries["med_high"]),
        "HIGH": "p >= {:.4f}  (p*)".format(boundaries["med_high"]),
    }
    for tier in ("LOW", "MED", "HIGH"):
        mask = (tiers == tier).to_numpy()
        rows.append({
            "tier": tier,
            "definition": definition[tier],
            "orders": int(mask.sum()),
            "share_pct": round(float(mask.mean()) * 100, 2),
            "mean_score": round(float(frame.loc[mask, "m2_score"].mean()), 4),
            "realised_rto": round(float(frame.loc[mask, "rto_flag"].mean()), 4),
            "cod_share": round(float(cod[mask].mean()), 4),
            "realised_cm_per_order": round(
                float(frame.loc[mask, "contribution_margin"].mean()), 2),
        })
    return pd.DataFrame(rows)
