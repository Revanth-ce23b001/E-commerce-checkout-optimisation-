"""GT-03 diagnostics — why does the adjustment close 70.9% of a 65% ceiling?

GT-03 fails and the ruling of 2026-08-26 refused to restate it. The ceiling is
load-bearing: ``latent_intent``, ``latent_trust`` and ``latent_liquidity`` are
unobservable **by construction**, so an adjustment built only from safe features
must not fully recover the selection component. Closing 70.9% of the
naive-to-AME distance means the observed confounders are proxying the latents
better than intended, and the ruling asked which of two things that is:

* a real weakness in the **confounding structure** — the observed set happens to
  span the same space as the latents; or
* a latent that is **partially reconstructible** from the safe feature set, in
  which case "unobservable by construction" is a claim that needs qualifying
  everywhere it is made.

Three measurements decide it. **Nothing here fixes anything** — no feature is
dropped from any production model, no threshold moves, GT-03 stays FAIL.

1. :func:`without_pit_rto_rate` — refit with ``pit_rto_rate_shrunk`` removed.
   It is the single most likely latent proxy (planted +2.80 in the RTO logit,
   and A11 generates the history it aggregates *from* the latents).
2. :func:`latent_reconstructibility` — regress each latent on the safe feature
   set and report R². Above ~0.35 the unobservability claim is qualified.
3. :func:`confounder_contributions` — per-block deviance contribution in the
   RTO model, alongside how far the ATT moves when the block is dropped.

Population: ``data/processed/h1_population.parquet``, shipped AND NOT censored —
the same frame GT-03 grades on, so the numbers are directly comparable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import h1_decomposition as H

# Named, not pattern-matched: the point of the exercise is that this exact
# feature is suspected, so the suspect has to be nameable in the output.
SUSPECT = "pit_rto_rate_shrunk"

LATENTS = ("latent_intent", "latent_trust", "latent_liquidity")

# Not a latent, and reported alongside them on purpose. ``true_cod_propensity``
# is the composite the three latents drive, and it is the CHOICE channel rather
# than the latent values themselves. A latent can be unrecoverable while the
# choice it produces is highly predictable -- and if that is what is happening,
# the adjustment is closing the gap by reconstructing treatment assignment, not
# by reconstructing an unobservable. The two look identical in a closure figure
# and are completely different findings.
CHOICE_CHANNEL = "true_cod_propensity"

# Blocks for measurement 3. A one-hot set is one confounder, not four, and the
# A18 missingness indicator belongs with the rate it annotates -- grading them
# separately would split one variable's contribution across several rows and
# understate every categorical.
BLOCK_PREFIXES = {
    "geo_tier": "geo_tier_",
    "category": "category_",
    "device_type": "device_type_",
    "age_bucket": "age_bucket_",
    "acquisition_channel": "acquisition_channel_",
}


def _blocks(columns) -> dict:
    """Group design-matrix columns into confounders."""
    out: dict = {}
    for column in columns:
        name = column
        for block, prefix in BLOCK_PREFIXES.items():
            if column.startswith(prefix):
                name = block
                break
        else:
            if column.endswith("_missing"):
                name = column[: -len("_missing")]
        out.setdefault(name, []).append(column)
    return out


def _fit(y, X):
    import statsmodels.api as sm

    return sm.Logit(np.asarray(y, dtype=float), X.astype(float)).fit(
        disp=False, maxiter=200)


def _att(model, X, treated) -> float:
    """ATT by counterfactual: the estimand A49 fixed for every GT-03 estimate."""
    off, on = X.copy(), X.copy()
    off["is_cod"], on["is_cod"] = 0.0, 1.0
    delta = (np.asarray(model.predict(on)) - np.asarray(model.predict(off))) * 100
    return float(delta[treated].mean())


def closed(estimate: float, ame: float, naive: float) -> float:
    """Share of the naive-to-AME distance an estimate closes. GT-03's metric."""
    return (naive - estimate) / (naive - ame)


# ---------------------------------------------------------------------------
# 1. Drop the suspect
# ---------------------------------------------------------------------------


