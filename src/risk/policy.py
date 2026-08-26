"""Turning an M2 score into a policy — and pricing the fairness constraint.

This module exists because of the §8.4 escalation and its ruling (decision A47,
`docs/phase4_escalation.md`). A score is not a policy; the ruling decides how the
score may be *used*, and that decision has three parts, all implemented here.

1. **Restrictive interventions rank within geo tier, not globally.**
   A Metro order at the 95th Metro percentile is treated like a Tier-3 order at
   the 95th Tier-3 percentile. Which levers that applies to is a classification
   rather than a measurement, so it lives in ``interventions.py`` and is
   re-exported here for callers that want the whole policy layer from one place.

2. **The constraint has a price and the price is reported.**
   ``margin_cost`` measures expected contribution margin under a global threshold
   against a per-tier threshold *at the same total restriction volume*, so the
   only thing that differs between the two arms is which orders are chosen.

3. **Equalising the ratio is not the same as making Tier-3's exposure
   acceptable.** ``tier_exposure`` reports the absolute rate and what the
   restricted orders in each tier actually look like, because a ratio of 1.0
   says nothing about the level.

The counterfactual, stated once
-------------------------------
``margin_cost`` prices a restriction as **pure abandonment**: a restricted order
does not happen, so its realised contribution margin goes to zero and the policy's
effect is ``-cm``. That is the §10.2 frame — above p*, an abandoned order is a
saving, not a loss — and it introduces no behavioural parameter that anyone would
have to defend. ``conversion_sensitivity`` sweeps the other direction: a share of
restricted COD orders switches to prepaid rather than abandoning, earning the
prepaid economics *measured from this dataset* rather than assumed.

This is an oracle evaluation on a labelled test set: it uses realised outcomes to
score a policy, which is what offline policy evaluation is. It is not a production
forecast, and both arms are scored the same way, so the *difference* between them
is the quantity that survives the caveat.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .fairness import PROXY_RATIO_LIMIT, apply_overlay
from .interventions import INTERVENTIONS, intervention_table  # noqa: F401  (re-export)

TIERS = ("METRO", "TIER1", "TIER2", "TIER3")

# The volumes FA-01 asserts at. Kept here so the validation suite and the report
# cannot drift apart: a fairness test that checks different volumes from the ones
# the policy was designed against would pass without meaning anything.
FA01_VOLUMES = (0.05, 0.10, 0.17, 0.25)


# ---------------------------------------------------------------------------
# The two thresholding rules
# ---------------------------------------------------------------------------


def global_flags(score: np.ndarray, volume: float) -> np.ndarray:
    """Top ``volume`` share of ALL orders by score. The margin-optimal rule."""
    cut = float(np.quantile(score, 1.0 - volume))
    return score >= cut


def per_tier_flags(score: np.ndarray, tier: pd.Series, volume: float) -> np.ndarray:
    """Top ``volume`` share *within each geo tier*, ranked against that tier.

    Each tier is cut at its own (1 - volume) quantile, so every tier is restricted
    at the same rate by construction and the §8.4 ratio is 1.0 before the
    customer-level overlay touches it. That is the whole mechanism: it does not
    make Metro riskier or Tier-3 safer, it changes who each order competes with.
    """
    tier = pd.Series(np.asarray(tier), name="geo_tier").reset_index(drop=True)
    score = pd.Series(np.asarray(score, dtype=float)).reset_index(drop=True)
    flags = np.zeros(len(score), dtype=bool)
    for value, index in tier.groupby(tier).groups.items():
        block = score.loc[index]
        cut = float(np.quantile(block, 1.0 - volume))
        flags[np.asarray(index)] = (block >= cut).to_numpy()
    return flags


def restrict(frame: pd.DataFrame, score: np.ndarray, volume: float, rule: str,
             apply_protections: bool = True) -> np.ndarray:
    """Flag the restricted set under ``rule``, then apply the §8.4 protections.

    The protections come *after* the threshold, not before, because that is the
    order a checkout would run them in: score, decide, then let the clean-record
    cap and the zero-history immunity veto. It also means the two mechanisms stay
    separately measurable, which §6.5 of the M1 report needed and this needs too.
    """
    flags = (global_flags(score, volume) if rule == "global"
             else per_tier_flags(score, frame["geo_tier"], volume))
    if not apply_protections:
        return flags
    tiers = pd.Series(np.where(flags, "HIGH", "LOW"))
    overlay = apply_overlay(frame.reset_index(drop=True), tiers)
    return (overlay["tier_final"] == "HIGH").to_numpy()


# ---------------------------------------------------------------------------
# Condition 3 — the absolute exposure, not just the ratio
# ---------------------------------------------------------------------------


def tier_exposure(frame: pd.DataFrame, score: np.ndarray, volume: float, rule: str,
                  pstar: float) -> pd.DataFrame:
    """Per-tier restriction rate, and what the restricted orders look like.

    ``share_below_pstar`` is the number the ratio hides. An order scored below p*
    has positive expected contribution margin as a COD order, so restricting it
    is a deliberate loss. Under a global threshold almost none of the restricted
    set is below p*; under per-tier thresholds a Metro cut can sit far below it.
    """
    work = frame.reset_index(drop=True).copy()
    work["score"] = np.asarray(score, dtype=float)
    work["flagged"] = restrict(work, work["score"].to_numpy(), volume, rule)

    rows = []
    for tier in TIERS:
        block = work[work["geo_tier"] == tier]
        flagged = block[block["flagged"]]
        rows.append({
            "geo_tier": tier,
            "orders": len(block),
            "restricted": int(len(flagged)),
            "restriction_rate": round(len(flagged) / max(len(block), 1), 4),
            "tier_rto_rate": round(float(block["rto_flag"].mean()), 4),
            "score_cut": round(float(flagged["score"].min()), 4) if len(flagged) else np.nan,
            "mean_score_restricted": round(float(flagged["score"].mean()), 4) if len(flagged) else np.nan,
            "rto_rate_restricted": round(float(flagged["rto_flag"].mean()), 4) if len(flagged) else np.nan,
            "share_below_pstar": round(float((flagged["score"] < pstar).mean()), 4) if len(flagged) else np.nan,
        })
    return pd.DataFrame(rows)


def ratio_audit(frame: pd.DataFrame, score: np.ndarray, rule: str,
                volumes=FA01_VOLUMES) -> pd.DataFrame:
    """FA-01's measurement: worst-tier / best-tier restriction rate, by volume.

    Worst-vs-best rather than Tier-3-vs-Metro. §8.4 named those two because they
    are the extremes it expected; naming them in the *test* would let a policy
    that concentrated on TIER2 pass a check that was watching the wrong pair.
    """
    rows = []
    for volume in volumes:
        exposure = tier_exposure(frame, score, volume, rule, pstar=1.0)
        rates = exposure.set_index("geo_tier")["restriction_rate"]
        worst, best = float(rates.max()), float(rates.min())
        rows.append({
            "volume_flagged": volume,
            "rule": rule,
            **{t: round(float(rates[t]), 4) for t in TIERS},
            "worst_tier": str(rates.idxmax()),
            "best_tier": str(rates.idxmin()),
            # A best-tier rate of zero is not a divide-by-zero to paper over. It
            # means the policy restricts nobody in that tier, which is the
            # strongest form of the failure FA-01 exists to catch.
            "ratio": ("no orders restricted in the best tier" if best == 0
                      else round(worst / best, 2)),
            "verdict": ("PASS" if best > 0 and worst / best <= PROXY_RATIO_LIMIT
                        else "FAIL"),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Condition 2 — the price of the constraint, in rupees
# ---------------------------------------------------------------------------


def margin_cost(frame: pd.DataFrame, score: np.ndarray, cm: np.ndarray,
                annualisation: float, volumes=FA01_VOLUMES) -> pd.DataFrame:
    """Expected CM under global vs per-tier restriction, at equal volume.

    ``cm`` is the realised per-order contribution margin from
    ``fct_order_economics``. Under the abandonment counterfactual the policy's
    effect on a restricted order is ``-cm``: an RTO carries a negative margin, so
    removing it is a gain; a delivered order carries a positive one, so removing
    it is a loss. Summing over the restricted set gives the policy's total effect
    with no behavioural parameter anywhere in it.

    The volumes are matched exactly, so the two arms restrict the same number of
    orders and the difference is pure targeting quality.
    """
    cm = np.asarray(cm, dtype=float)
    rows = []
    for volume in volumes:
        # Both arms select the SAME number of orders. The §8.4 overlay then vetoes
        # a slightly different number in each, because clean records are not
        # evenly distributed across tiers -- so `selected` is the matched volume
        # and the `_n` columns are what survives the veto. Reported separately
        # rather than folded, so "same volume" can be checked and not taken on
        # trust.
        record = {"volume_flagged": volume,
                  "selected": int(global_flags(score, volume).sum())}
        for rule in ("global", "per-tier"):
            flags = restrict(frame, score, volume, rule)
            delta = float(-cm[flags].sum())
            record[f"{rule}_n"] = int(flags.sum())
            record[f"{rule}_delta_cm"] = round(delta, 0)
            record[f"{rule}_per_order"] = round(delta / max(int(flags.sum()), 1), 2)
        record["price_of_fairness"] = round(
            record["global_delta_cm"] - record["per-tier_delta_cm"], 0)
        record["price_per_restricted_order"] = round(
            record["price_of_fairness"] / max(record["global_n"], 1), 2)
        record["price_annualised_cr"] = round(
            record["price_of_fairness"] * annualisation / 1e7, 2)
        rows.append(record)
    return pd.DataFrame(rows)


def prepaid_economics(economics: pd.DataFrame, orders: pd.DataFrame) -> dict:
    """Measured prepaid CM on delivered and returned orders. Not assumed.

    Used only by ``conversion_sensitivity``. Measuring these rather than quoting
    §6.5's worked example matters: the worked example is at a ₹1,000 order value
    and the restricted population is not a ₹1,000 population.
    """
    joined = orders.merge(economics, on="order_id", how="inner", suffixes=("", "_e"))
    prepaid = joined[(joined["payment_method"] == "PREPAID") & joined["is_shipped"]]
    resolved = prepaid[~prepaid["is_censored"].astype(bool)]
    return {
        "prepaid_rto_rate": float(resolved["rto_flag"].mean()),
        "cm_delivered": float(resolved.loc[~resolved["rto_flag"].astype(bool),
                                           "contribution_margin"].mean()),
        "cm_rto": float(resolved.loc[resolved["rto_flag"].astype(bool),
                                     "contribution_margin"].mean()),
    }


def conversion_sensitivity(frame: pd.DataFrame, score: np.ndarray, cm: np.ndarray,
                           prepaid: dict, annualisation: float, volume: float,
                           shares=(0.0, 0.25, 0.50)) -> pd.DataFrame:
    """The price of fairness when restricted COD orders SWITCH rather than abandon.

    ``shares`` is a sweep, not an estimate. Nobody has measured the switch rate on
    this platform — §10.1's ±20-30pp figures are Phase 1 priors written down to be
    tested, not results — so the honest thing is to show how the answer moves
    across the plausible range rather than pick a point inside it and quote it.

    A switched order earns ``(1 - p_prepaid) * cm_delivered + p_prepaid * cm_rto``
    with all three quantities measured from this dataset.
    """
    cm = np.asarray(cm, dtype=float)
    switched_ev = ((1 - prepaid["prepaid_rto_rate"]) * prepaid["cm_delivered"]
                   + prepaid["prepaid_rto_rate"] * prepaid["cm_rto"])
    is_cod = (frame["payment_method"] == "COD").to_numpy()

    rows = []
    for share in shares:
        record = {"cod_switch_share": share, "switched_order_ev": round(switched_ev, 2)}
        for rule in ("global", "per-tier"):
            flags = restrict(frame, score, volume, rule)
            switchable = flags & is_cod
            # Written out longhand rather than folded into one expression,
            # because a fold is where a sign error hides.
            #
            #   every restricted order loses its realised margin      -> -cm
            #   a switching COD order then EARNS the prepaid EV       -> +ev
            #
            # A restricted PREPAID order can only abandon; there is nothing for
            # it to switch to.
            abandoned = float(-cm[flags].sum())
            earned = float(share * switched_ev * switchable.sum())
            delta = abandoned + earned
            record[f"{rule}_delta_cm"] = round(delta, 0)
        record["price_of_fairness"] = round(
            record["global_delta_cm"] - record["per-tier_delta_cm"], 0)
        record["price_annualised_cr"] = round(
            record["price_of_fairness"] * annualisation / 1e7, 2)
        rows.append(record)
    return pd.DataFrame(rows)


def protection_gradient(frame: pd.DataFrame) -> pd.DataFrame:
    """Why per-tier thresholds land at a ratio near 1.3 rather than exactly 1.0.

    Per-tier selection equalises exactly. What breaks the tie afterwards is the
    §8.4 customer-level overlay, and the overlay is not geographically neutral:
    a clean record means three delivered orders and zero RTO, and Tier-3 has a
    much higher RTO rate, so far fewer Tier-3 customers hold one.

    Reported rather than asserted, because "the residual spread is the overlay's
    own gradient" is a claim about a mechanism and a claim about a mechanism
    should come with the measurement.
    """
    work = frame.reset_index(drop=True)
    clean = work["pit_has_clean_record"].astype(bool)
    no_history = ~work["pit_has_history"].astype(bool)
    rows = []
    for tier in TIERS:
        mask = (work["geo_tier"] == tier).to_numpy()
        rows.append({
            "geo_tier": tier,
            "clean_record_pct": round(float(clean[mask].mean()) * 100, 2),
            "zero_history_pct": round(float(no_history[mask].mean()) * 100, 2),
            "protected_pct": round(float((clean | no_history)[mask].mean()) * 100, 2),
            "tier_rto_rate": round(float(work.loc[mask, "rto_flag"].mean()), 4),
        })
    return pd.DataFrame(rows)
