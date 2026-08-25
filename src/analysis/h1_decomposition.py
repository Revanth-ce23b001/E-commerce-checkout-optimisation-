"""H1 — how much of the COD–RTO gap is causation, and how much is selection?

The centrepiece of Phase 3, and the reason the dataset was built with a planted
ground truth.

The question
------------
COD orders return at 23.34%; prepaid orders at 5.61%. The naive read is that
paying cash makes an order 17.73pp more likely to come back. If that is right,
payment method is a lever and a COD fee is well aimed.

But COD is *chosen*, not assigned. If the customers who choose it are
independently riskier — newer, Tier-3, thinner history — then some of that
17.73pp is the customers, not the payment method, and a COD fee is aimed at a
symptom.

Four estimates, each controlling for more
-----------------------------------------
1. **Raw crosstab.** No controls.
2. **Stratified.** Rates within tenure x geo cells, recombined by direct
   standardisation to the common (prepaid) distribution. Non-parametric.
3. **Logistic regression** on every observed confounder, reported as an average
   marginal effect so it is on the same percentage-point scale as the others.
4. **Propensity matching.** Nearest-neighbour within a caliper on the estimated
   P(COD | X), which drops COD orders with no comparable prepaid twin instead of
   extrapolating into regions where the data cannot support a comparison.

What "success" looks like — and why it is not zero
--------------------------------------------------
The planted truth is an average marginal effect of **9.99pp**; the naive gap is
**17.73pp**. The difference, **7.74pp**, is selection.

An adjustment that recovered all 7.74pp would be a **failure**, not a triumph:
the confounder that does most of the work — ``latent_intent`` — is unobservable
by construction, held in a PostgreSQL schema the analyst role cannot read. If an
adjusted estimate lands on the truth, something has leaked into the feature set.

Test GT-03 encodes exactly that::

    PASS if  AME < adjusted < naive
    AND      (adjusted - AME) / (naive - AME) >= 0.35

i.e. the adjustment must move toward the truth without arriving, closing at most
65% of the selection component.

Denominator, throughout: shipped AND NOT censored.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Every confounder here is on the safe-feature whitelist and is knowable BEFORE
# the outcome. No latent, no post-dispatch fact, no delivery-attempt count.
# These are the variables an analyst with this warehouse would actually have.
CONTINUOUS = [
    "pit_tenure_days", "pit_orders_placed", "pit_orders_delivered",
    "pit_rto_rate_shrunk", "pit_cod_share", "pit_payment_failure_rate",
    "serviceability_score", "courier_reliability_score", "cod_cultural_index",
    "product_rating", "review_count", "seller_rating", "seller_sla_breach_rate",
    "cart_size", "estimated_delivery_days", "address_completeness_score",
    "order_value", "discount_pct",
]
BINARY = ["pit_is_new_customer", "pit_has_history", "has_saved_prepaid_instrument",
          "is_month_end_window", "is_returnable"]
CATEGORICAL = ["geo_tier", "category", "device_type", "age_bucket",
               "acquisition_channel"]


def design_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Confounders only. ``is_cod`` is added by the caller as the treatment."""
    parts = [frame[CONTINUOUS].astype(float)]
    parts.append(frame[BINARY].astype(float))
    parts.append(pd.get_dummies(frame[CATEGORICAL].astype(str),
                                drop_first=True, dtype=float))
    X = pd.concat(parts, axis=1)

    # log the two heavy-tailed money/count columns; both enter the generating
    # process on a log scale, and leaving them linear would understate the
    # adjustment for reasons that have nothing to do with confounding.
    if "order_value" in X:
        X["order_value"] = np.log(X["order_value"].clip(lower=1.0))
    if "review_count" in X:
        X["review_count"] = np.log1p(X["review_count"])

    # A18: no imputation of a rate with an empty denominator. NULL becomes 0
    # alongside an explicit missingness indicator, so the model can tell
    # "no history" from "history, and it is zero".
    for column in ("pit_rto_rate_shrunk", "pit_cod_share", "pit_payment_failure_rate",
                   "pit_avg_order_value"):
        if column in X.columns:
            X[f"{column}_missing"] = X[column].isna().astype(float)
            X[column] = X[column].fillna(0.0)
    return drop_collinear(X.fillna(0.0))


