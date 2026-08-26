"""What would have to be true for each lever to ship.

Run at the depths §10.1 specifies, **nothing in the library clears §12.1**. Every
stick breaches the aggregate conversion floor; every carrot falls short of the
+₹1.50 CM bar. "Nothing ships" is an honest answer and it is also a useless one,
because the interesting question is not *does this configuration pass* but *what
configuration would*.

Two knobs, one per kind of lever, and neither of them is a behavioural assumption:

**Restrictions have a volume.** A47 ruled on *which* orders a restriction picks;
it said nothing about how many. Volume is a free policy parameter, and the
aggregate conversion floor caps it:

    max volume ~ floor / (COD share of the restricted set x abandonment rate)

so a lever that abandons 7.5% of the COD orders it touches can be applied to at
most about a seventh of traffic before −1.0% relative conversion is breached.
That cap is a **derived product constraint**, not an assumption, and it is the
single most actionable number this phase produces. ``restriction_volume`` finds
it by search rather than by the formula above, because the §8.4 overlay and the
per-tier ranking both bend the relationship.

**Offers have a required switch rate.** They cost conversion nothing, so the
binding constraint is the ship bar. ``required_switch`` inverts it: given the
lever's economics, what switch rate would deliver ₹1.50 per session? Comparing
that to §10.1's prior says whether the lever is plausibly worth building or is
being asked to do something no UI change has ever done.

Neither routine tunes an assumption. The [A] band stays exactly where
``config/interventions.yaml`` puts it; what moves is the policy.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import decision, library, simulate

VOLUME_GRID = np.round(np.arange(0.01, 0.42, 0.01), 4)
SWITCH_GRID = np.round(np.arange(0.0, 1.001, 0.01), 4)


def _run(sim, lever, frame, tier, mask, resp):
    result = sim.run(mask, library.switchable(lever, frame), resp)
    return simulate.summarise(sim, result, tier, "feasible")


def restriction_volume(sim, lever, frame, tier, score, boundaries,
                       grid=VOLUME_GRID) -> pd.DataFrame:
    """Sweep restricted volume; report guardrails and CM at each.

    The selection rule is A47's throughout — rank within geo tier, then apply the
    §8.4 overlay — so this sweeps *how much* restriction, never *how it is
    chosen*. The ruling is not a parameter here.
    """
    share = library.pp_denominator(frame, lever, tier, score, boundaries)
    cohort = library.eligible(lever, frame, tier, score, boundaries, "all")
    rows = []
    for volume in grid:
        mask = cohort & library.restrictive_set(frame, score, float(volume))
        if not mask.any():
            continue
        resp = library.response(lever, share, "central")
        summary = _run(sim, lever, frame, tier, mask, resp)
        guard = decision.guardrails(summary)
        rows.append({
            "lever": lever.id, "volume": float(volume),
            "treated": int(mask.sum()),
            "d_cm_per_session": round(guard["cm_per_session"], 3),
            "agg_checkout_rel_pct": round(guard["aggregate_conversion_rel_pct"], 3),
            "agg_net_rel_pct": round(guard["aggregate_net_conversion_rel_pct"], 3),
            "low_checkout_rel_pct": round(guard["low_tier_conversion_rel_pct"], 3),
            "treated_rto_pp": round(guard["treated_rto_pp"], 2),
            "shippable": guard["shippable"],
            "verdict": guard["verdict"],
        })
    return pd.DataFrame(rows)


def required_switch(sim, lever, frame, tier, score, boundaries, depth,
                    target=decision.CM_PER_SESSION_FLOOR,
                    grid=SWITCH_GRID) -> dict:
    """The switch rate an offer would need to clear the ship bar at ``depth``.

    Returned alongside §10.1's own prior, so the answer reads as a ratio rather
    than a bare number: an offer needing four times its prior is a different
    proposition from one needing 1.1 times it.
    """
    share = library.pp_denominator(frame, lever, tier, score, boundaries)
    mask = library.eligible(lever, frame, tier, score, boundaries, depth)
    prior = library.response(lever, share, "central")["switch"]

    achieved, needed = [], None
    for value in grid:
        resp = library.response(lever, share, "central")
        resp["switch"] = float(value)
        resp["abandon"] = min(resp["abandon"], 1.0 - float(value))
        summary = _run(sim, lever, frame, tier, mask, resp)
        cm = float(summary[summary["tier"] == "ALL"].iloc[0]["d_cm_per_session"])
        achieved.append((float(value), cm))
        if needed is None and cm >= target:
            needed = float(value)
    best = max(cm for _v, cm in achieved)
    return {"lever": lever.id, "depth": depth, "prior_switch": round(prior, 4),
            "required_switch": needed,
            "multiple_of_prior": (None if needed is None or prior <= 0
                                  else round(needed / prior, 2)),
            "max_cm_at_switch_1.0": round(best, 3),
            "reachable": needed is not None}


def summarise_feasibility(sim, levers, frame, tier, score, boundaries,
                          cells) -> dict:
    """One shippable configuration per lever, or an explicit statement of none."""
    volumes, switches, rows = {}, {}, []
    for key, lever in levers.items():
        if lever.restrictive:
            sweep = restriction_volume(sim, lever, frame, tier, score, boundaries)
            volumes[key] = sweep
            ok = sweep[sweep["shippable"]]
            if len(ok):
                best = ok.sort_values("d_cm_per_session").iloc[-1]
                rows.append({
                    "lever": key, "name": lever.name, "kind": "RESTRICTIVE",
                    "shippable_at": "volume {:.0%}".format(best["volume"]),
                    "treated": int(best["treated"]),
                    "d_cm_per_session": float(best["d_cm_per_session"]),
                    "agg_checkout_rel_pct": float(best["agg_checkout_rel_pct"]),
                    "treated_rto_pp": float(best["treated_rto_pp"]),
                    "binding_constraint": _binding(sweep, best["volume"])})
            else:
                rows.append({
                    "lever": key, "name": lever.name, "kind": "RESTRICTIVE",
                    "shippable_at": "no volume", "treated": 0,
                    "d_cm_per_session": 0.0, "agg_checkout_rel_pct": 0.0,
                    "treated_rto_pp": 0.0,
                    "binding_constraint": str(sweep.iloc[0]["verdict"])
                    if len(sweep) else "no feasible volume"})
        else:
            depth = cells[key]["_best_depth"]
            need = required_switch(sim, lever, frame, tier, score, boundaries, depth)
            switches[key] = need
            rows.append({
                "lever": key, "name": lever.name, "kind": "offer",
                "shippable_at": ("switch {:.1%}".format(need["required_switch"])
                                 if need["reachable"] else "no switch rate"),
                "treated": int(cells[key][depth]["mask"].sum()),
                "d_cm_per_session": (decision.CM_PER_SESSION_FLOOR
                                     if need["reachable"]
                                     else need["max_cm_at_switch_1.0"]),
                "agg_checkout_rel_pct": 0.0, "treated_rto_pp": 0.0,
                "binding_constraint": (
                    "needs {}x its §10.1 prior".format(need["multiple_of_prior"])
                    if need["reachable"]
                    else "ship bar unreachable even at a 100% switch rate")})
    table = pd.DataFrame(rows)
    return {"volumes": volumes, "switches": switches, "table": table,
            "recommendation": _recommend(sim, levers, frame, tier, score,
                                         boundaries, volumes)}


def _recommend(sim, levers, frame, tier, score, boundaries, volumes) -> pd.DataFrame:
    """Per-tier recommendation, restricted to configurations that actually ship.

    Only restrictive levers appear here, because they are the only ones with a
    shippable configuration: no offer in the library reaches the +₹1.50 bar at
    any switch rate this dataset supports. That asymmetry is the result, not a
    filtering choice — see the ``required_switch`` column in the table above.
    """
    per_tier = {}
    for key, sweep in volumes.items():
        ok = sweep[sweep["shippable"]]
        if ok.empty:
            continue
        volume = float(ok.sort_values("d_cm_per_session").iloc[-1]["volume"])
        lever = levers[key]
        share = library.pp_denominator(frame, lever, tier, score, boundaries)
        mask = (library.eligible(lever, frame, tier, score, boundaries, "all")
                & library.restrictive_set(frame, score, volume))
        summary = _run(sim, lever, frame, tier, mask, library.response(lever, share))
        for _, row in summary.iterrows():
            if row["tier"] in ("ALL", "TREATED"):
                continue
            value = float(row["d_cm_per_session"])
            current = per_tier.get(row["tier"])
            if value > 0 and (current is None or value > current["d_cm_per_session"]):
                per_tier[row["tier"]] = {
                    "tier": row["tier"],
                    "action": "RUN {} at {:.0%} restricted volume".format(key, volume),
                    "lever": lever.name,
                    "treated_in_tier": int(row["treated"]),
                    "d_cm_per_session": round(value, 3),
                    "d_conversion_rel_pct": round(
                        float(row["d_conversion_rel_pct"]), 3),
                    "d_rto_pp": round(float(row["d_rto_pp"]), 2)}

    rows = []
    for name in ("LOW", "MED", "HIGH"):
        rows.append(per_tier.get(name, {
            "tier": name, "action": "NO INTERVENTION", "lever": "-",
            "treated_in_tier": 0, "d_cm_per_session": 0.0,
            "d_conversion_rel_pct": 0.0, "d_rto_pp": 0.0}))
    return pd.DataFrame(rows)


def _binding(sweep: pd.DataFrame, volume: float) -> str:
    """What stops the lever going one step past its feasible volume."""
    beyond = sweep[sweep["volume"] > volume]
    if beyond.empty:
        return "grid exhausted — no binding constraint found below 42% volume"
    return str(beyond.iloc[0]["verdict"])