def without_pit_rto_rate(pop: pd.DataFrame, ame: float, naive: float) -> dict:
    """Refit the adjusted estimators with ``pit_rto_rate_shrunk`` removed.

    Its A18 missingness indicator goes with it. The indicator exists only to say
    "this rate is unknown"; leaving it behind would keep a column whose entire
    meaning is a reference to the feature just dropped, and the residual signal
    would be attributed to the wrong place.

    Both the logistic ATT and the propensity match are refit, because the two
    can move in opposite directions: dropping a confounder from the outcome
    model removes an adjustment, while dropping it from the propensity model
    changes *who gets matched to whom*.
    """
    import statsmodels.api as sm

    full = H.design_matrix(pop)
    removed = [c for c in full.columns if c.startswith(SUSPECT)]
    reduced = full.drop(columns=removed)
    treated = pop["is_cod"].astype(bool).to_numpy()
    y = pop["rto_flag"].astype(float).to_numpy()

    rows = []
    for label, base in (("full confounder set", full),
                        ("minus " + SUSPECT, reduced)):
        X = base.copy()
        X.insert(0, "is_cod", pop["is_cod"].astype(float).to_numpy())
        X = sm.add_constant(X, has_constant="add")
        model = _fit(y, X)
        att = _att(model, X, treated)
        on, off = X.copy(), X.copy()
        on["is_cod"], off["is_cod"] = 1.0, 0.0
        ate = float((np.asarray(model.predict(on))
                     - np.asarray(model.predict(off))).mean()) * 100
        rows.append({
            "spec": label, "n_confounders": base.shape[1],
            "logit_att_pp": att, "logit_ate_pp": ate,
            "closed_att": closed(att, ame, naive),
            "closed_ate": closed(ate, ame, naive),
            "cod_coefficient": float(model.params["is_cod"]),
            "pseudo_r2": float(model.prsquared),
        })

    # The propensity match is refit separately: `H.propensity_matched` builds its
    # own design matrix internally, so the reduced version runs on a frame with
    # the column blanked rather than by passing a matrix in. Blanking to a
    # constant is equivalent to dropping -- `drop_collinear` removes
    # zero-variance columns -- and it avoids forking the matching code.
    blanked = pop.copy()
    blanked[SUSPECT] = 0.0
    psm_full = H.propensity_matched(pop)
    psm_reduced = H.propensity_matched(blanked)

    return {
        "removed_columns": removed,
        "table": pd.DataFrame(rows),
        "psm_full_pp": psm_full["estimate_pp"],
        "psm_reduced_pp": psm_reduced["estimate_pp"],
        "psm_full_closed": closed(psm_full["estimate_pp"], ame, naive),
        "psm_reduced_closed": closed(psm_reduced["estimate_pp"], ame, naive),
        "psm_full_ps_auc": psm_full["ps_auc"],
        "psm_reduced_ps_auc": psm_reduced["ps_auc"],
        "psm_full_unmatched": psm_full["unmatched_share"],
        "psm_reduced_unmatched": psm_reduced["unmatched_share"],
    }


# ---------------------------------------------------------------------------
# 2. Are the latents reconstructible?
# ---------------------------------------------------------------------------

# Columns excluded from the maximal safe set. The outcome and its two filters,
# plus identifiers and the timestamp. `payment_method` is excluded because
# `is_cod` already carries it; `delivery_geography_id` because it is a 500-level
# identifier that risk/features.py already excludes for the same reason.
_NOT_SAFE = {"rto_flag", "is_shipped", "is_censored", "session_id",
             "customer_id", "session_start_ts", "payment_method",
             "delivery_geography_id"}
_EXTRA_CATEGORICAL = ["pit_risk_tier_rule_based"]


def maximal_safe_matrix(pop: pd.DataFrame) -> pd.DataFrame:
    """Everything analyst-visible and knowable before the outcome.

    Deliberately WIDER than GT-03's confounder set. The question is whether a
    latent can be reconstructed from what an analyst could actually reach, so
    the generous set is the conservative one: if R² is low even here, the
    unobservability claim is safe; if it is high, restricting the set first
    would have hidden that.
    """
    base = H.design_matrix(pop)
    candidates = []
    for column in pop.columns:
        if column in _NOT_SAFE or column in H.CATEGORICAL:
            continue
        if column in _EXTRA_CATEGORICAL or column in base.columns:
            continue
        series = pop[column]
        if pd.api.types.is_bool_dtype(series) or pd.api.types.is_numeric_dtype(series):
            candidates.append(column)
    wide = pop[candidates].astype(float)
    wide = wide.fillna(wide.median())
    cats = pd.get_dummies(pop[_EXTRA_CATEGORICAL].astype(str),
                          drop_first=True, dtype=float)
    return H.drop_collinear(
        pd.concat([base.reset_index(drop=True), wide.reset_index(drop=True),
                   cats.reset_index(drop=True)], axis=1).fillna(0.0))


