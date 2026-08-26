"""The §8.4 fairness overlay, audited before any tiering is proposed.

Blueprint §8.4 wrote three protections down *before* the model existed, and gave
the reason: "after you have the AUC it's very hard to argue yourself out of using
it." They are enforced here, in this order:

1. **Geography audit.** Intervention rate by geo tier. If Tier-3 exceeds Metro by
   more than 2.5x, the model is proxying for postcode rather than behaviour.
   Escalate; do not proceed to tiering.
2. **The clean-record cap.** >= 3 delivered orders and zero prior RTO ⇒ hard-capped
   at MED regardless of score. ``pit_has_clean_record`` is that predicate,
   already computed point-in-time, so the cap is implementable from the view.
3. **Zero-history immunity.** A customer with no history is never eligible for a
   restriction. ``pit_has_history`` is that predicate. New customers get carrots.

One thing this module deliberately does NOT do: tier M1 scores against p*.
p* = 0.2576 is the break-even of a *COD* order and belongs to M2 (spec §4.3,
closeout §2). An M1 score is a probability over a payment mix that is not yet
chosen, so comparing it to a COD-specific break-even is a category error. The
geography audit is therefore run across a SWEEP of restriction volumes rather
than at one threshold — the sweep answers "is this model proxying for postcode"
without borrowing a threshold it is not entitled to.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PROXY_RATIO_LIMIT = 2.5
TIER_SEQUENCE = ("LOW", "MED", "HIGH")


def geo_audit(frame: pd.DataFrame, score: np.ndarray,
              volumes=(0.05, 0.10, 0.17, 0.25)) -> pd.DataFrame:
    """Restriction rate by geo tier across a sweep of restricted volumes.

    ``volumes`` are shares of traffic flagged, top-down by score. 0.17 is the
    share blueprint §8.3 expects to land in the High tier.
    """
    rows = []
    for volume in volumes:
        cut = float(np.quantile(score, 1.0 - volume))
        flagged = score >= cut
        by_tier = pd.DataFrame({"geo_tier": frame["geo_tier"].to_numpy(),
                                "flagged": flagged}).groupby("geo_tier")["flagged"].mean()
        metro = float(by_tier.get("METRO", np.nan))
        tier3 = float(by_tier.get("TIER3", np.nan))
        record = {"volume_flagged": volume, "score_cut": round(cut, 4)}
        for tier in ("METRO", "TIER1", "TIER2", "TIER3"):
            record[tier] = round(float(by_tier.get(tier, np.nan)), 4)
        # A zero Metro rate is not a divide-by-zero to be papered over. It means
        # the policy restricts NO Metro customer at all, which is the strongest
        # possible form of the failure this test exists to catch.
        record["tier3_over_metro"] = (
            "no Metro flagged" if metro == 0 else round(tier3 / metro, 1))
        record["verdict"] = ("PASS" if metro > 0 and tier3 / metro <= PROXY_RATIO_LIMIT
                             else "ESCALATE")
        rows.append(record)
    return pd.DataFrame(rows)


GEOGRAPHIC_FEATURES = ("geo_tier[TIER1]", "geo_tier[TIER2]", "geo_tier[TIER3]",
                       "serviceability_score", "courier_reliability_score",
                       "cod_cultural_index", "estimated_delivery_days")


def geography_ablation(X_train, y_train, X_test, test_frame, y_test,
                       volume: float = 0.17) -> pd.DataFrame:
    """Refit M1 with geographic feature blocks removed, one block at a time.

    This is what turns "the audit failed" into an actionable finding. It
    separates two questions that look identical from the outside: is the model
    *using* postcode, and is postcode *where the risk is*? Dropping the geo_tier
    dummies alone answers the first; dropping every geographically-derived
    feature answers the second.
    """
    from .evaluate import auc as _auc
    from .scorecard import Scorecard

    blocks = {
        "full model": [],
        "no geo_tier dummies": [c for c in X_train.columns if c.startswith("geo_tier[")],
        "no geographic features at all": [c for c in GEOGRAPHIC_FEATURES
                                          if c in X_train.columns],
        "no point-in-time history": [c for c in X_train.columns
                                     if "pit_" in c],
    }
    rows = []
    for name, dropped in blocks.items():
        keep = [c for c in X_train.columns if c not in dropped]
        model = Scorecard().fit(X_train[keep], y_train)
        scores = model.predict_proba(X_test[keep])
        audited = geo_audit(test_frame, scores, (volume,)).iloc[0]
        rows.append({
            "model": name,
            "features": len(keep),
            "test_auc": round(_auc(y_test, scores), 4),
            "metro_flagged": audited["METRO"],
            "tier3_flagged": audited["TIER3"],
            "tier3_over_metro": audited["tier3_over_metro"],
            "verdict": audited["verdict"],
        })
    return pd.DataFrame(rows)


def protection_coverage(frame: pd.DataFrame) -> pd.DataFrame:
    """Are the two customer-level protections implementable from the view, and
    how many customers do they actually protect?"""
    clean = frame["pit_has_clean_record"].astype(bool)
    no_history = ~frame["pit_has_history"].astype(bool)
    rows = [
        {"protection": "clean-record cap (>=3 delivered, 0 RTO)",
         "predicate": "pit_has_clean_record",
         "in_view": "pit_has_clean_record" in frame.columns,
         "orders": int(clean.sum()),
         "share_pct": round(float(clean.mean()) * 100, 2),
         "rto_rate": round(float(frame.loc[clean, "rto_flag"].mean()), 4)},
        {"protection": "zero-history immunity (never restricted)",
         "predicate": "NOT pit_has_history",
         "in_view": "pit_has_history" in frame.columns,
         "orders": int(no_history.sum()),
         "share_pct": round(float(no_history.mean()) * 100, 2),
         "rto_rate": round(float(frame.loc[no_history, "rto_flag"].mean()), 4)},
    ]
    return pd.DataFrame(rows)


def apply_overlay(frame: pd.DataFrame, tiers: pd.Series) -> pd.DataFrame:
    """Cap protected customers, and report how much each rule moves.

    Returns the frame with ``tier_raw`` and ``tier_final`` so the reclassified
    share can be reported per tier — §8.4's last requirement.
    """
    out = pd.DataFrame({"tier_raw": pd.Categorical(tiers, categories=TIER_SEQUENCE,
                                                   ordered=True)})
    out["tier_final"] = out["tier_raw"].astype(str)

    clean = frame["pit_has_clean_record"].astype(bool).to_numpy()
    no_history = ~frame["pit_has_history"].astype(bool).to_numpy()

    capped = clean & (out["tier_raw"].astype(str) == "HIGH").to_numpy()
    out.loc[capped, "tier_final"] = "MED"

    immune = no_history & (out["tier_final"] == "HIGH").to_numpy()
    out.loc[immune, "tier_final"] = "MED"

    out["reclassified"] = out["tier_final"] != out["tier_raw"].astype(str)
    out["reason"] = np.where(capped, "clean-record cap",
                             np.where(immune, "zero-history immunity", ""))
    return out


def overlay_summary(overlay: pd.DataFrame) -> pd.DataFrame:
    raw = overlay["tier_raw"].astype(str).value_counts()
    final = overlay["tier_final"].value_counts()
    moved = overlay[overlay["reclassified"]].groupby(
        overlay["tier_raw"].astype(str)).size()
    table = pd.DataFrame({
        "tier": list(TIER_SEQUENCE),
        "n_raw": [int(raw.get(t, 0)) for t in TIER_SEQUENCE],
        "n_final": [int(final.get(t, 0)) for t in TIER_SEQUENCE],
        "reclassified_out": [int(moved.get(t, 0)) for t in TIER_SEQUENCE],
    })
    table["share_raw_pct"] = (table["n_raw"] / table["n_raw"].sum() * 100).round(2)
    table["share_final_pct"] = (table["n_final"] / table["n_final"].sum() * 100).round(2)
    table["pct_of_tier_reclassified"] = np.where(
        table["n_raw"] > 0, (table["reclassified_out"] / table["n_raw"] * 100).round(2), 0.0)
    return table
