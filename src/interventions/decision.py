"""The risk-based pricing decision table, and blueprint §12's guardrails.

Two things live here, and the second is the reason the first is not just a
ranking by contribution margin.

**The decision table** is tier × lever × targeting depth, carrying the three
numbers §12 says a launch decision needs — conversion, RTO and CM. A tier's
recommendation is not "run the lever with the biggest ΔCM": it is the best lever
*that clears every guardrail*, and in the LOW tier the answer should be to run
nothing at all.

**The guardrails** are blueprint §12.1, pre-committed in Phase 1 before any
model, score or simulation existed. Three of the six can be evaluated from a
simulation:

    aggregate net conversion    >= -1.0% relative
    LOW-tier conversion         >= -0.3% relative     <- the one that matters
    CM per session              >= +1.50 to ship

Three cannot, and are reported as UNTESTABLE rather than quietly passed:
statistical significance needs an experiment, maturation needs 30 days of one,
and the complaint / refund / repeat-purchase guardrails need a treated population
that has never existed. **A verdict with three of six clauses unevaluated is not
a launch decision.** It is a shortlist.

Why the LOW-tier clause is tighter than the aggregate one
---------------------------------------------------------
Because it is the thesis. §12.1: *"low-risk orders have +₹65 EV, so any loss
there is pure value destruction. The whole thesis is that we don't touch them."*
A lever that raises total CM by damaging low-risk conversion has not improved the
business; it has taxed its best customers to pay for its worst. §12.2 makes that
a KILL regardless of CM, and it is the clause that separates a targeted policy
from a flat one when both are CM-positive.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Blueprint §12.1. Written here rather than in interventions.yaml because these
# are not behavioural assumptions to sweep — they are the pre-committed launch
# criteria, and a threshold that moves during the analysis it governs is not a
# guardrail.
CM_PER_SESSION_FLOOR = 1.50
AGGREGATE_CONVERSION_FLOOR_REL = -1.0
LOW_TIER_CONVERSION_FLOOR_REL = -0.3
RTO_IMPROVEMENT_FLOOR_PP = -2.0

UNTESTABLE = (
    "95% CI excludes zero", "power >= 80%", ">=30d maturation",
    "complaint rate <= +5% rel", "refund rate <= +5% rel",
    "30-day repeat purchase >= -1.0% rel",
)

DEPTH_LABEL = {"high_only": "HIGH only", "med_high": "MED+HIGH", "all": "flat (all)",
               "random_matched": "random, volume-matched"}


def guardrails(summary: pd.DataFrame) -> dict:
    """Evaluate the three §12.1 clauses a simulation can actually evaluate."""
    total = summary[summary["tier"] == "ALL"].iloc[0]
    low = summary[summary["tier"] == "LOW"].iloc[0]
    treated_row = summary[summary["tier"] == "TREATED"]

    cm = float(total["d_cm_per_session"])
    agg_conv = float(total["d_conversion_rel_pct"])
    agg_net = float(total["d_net_conversion_rel_pct"])
    low_conv = float(low["d_conversion_rel_pct"])
    low_net = float(low["d_net_conversion_rel_pct"])
    # 12.1 states the RTO clause "in treated cohorts", so it is measured on the
    # orders the lever actually touched. Measuring it on a risk tier would dilute
    # it with the untreated orders that happen to share that tier, and a lever
    # would fail the mechanism check for being well targeted.
    rto = float(treated_row["d_rto_pp"].iloc[0]) if len(treated_row) else 0.0

    # BOTH conversion metrics must clear. §12.1's aggregate floor names *net*
    # conversion; §5.3's guardrail table names *checkout* conversion, at the
    # same -1.0%. They can move in opposite directions, so requiring only the
    # one that happens to pass would be metric-shopping. Requiring both is the
    # conservative reading and the only one that cannot be gamed after the fact.
    checks = {
        "cm_per_session": (cm >= CM_PER_SESSION_FLOOR, cm, CM_PER_SESSION_FLOOR),
        "aggregate_conversion": (agg_conv >= AGGREGATE_CONVERSION_FLOOR_REL,
                                 agg_conv, AGGREGATE_CONVERSION_FLOOR_REL),
        "aggregate_net_conversion": (agg_net >= AGGREGATE_CONVERSION_FLOOR_REL,
                                     agg_net, AGGREGATE_CONVERSION_FLOOR_REL),
        "low_tier_conversion": (low_conv >= LOW_TIER_CONVERSION_FLOOR_REL,
                                low_conv, LOW_TIER_CONVERSION_FLOOR_REL),
        "low_tier_net_conversion": (low_net >= LOW_TIER_CONVERSION_FLOOR_REL,
                                    low_net, LOW_TIER_CONVERSION_FLOOR_REL),
        "rto_moved": (rto <= RTO_IMPROVEMENT_FLOOR_PP, rto, RTO_IMPROVEMENT_FLOOR_PP),
    }
    return {"checks": checks,
            "cm_per_session": cm,
            "aggregate_conversion_rel_pct": agg_conv,
            "aggregate_net_conversion_rel_pct": agg_net,
            "low_tier_conversion_rel_pct": low_conv,
            "low_tier_net_conversion_rel_pct": low_net,
            "treated_rto_pp": rto,
            "verdict": _verdict(checks),
            "shippable": _verdict(checks).startswith("SHORTLIST")}


def _verdict(checks: dict) -> str:
    """§12.2, restricted to its evaluable clauses.

    The LOW-tier clause is checked first and on its own, because §12.2 makes it a
    KILL "regardless of CM" — a fairness failure, not a trade-off. Reading it in
    sequence with the others would let a large CM gain argue against it, which is
    exactly what pre-committing it was meant to prevent.
    """
    if not checks["low_tier_conversion"][0]:
        return "KILL (low-risk checkout conversion — fairness failure)"
    if not checks["low_tier_net_conversion"][0]:
        return "KILL (low-risk NET conversion — fairness failure)"
    if not checks["aggregate_conversion"][0]:
        return "KILL (aggregate checkout-conversion floor breached)"
    if not checks["aggregate_net_conversion"][0]:
        return "KILL (aggregate NET-conversion floor breached)"
    if not checks["cm_per_session"][0]:
        return "ITERATE (CM/session below the +1.50 ship bar)"
    if not checks["rto_moved"][0]:
        return "ITERATE (mechanism did not fire: RTO moved < 2.0pp in treated tiers)"
    return "SHORTLIST (all evaluable clauses pass; 3 of 6 need an experiment)"


def _total(cell: dict) -> pd.Series:
    summary = cell["summary"]
    return summary[summary["tier"] == "ALL"].iloc[0]


def best_depth(lever_cells: dict, guard: dict, key: str,
               blueprint_depth: str) -> str:
    """The depth to carry forward as the lever's headline configuration.

    Highest CM/session **among the depths that clear §12.1** — including ``all``,
    because if a flat rollout is the only shippable configuration then "ship
    flat" is the finding and hiding it would be the dishonest move.

    **When nothing clears, the fallback is the depth §10.1 specifies** — not the
    highest-CM depth. Picking the highest-CM depth among failing configurations
    would headline every stick lever at its flat rollout, because a flat stick
    always earns more raw margin than a targeted one, and it would sit the
    largest number in the table next to a KILL verdict. That is exactly the
    misreading §12.2 was written to prevent. Reporting the lever as its owner
    specified it is the neutral choice, and §5 prints all three depths anyway, so
    nothing is hidden by it.
    """
    depths = [d for d in ("high_only", "med_high", "all") if d in lever_cells]
    shippable = [d for d in depths if guard[(key, d)]["shippable"]]
    if shippable:
        return max(shippable,
                   key=lambda d: float(_total(lever_cells[d])["d_cm_per_session"]))
    return blueprint_depth if blueprint_depth in depths else depths[0]


def build_table(cells: dict, levers: dict, guard: dict) -> pd.DataFrame:
    """Tier × lever × depth, long form. The artefact Stage 2 consumes."""
    rows = []
    for key, by_depth in cells.items():
        for depth in ("high_only", "med_high", "all", "random_matched"):
            if depth not in by_depth:
                continue
            summary = by_depth[depth]["summary"]
            resp = by_depth[depth]["resp"]
            for _, row in summary.iterrows():
                rows.append({
                    "lever": key, "name": levers[key].name,
                    "kind": "RESTRICTIVE" if levers[key].restrictive else "offer",
                    "depth": depth, "depth_label": DEPTH_LABEL[depth],
                    "is_blueprint_depth": depth == levers[key].blueprint_depth,
                    "is_best_depth": depth == by_depth["_best_depth"],
                    "tier": row["tier"], "orders": int(row["orders"]),
                    "treated": int(row["treated"]),
                    "switch": round(resp["switch"], 4),
                    "abandon": round(resp["abandon"], 4),
                    "d_conversion_rel_pct": float(row["d_conversion_rel_pct"]),
                    "d_net_conversion_rel_pct": float(
                        row["d_net_conversion_rel_pct"]),
                    "d_cod_share_pp": float(row["d_cod_share_pp"]),
                    "d_rto_pp": float(row["d_rto_pp"]),
                    "d_cm_per_order": float(row["d_cm_per_order"]),
                    "d_cm_per_session": float(row["d_cm_per_session"]),
                    "verdict": (guard[(key, depth)]["verdict"]
                                if (key, depth) in guard else "n/a"),
                })
    return pd.DataFrame(rows)


def recommend(cells: dict, levers: dict, guard: dict, boundaries: dict) -> pd.DataFrame:
    """One recommended action per risk tier.

    A candidate must clear the §12.1 guardrails **at the whole-population level**,
    because that is where §12.1 sets them: a lever cannot be shipped on the
    strength of one tier's economics if rolling it out breaks low-risk conversion
    somewhere else. Among the candidates that clear, the tier's winner is the one
    contributing the most CM *in that tier*.
    """
    rows = []
    for tier in ("LOW", "MED", "HIGH"):
        best = None
        for key, by_depth in cells.items():
            for depth in ("high_only", "med_high", "all"):
                if not guard[(key, depth)]["shippable"]:
                    continue
                summary = by_depth[depth]["summary"]
                row = summary[summary["tier"] == tier].iloc[0]
                value = float(row["d_cm_per_session"])
                if value <= 0:
                    continue
                if best is None or value > best[2]:
                    best = (key, depth, value, int(row["treated"]),
                            float(row["d_conversion_rel_pct"]),
                            float(row["d_rto_pp"]))
        if best is None:
            rows.append({
                "tier": tier, "action": "NO INTERVENTION", "lever": "-",
                "depth": "-", "treated_in_tier": 0,
                "d_cm_per_session": 0.0, "d_conversion_rel_pct": 0.0,
                "d_rto_pp": 0.0,
                "why": "no lever clears the §12.1 guardrails with a positive CM "
                       "contribution in this tier"})
            continue
        key, depth, value, treated, conv, rto = best
        rows.append({
            "tier": tier,
            "action": "RUN {} at {}".format(key, DEPTH_LABEL[depth]),
            "lever": levers[key].name, "depth": DEPTH_LABEL[depth],
            "treated_in_tier": treated,
            "d_cm_per_session": round(value, 3),
            "d_conversion_rel_pct": round(conv, 3),
            "d_rto_pp": round(rto, 2),
            "why": "highest in-tier CM among guardrail-clearing configurations"})
    return pd.DataFrame(rows)


def targeting_premium(lever_cells: dict, lever) -> pd.DataFrame:
    """H10, stated three ways, because the three do not agree.

    * **best targeted depth vs flat** — §10.2's comparison. It confounds volume
      with selection: the flat arm treats every order the lever can act on, which
      is typically three times the targeted volume.
    * **best targeted depth vs random, volume-matched** — the same number of
      orders, chosen at random instead of by score. **This is the targeting
      premium proper**, and the only one of the three that isolates the risk
      engine's contribution.
    * **shippable** — whether each arm survives §12.1 at all.

    A lever can lose on CM against the flat arm and still be the right policy,
    because the flat arm is not shippable. Reporting only the CM column would
    make that look like the risk engine failing, when what it shows is a bigger
    intervention doing more of everything — including the damage.
    """
    best = lever_cells["_best_depth"]
    rows = []
    for depth, label in (("high_only", "targeted: HIGH only"),
                         ("med_high", "targeted: MED+HIGH"),
                         ("all", "flat: every actionable order"),
                         ("random_matched", "flat, volume-matched (random)")):
        if depth not in lever_cells:
            continue
        total = _total(lever_cells[depth])
        rows.append({
            "arm": label + ("  <- best" if depth == best else ""),
            "treated": int(total["treated"]),
            "d_cm_per_session": float(total["d_cm_per_session"]),
            "d_conversion_rel_pct": float(total["d_conversion_rel_pct"]),
            "d_rto_pp": float(total["d_rto_pp"]),
        })
    return pd.DataFrame(rows)


def geo_ratio(frame: pd.DataFrame, eligible: np.ndarray) -> float:
    """FA-01's measurement on one lever's treated set: worst tier over best.

    Re-measured per lever and per depth rather than inherited from Phase 4,
    because the eligible volume here is set by the derived risk bands rather than
    by Phase 4's 17%. A ruling that holds at one volume and not another has not
    been enforced.
    """
    rates = pd.DataFrame({"geo_tier": frame["geo_tier"].to_numpy(),
                          "flagged": np.asarray(eligible, dtype=bool)}
                         ).groupby("geo_tier")["flagged"].mean()
    best = float(rates.min())
    return float("inf") if best == 0 else float(rates.max() / best)