def latent_reconstructibility(pop: pd.DataFrame, latents: pd.DataFrame) -> dict:
    """R² of each latent on the safe features, order-level and customer-level.

    Two levels because they answer different questions. **Order-level** is what
    an adjustment actually exploits: repeated customers let the same latent be
    pinned down from several orders' worth of features. **Customer-level** is
    the honest per-person reconstructibility, one row per customer, and is the
    number to quote when qualifying "unobservable by construction".
    """
    import statsmodels.api as sm

    targets = [*LATENTS, CHOICE_CHANNEL]
    frame = pop.merge(latents[["customer_id", *targets]], on="customer_id",
                      how="left", validate="many_to_one")
    if frame[targets].isna().any().any():
        raise AssertionError("latent join left NULLs; customer_id mismatch.")

    narrow = H.design_matrix(pop).reset_index(drop=True)
    wide = maximal_safe_matrix(pop).reset_index(drop=True)
    keys = frame[["customer_id", *targets]].reset_index(drop=True)

    rows = []
    for scope, base in (("GT-03 confounder set", narrow),
                        ("maximal safe set", wide)):
        X = sm.add_constant(base, has_constant="add").astype(float)
        grouped = pd.concat([keys, base], axis=1).groupby(
            "customer_id", as_index=False).mean()
        Xc = sm.add_constant(grouped[base.columns], has_constant="add").astype(float)
        for target in targets:
            order_r2 = float(sm.OLS(keys[target].to_numpy(float), X).fit().rsquared)
            customer_r2 = float(
                sm.OLS(grouped[target].to_numpy(float), Xc).fit().rsquared)
            rows.append({"scope": scope, "target": target,
                         "kind": "choice channel" if target == CHOICE_CHANNEL
                                 else "latent",
                         "n_features": base.shape[1],
                         "order_r2": order_r2,
                         "n_customers": len(grouped),
                         "customer_r2": customer_r2})
    return {"table": pd.DataFrame(rows)}


# ---------------------------------------------------------------------------
# 3. Which confounders close the gap
# ---------------------------------------------------------------------------


def confounder_contributions(pop: pd.DataFrame, ame: float, naive: float) -> dict:
    """Per-confounder deviance contribution, and the ATT shift on dropping it.

    **Deviance contribution** is the likelihood-ratio statistic for dropping the
    block from the full RTO model — the same definition H6 and BR-09 use, so the
    two are on one scale.

    **Deviance and gap-closing are not the same question**, and reporting only
    the first would answer the ruling's words while missing its point. A term can
    explain a great deal of RTO and shift the COD estimate not at all, if it is
    uncorrelated with COD choice. Both columns are therefore printed, and the
    ordering by each is stated where they disagree.
    """
    import statsmodels.api as sm

    confounders = H.design_matrix(pop)
    treated = pop["is_cod"].astype(bool).to_numpy()
    y = pop["rto_flag"].astype(float).to_numpy()

    def fit(cols):
        X = confounders[list(cols)].copy()
        X.insert(0, "is_cod", pop["is_cod"].astype(float).to_numpy())
        X = sm.add_constant(X, has_constant="add")
        model = _fit(y, X)
        return -2 * float(model.llf), _att(model, X, treated)

    full_dev, full_att = fit(confounders.columns)
    null_dev, null_att = fit([])
    total_explained = null_dev - full_dev
    full_closed = closed(full_att, ame, naive)

    rows = []
    for block, columns in _blocks(confounders.columns).items():
        keep = [c for c in confounders.columns if c not in columns]
        dev, att = fit(keep)
        rows.append({
            "confounder": block, "n_columns": len(columns),
            "deviance_contribution": dev - full_dev,
            "share_of_explained": (dev - full_dev) / total_explained,
            "att_without_pp": att,
            "closed_without": closed(att, ame, naive),
            "closure_lost_pp": (full_closed - closed(att, ame, naive)) * 100,
        })
    table = pd.DataFrame(rows).sort_values("deviance_contribution",
                                           ascending=False, ignore_index=True)
    return {
        "table": table,
        "full_att_pp": full_att, "full_closed": full_closed,
        "unadjusted_att_pp": null_att,
        "unadjusted_closed": closed(null_att, ame, naive),
        "full_deviance": full_dev, "null_deviance": null_dev,
        "total_explained": total_explained,
    }