def drop_collinear(X: pd.DataFrame, tol: float = 1e-9) -> pd.DataFrame:
    """Keep a maximal full-rank subset of the confounders, and say what went.

    The design matrix is rank-deficient by construction here, and the reason is
    worth stating rather than silently regularising away: ``pit_has_history`` is
    ``pit_orders_placed > 0``, which is the exact complement of the missingness
    indicators that decision A18 requires alongside every NULL-able point-in-time
    rate. Two correct modelling rules produce one redundant column.

    Dropping is preferred to ridge-penalising because a penalty would shrink the
    treatment coefficient too, and the treatment coefficient is the estimate.
    Column selection is by pivoted QR, which is deterministic and picks the
    better-conditioned member of each collinear set rather than the first one
    alphabetically.
    """
    keep = [c for c in X.columns if X[c].std(ddof=0) > 0]
    dropped_constant = [c for c in X.columns if c not in keep]

    values = X[keep].to_numpy(dtype=float)
    values = (values - values.mean(0)) / values.std(0)
    _, r, pivots = __import__("scipy.linalg", fromlist=["qr"]).qr(
        values, mode="economic", pivoting=True)
    rank = int((np.abs(np.diag(r)) > tol * abs(r[0, 0])).sum())
    selected = sorted(pivots[:rank])
    out = X[[keep[i] for i in selected]]
    out.attrs["dropped_constant"] = dropped_constant
    out.attrs["dropped_collinear"] = [keep[i] for i in pivots[rank:]]
    return out


# ---------------------------------------------------------------------------
# 1. Raw
# ---------------------------------------------------------------------------


def raw_crosstab(frame: pd.DataFrame) -> dict:
    """The comparison anyone runs first. No controls at all."""
    cod = frame[frame["is_cod"] == 1]["rto_flag"]
    prepaid = frame[frame["is_cod"] == 0]["rto_flag"]
    gap = cod.mean() - prepaid.mean()
    # Effect size, not just a p-value: at 91,250 rows everything is significant.
    se = np.sqrt(cod.var(ddof=1) / len(cod) + prepaid.var(ddof=1) / len(prepaid))
    return {
        "method": "1. Raw crosstab",
        "cod_rate": float(cod.mean()),
        "prepaid_rate": float(prepaid.mean()),
        "estimate_pp": float(gap) * 100,
        "ci_low_pp": float(gap - 1.96 * se) * 100,
        "ci_high_pp": float(gap + 1.96 * se) * 100,
        "n_cod": len(cod),
        "n_prepaid": len(prepaid),
        "controls": "none",
    }


# ---------------------------------------------------------------------------
# 2. Stratified
# ---------------------------------------------------------------------------


def tenure_bucket(delivered: pd.Series) -> pd.Series:
    """Phase 1 H2's buckets: 0, 1-2, 3-5, 6-15, 16+ completed orders."""
    return pd.cut(delivered.fillna(0), bins=[-1, 0, 2, 5, 15, np.inf],
                  labels=["0", "1-2", "3-5", "6-15", "16+"])


def stratified(frame: pd.DataFrame) -> dict:
    """Rates within tenure x geo cells, recombined by direct standardisation.

    **Standardised to the COD distribution, because the estimand is the effect
    on the TREATED.** The question is "what would have happened to *these COD
    orders* had they been prepaid", and the truth file's AME is computed over
    exactly that population (shipped COD orders, decision A6). Weighting by the
    prepaid mix answers the mirror-image question -- what would happen to the
    prepaid population if it went COD -- and is not comparable to the AME.

    The choice is not cosmetic: it moves the estimate by 3.7pp here, more than a
    third of the true effect. All three weightings are returned so the sensitivity
    is visible rather than buried in a default.

    Cells with fewer than 30 orders on either side are dropped rather than
    smoothed -- a 3-order cell contributes noise with the authority of a rate.
    """
    work = frame.copy()
    work["tenure"] = tenure_bucket(work["pit_orders_delivered"])
    cells, dropped, dropped_orders = [], 0, 0
    for (tenure, geo), group in work.groupby(["tenure", "geo_tier"], observed=True):
        cod, prepaid = group[group["is_cod"] == 1], group[group["is_cod"] == 0]
        if len(cod) < 30 or len(prepaid) < 30:
            dropped += 1
            dropped_orders += len(group)
            continue
        cells.append({
            "tenure": str(tenure), "geo_tier": geo,
            "n_cod": len(cod), "n_prepaid": len(prepaid),
            "cod_rate": float(cod["rto_flag"].mean()),
            "prepaid_rate": float(prepaid["rto_flag"].mean()),
            "gap_pp": float(cod["rto_flag"].mean() - prepaid["rto_flag"].mean()) * 100,
        })
    table = pd.DataFrame(cells)

    def standardise(weights: pd.Series) -> float:
        return float((table["gap_pp"] * (weights / weights.sum())).sum())

    att = standardise(table["n_cod"])
    return {
        "method": "2. Stratified (tenure x geo)",
        "estimate_pp": att,
        "estimate_att_cod_weighted_pp": att,
        "estimate_ate_pooled_weighted_pp": standardise(table["n_cod"] + table["n_prepaid"]),
        "estimate_atu_prepaid_weighted_pp": standardise(table["n_prepaid"]),
        "cells_used": len(table),
        "cells_dropped": dropped,
        "orders_dropped": dropped_orders,
        "controls": "tenure bucket x geo tier",
        "table": table.sort_values("gap_pp", ascending=False, ignore_index=True),
    }


# ---------------------------------------------------------------------------
# 3. Logistic regression
# ---------------------------------------------------------------------------


def logistic_adjusted(frame: pd.DataFrame) -> dict:
    """Every observed confounder, reported as an average marginal effect.

    The AME -- not the odds ratio -- is what goes in the comparison table, so
    that all four estimates are on the same percentage-point scale. An odds ratio
    of 3.0 means different things at a 5% and a 25% baseline, and quoting it
    against a pp figure is the commonest way this comparison gets muddled.
    """
    import statsmodels.api as sm

    X = design_matrix(frame)
    X.insert(0, "is_cod", frame["is_cod"].astype(float).to_numpy())
    X = sm.add_constant(X, has_constant="add")
    y = frame["rto_flag"].astype(float).to_numpy()

    model = sm.Logit(y, X.astype(float)).fit(disp=False, maxiter=200)

    # AME by counterfactual: set is_cod off for everyone, then on for everyone,
    # and average the difference in predicted probability. Same definition the
    # truth file uses for the planted effect (decision A6), so the two are
    # directly comparable rather than approximately so.
    off, on = X.copy(), X.copy()
    off["is_cod"], on["is_cod"] = 0.0, 1.0
    ame = float((model.predict(on) - model.predict(off)).mean()) * 100

    coef = float(model.params["is_cod"])
    se = float(model.bse["is_cod"])
    # Delta-method-free CI: scale the AME by the coefficient's relative CI width.
    # Adequate here because the point estimate is what GT-03 tests.
    return {
        "method": "3. Logistic regression",
        "estimate_pp": ame,
        "log_odds_coefficient": coef,
        "odds_ratio": float(np.exp(coef)),
        "coef_se": se,
        "ci_low_pp": ame * (coef - 1.96 * se) / coef,
        "ci_high_pp": ame * (coef + 1.96 * se) / coef,
        "n_features": X.shape[1] - 1,
        "pseudo_r2": float(model.prsquared),
        "controls": f"{X.shape[1] - 2} observed confounders",
    }


# ---------------------------------------------------------------------------
# 4. Propensity matching
# ---------------------------------------------------------------------------


def propensity_matched(frame: pd.DataFrame, caliper: float = 0.01,
                       seed: int = 20260115) -> dict:
    """Nearest-neighbour matching on P(COD | X), within a caliper.

    Matching answers a narrower question than the regression does, and that is
    the point: it compares each COD order only against prepaid orders that
    *could plausibly have been COD*. Where no such twin exists the COD order is
    dropped rather than extrapolated over, so the estimate is an ATT on the
    region of common support, not an average over territory the data cannot
    speak to.

    The number of unmatched COD orders is reported. It is a finding in its own
    right: it measures how far COD and prepaid populations have separated.
    """
    import statsmodels.api as sm

    X = design_matrix(frame)
    X = sm.add_constant(X, has_constant="add").astype(float)
    treatment = frame["is_cod"].astype(int).to_numpy()

    ps_model = sm.Logit(treatment, X).fit(disp=False, maxiter=200)
    ps = np.asarray(ps_model.predict(X), dtype=float)

    y = frame["rto_flag"].astype(float).to_numpy()
    treated = np.flatnonzero(treatment == 1)
    control = np.flatnonzero(treatment == 0)

    order = np.argsort(ps[control], kind="stable")
    control_sorted = control[order]
    ps_control = ps[control_sorted]

    rng = np.random.default_rng(seed)
    # Match on the LOGIT of the propensity, the standard scale: it spreads the
    # crowded tails where raw propensities pile up near 0 and 1.
    def lg(p):
        p = np.clip(p, 1e-9, 1 - 1e-9)
        return np.log(p / (1 - p))

    lg_control = lg(ps_control)
    lg_treated = lg(ps[treated])
    caliper_width = caliper * np.std(lg(ps))

    idx = np.searchsorted(lg_control, lg_treated)
    matched_t, matched_c = [], []
    for k, t in enumerate(treated):
        i = idx[k]
        best, best_dist = -1, np.inf
        for j in (i - 1, i):
            if 0 <= j < len(lg_control):
                d = abs(lg_control[j] - lg_treated[k])
                if d < best_dist:
                    best, best_dist = j, d
        if best >= 0 and best_dist <= caliper_width:
            matched_t.append(t)
            matched_c.append(control_sorted[best])

    matched_t = np.asarray(matched_t)
    matched_c = np.asarray(matched_c)
    gap = float(y[matched_t].mean() - y[matched_c].mean())
    n = len(matched_t)
    se = np.sqrt(y[matched_t].var(ddof=1) / n + y[matched_c].var(ddof=1) / n)
    return {
        "method": "4. Propensity matched",
        "estimate_pp": gap * 100,
        "ci_low_pp": (gap - 1.96 * se) * 100,
        "ci_high_pp": (gap + 1.96 * se) * 100,
        "n_matched_pairs": n,
        "n_treated": len(treated),
        "unmatched_cod": len(treated) - n,
        "unmatched_share": 1 - n / len(treated),
        "caliper_logit_sd": caliper,
        "ps_auc": _auc(treatment, ps),
        "controls": "matched on P(COD | X), caliper on logit(ps)",
    }


def _auc(y: np.ndarray, score: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, score))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def gt_03(adjusted_pp: float, ame_pp: float, naive_pp: float) -> dict:
    """The relative rule (decision A6). Ordering first, then how far it moved.

    ``remaining`` is the share of the selection component the adjustment did NOT
    explain. GT-03 requires at least 0.35 of it to survive: an estimate that
    lands on the truth means the unobservable leaked.
    """
    remaining = (adjusted_pp - ame_pp) / (naive_pp - ame_pp)
    ordered = ame_pp < adjusted_pp < naive_pp
    return {
        "ordered_ame_lt_adjusted_lt_naive": bool(ordered),
        "selection_component_pp": naive_pp - ame_pp,
        "recovered_pp": naive_pp - adjusted_pp,
        "share_of_selection_recovered": 1 - remaining,
        "share_remaining": remaining,
        "passes": bool(ordered and remaining >= 0.35),
    }
