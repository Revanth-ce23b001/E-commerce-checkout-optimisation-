"""GT-01 — do the planted RTO coefficients come back out of the data?

Spec §17: *"Logistic regression of ``rto_flag`` on all safe features. >=80% of
planted coefficients fall inside the estimate's 95% CI, and no sign flips on any
Strong/Moderate relationship."*

Two halves, and they are not the same test.

**Signs** ask whether the recovered structure is the planted structure. That is
the question the dataset exists to answer, and it is scale-free.

**Magnitudes** ask whether the recovered numbers equal the planted numbers. That
question has a known, structural answer here, and it is *no* — for a reason that
was decided on the record.

Why magnitudes cannot come back, and why that is not a defect
-------------------------------------------------------------
Decision **A37** raised ``post_dispatch_noise_sd`` from 0.85 to **3.3125** to
bring the achievable AUC ceiling into GT-05's band, and **A38** froze it. The
generator therefore draws

    logit(p) = X @ beta + epsilon,     epsilon ~ N(0, 3.3125**2)

A logistic regression fitted on ``X`` alone cannot see ``epsilon``. It converges
not on ``beta`` but on an **attenuated** ``beta / sqrt(1 + c * sigma**2)`` — the
standard latent-noise attenuation, with ``c ~ 0.346`` for the logistic link. At
sigma = 3.3125 that divisor is roughly **2.2**, so every planted coefficient
should come back at just under half its size.

This is the same root cause as limitation **L8**: the spec's prose figures
(13.4pp / 19.9pp / 33%) belong to sigma = 0.85 and no longer describe this
dataset. GT-01's ">=80% inside the CI" threshold belongs to sigma = 0.85 too. It
was never re-anchored when A37 moved the noise, because no fitted model existed
to notice.

So this module reports **both**:

* the **literal test**, exactly as specified, with no adjustment; and
* the **attenuation diagnostic** — the ratio of fitted to planted across every
  term. If those ratios cluster tightly around one common constant, the failure
  is a uniform rescaling and the planted *structure* is intact. If they scatter,
  something is genuinely wrong.

The diagnostic is reported so a reader can tell those two apart. It is **not**
used to make the test pass. Re-anchoring GT-01 after seeing the result it failed
is a ruling for someone other than whoever ran it — the same position Phase 3
§C.3 took on GT-03, and for the same reason.

What is excluded, and why exclusion is forced
---------------------------------------------
``latent_intent``, ``latent_liquidity`` and ``latent_trust`` are planted terms
that **cannot** be tested: CLAUDE.md invariant 4 keeps them in a PostgreSQL
schema the analyst role is denied on. ``shock.*`` terms are Stage-4 and
hard-blocked. A coefficient on a variable the model is forbidden to see is not
recoverable by any analysis, so the denominator here is the planted terms that
are *estimable from safe features* — stated explicitly rather than silently
dropped, because a denominator chosen after the fact is how a recovery rate gets
flattered.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Spec §7.2's R-table, mapped term by term. Written out rather than inferred from
# |beta| so the classification is auditable against the source document: the
# sign-flip half of GT-01 applies only to Strong/Moderate relationships, and
# deriving that set from the magnitudes would let the test grade its own homework.
SPEC_STRENGTH = {
    "is_cod": ("H1", "Strong"),
    "geo_tier": ("R8", "Moderate"),
    "serviceability_z": ("R9", "Moderate"),
    "address_completeness": ("R17", "Strong"),
    "seller_sla_breach_rate": ("R11", "Moderate"),
    "seller_rating_centered": ("R10", "Moderate"),
    "product_rating_centered": ("R13", "Weak"),
    "log1p_review_count_centered": ("R14", "Weak — PLANTED NULL"),
    "log_order_value": ("R12", "Weak-moderate"),
    "discount_pct_centered": ("R19", "Weak"),
    "cart_size_ge3": ("—", "Weak"),
    "est_delivery_days_centered": ("R15", "Weak-moderate"),
    "category": ("R18", "Weak-moderate"),
    "pit_rto_rate_shrunk": ("R5", "Strong"),
    "is_new_customer": ("R6", "Moderate-strong"),
    "log1p_orders_delivered": ("R7", "Moderate"),
    "pit_cod_share": ("R4", "Weak (indirect on RTO)"),
    "paid_via_switch": ("—", "Moderate"),
    "month_end_x_cod": ("R20", "Moderate, interaction only"),
}

# GT-01's sign clause applies to "any Strong/Moderate relationship". The R-table
# uses five labels, so the boundary has to be drawn explicitly rather than by a
# substring match -- an earlier draft used `str.contains("Strong|Moderate")`,
# which is case-sensitive and therefore silently excluded "Weak-moderate" while
# a comment two lines above claimed it was included. The code and the claim have
# to agree, so both are now written out.
#
# IN scope: the literal reading of "Strong/Moderate".
# WATCHED:  "Weak-moderate" -- outside the clause, but reported separately so the
#           stricter reading is visible instead of being quietly dropped.
IN_SCOPE_FOR_SIGNS = frozenset({
    "Strong", "Moderate-strong", "Moderate", "Moderate, interaction only",
})
WATCHED_FOR_SIGNS = frozenset({"Weak-moderate"})

# Planted terms that no safe-feature model can estimate. Named, not filtered by
# a pattern, so adding a latent later cannot silently enlarge the exemption.
UNTESTABLE = ("latent_intent", "latent_liquidity", "latent_trust")
UNTESTABLE_PREFIX = "shock."

# Reference levels for the two planted categorical blocks. Any choice is valid --
# the contrasts are the same structure read from a different origin -- so these
# match the references `risk/features.py` already uses, to keep one convention.
CATEGORICAL_REFERENCE = {"geo_tier": "METRO", "category": "GROCERY_FMCG"}

# The logistic attenuation constant: Var of the standard logistic is pi^2/3, and
# the usual latent-scale correction is 1 / sqrt(1 + sigma^2 * 3/pi^2).
_LOGIT_ATTENUATION_C = 3.0 / np.pi ** 2


def expected_attenuation(noise_sd: float) -> float:
    """The factor every planted coefficient is expected to shrink by."""
    return 1.0 / np.sqrt(1.0 + _LOGIT_ATTENUATION_C * float(noise_sd) ** 2)


def recovery_matrix(frame: pd.DataFrame, centering: dict) -> pd.DataFrame:
    """Rebuild the generator's OBSERVABLE RTO terms, in the generator's own forms.

    Reconstructed term-for-term against ``generators/predictors.rto_static_terms``
    and ``generators/rto.py``. Using a convenient encoding instead — plain
    ``review_count`` rather than centred ``log1p``, say — would compare a fitted
    coefficient to a planted one that means something different, and the
    mismatch would read as a recovery failure.

    One documented approximation: ``serviceability_z`` is z-scored over this
    population, and the generator z-scored over its own. A different standard
    deviation rescales that one coefficient. It is flagged in the output rather
    than corrected, because correcting it would mean importing a generator-side
    constant into an analyst-side test.
    """
    out = pd.DataFrame(index=frame.index)
    serviceability = frame["serviceability_score"].astype(float)
    sd = serviceability.std(ddof=0)

    out["is_cod"] = frame["is_cod"].astype(float)
    out["serviceability_z"] = ((serviceability - serviceability.mean()) / sd
                               if sd > 0 else 0.0)
    out["address_completeness"] = frame["address_completeness_score"].astype(float)
    out["seller_sla_breach_rate"] = frame["seller_sla_breach_rate"].astype(float)
    out["seller_rating_centered"] = (frame["seller_rating"].astype(float)
                                     - float(centering["seller_rating_center"]))
    out["product_rating_centered"] = (frame["product_rating"].astype(float)
                                      - float(centering["product_rating_center"]))
    log1p_reviews = np.log1p(frame["review_count"].astype(float))
    out["log1p_review_count_centered"] = log1p_reviews - log1p_reviews.mean()
    out["log_order_value"] = np.log(
        frame["order_value"].astype(float).clip(lower=1.0)
        / float(centering["order_value_scale"]))
    out["discount_pct_centered"] = (
        (frame["discount_pct"].astype(float) - float(centering["discount_pct_center"]))
        / float(centering["discount_pct_unit"]))
    out["cart_size_ge3"] = (frame["cart_size"].astype(float) >= 3).astype(float)
    out["est_delivery_days_centered"] = (frame["estimated_delivery_days"].astype(float)
                                         - float(centering["est_delivery_days_center"]))
    out["pit_rto_rate_shrunk"] = frame["pit_rto_rate_shrunk"].astype(float).fillna(0.0)
    out["is_new_customer"] = frame["pit_is_new_customer"].astype(float)
    out["log1p_orders_delivered"] = np.log1p(
        frame["pit_orders_delivered"].astype(float).fillna(0.0))
    out["pit_cod_share"] = frame["pit_cod_share"].astype(float).fillna(0.0)
    out["paid_via_switch"] = frame["paid_via_switch"].astype(float)
    out["month_end_x_cod"] = (frame["is_month_end_window"].astype(float)
                              * out["is_cod"])

    # Categoricals enter as CONTRASTS against a reference level, plus an
    # intercept. The generator plants an absolute level for every category, but
    # **absolute levels of a full dummy set are not identified from data** — only
    # the differences between them are, because adding a constant to every level
    # and subtracting it from the intercept gives an identical likelihood.
    #
    # An earlier draft emitted full dummy sets for both blocks with no intercept.
    # That is worse than unidentified: the two blocks each sum to 1, so they are
    # collinear with EACH OTHER. statsmodels returned NaN standard errors and
    # level estimates near -1.2 against planted values near 0, which read as
    # catastrophic recovery failure and sign flips on geo_tier. It was an
    # identification bug in the test, not a defect in the data.
    for column, reference in CATEGORICAL_REFERENCE.items():
        levels = sorted(frame[column].dropna().unique())
        if reference not in levels:
            raise AssertionError(
                f"{column}: reference level {reference!r} absent from the data "
                f"({levels}).")
        for level in levels:
            if level != reference:
                out[f"{column}[{level}]"] = (frame[column] == level).astype(float)
    return out


def testable_terms(ledger_rto: dict) -> dict:
    """Planted RTO terms a safe-feature model could estimate at all."""
    return {k: v for k, v in ledger_rto.items()
            if k not in UNTESTABLE and not k.startswith(UNTESTABLE_PREFIX)}


def planted_contrasts(planted: dict) -> dict:
    """Convert planted categorical LEVELS into contrasts against the reference.

    The generator plants ``geo_tier[TIER3] = +0.45`` and ``geo_tier[METRO] =
    -0.35`` as absolute levels. A regression cannot recover either — only their
    difference, ``+0.80``. Comparing a fitted contrast to a planted level would
    fail every categorical term for a reason that has nothing to do with the
    data, so the planted side is put on the same footing before comparison.

    Non-categorical terms pass through untouched.
    """
    out = {}
    for term, value in planted.items():
        if "[" not in term:
            out[term] = value
            continue
        block, level = term.split("[", 1)
        level = level.rstrip("]")
        reference = CATEGORICAL_REFERENCE.get(block)
        if reference is None or level == reference:
            continue
        base = planted.get(f"{block}[{reference}]")
        if base is None:
            raise AssertionError(
                f"{block}: no planted value for reference level {reference!r}.")
        out[term] = value - base
    return out


def _rank_rho(scope: pd.DataFrame) -> float:
    """Spearman correlation of |planted| against |fitted| magnitudes.

    Tests the RANKING claim directly instead of inferring it from uniform
    attenuation. That matters here: the attenuation is NOT uniform, so the usual
    "a common rescaling preserves order" argument does not apply and the order
    has to be measured rather than assumed.
    """
    from scipy.stats import spearmanr

    usable = scope[scope["fitted"].notna() & (scope["planted"] != 0)]
    if len(usable) < 3:
        return float("nan")
    return float(spearmanr(usable["planted"].abs(), usable["fitted"].abs()).statistic)


def _require_spec_noise(truth: dict) -> float:
    """The pre-A37 noise level, from the truth file rather than from memory."""
    frozen = truth.get("frozen", {})
    if "post_dispatch_noise_sd_spec_value" not in frozen:
        raise AssertionError(
            "truth['frozen']['post_dispatch_noise_sd_spec_value'] is absent. It "
            "records the noise level A37 superseded, and GT-01's diagnosis "
            "compares the two. Without it the report cannot say what the "
            "threshold would have measured under the noise it was written for.")
    return float(frozen["post_dispatch_noise_sd_spec_value"])


def gt_01(frame: pd.DataFrame, truth: dict, centering: dict) -> dict:
    """Fit the safe-feature RTO model and compare every coefficient to its plant."""
    import statsmodels.api as sm

    ledger = truth["coefficient_ledger"]["rto_model"]
    planted = testable_terms(ledger)
    noise_sd = float(truth["frozen"]["post_dispatch_noise_sd"])

    X = sm.add_constant(recovery_matrix(frame, centering), has_constant="add")
    y = frame["rto_flag"].astype(float).to_numpy()
    model = sm.Logit(y, X.astype(float)).fit(disp=False, maxiter=300)

    rows = []
    for term, value in sorted(planted_contrasts(planted).items()):
        if term not in X.columns:
            rows.append({"term": term, "planted": value, "fitted": np.nan,
                         "ci_low": np.nan, "ci_high": np.nan,
                         "inside_ci": False, "sign_ok": False,
                         "ratio": np.nan, "strength": "NOT IN MATRIX"})
            continue
        fitted = float(model.params[term])
        se = float(model.bse[term])
        low, high = fitted - 1.96 * se, fitted + 1.96 * se
        base = term.split("[")[0]
        spec_ref, strength = SPEC_STRENGTH.get(base, ("—", "Weak"))
        # A planted zero has no sign to flip, and a sign "flip" between two
        # near-zero numbers is noise, not a structural error.
        sign_ok = bool(value == 0 or np.sign(fitted) == np.sign(value))
        rows.append({
            "term": term, "spec": spec_ref, "strength": strength,
            "planted": value, "fitted": fitted,
            "ci_low": low, "ci_high": high,
            "inside_ci": bool(low <= value <= high),
            "sign_ok": sign_ok,
            "ratio": fitted / value if value != 0 else np.nan,
        })
    table = pd.DataFrame(rows)

    in_scope = table["strength"].isin(IN_SCOPE_FOR_SIGNS)
    scope_ratio = table.loc[in_scope & table["ratio"].notna(), "ratio"]
    watched = table["strength"].isin(WATCHED_FOR_SIGNS)
    flips = table[in_scope & ~table["sign_ok"]]
    # A "flip" whose CI straddles zero is not a flip -- it is an estimate that
    # cannot tell the sign apart from noise, which is a different (and much
    # weaker) statement than recovering the wrong direction.
    straddles_zero = (table["ci_low"] <= 0) & (table["ci_high"] >= 0)
    watched_flips = table[watched & ~table["sign_ok"] & ~straddles_zero]
    ratios = table.loc[table["ratio"].notna() & (table["planted"].abs() >= 0.10),
                       "ratio"]

    return {
        "n": int(len(frame)),
        "table": table,
        "n_testable": int(len(table)),
        "n_untestable": int(len(ledger) - len(planted)),
        "untestable": sorted(set(ledger) - set(planted)),
        "share_inside_ci": float(table["inside_ci"].mean()),
        "sign_flips_in_scope": flips["term"].tolist(),
        "n_in_scope_for_signs": int(in_scope.sum()),
        "sign_flips_watched_significant": watched_flips["term"].tolist(),
        "n_watched_for_signs": int(watched.sum()),
        # A49 requires the attenuation factor across the Strong/Moderate set, and
        # requires saying whether it is roughly uniform -- because "uniform
        # attenuation leaves the RANKING intact" is only sound if it IS uniform.
        # The mean alone is actively misleading here and the CV says why.
        "scope_ratio_mean": float(scope_ratio.mean()),
        "scope_ratio_sd": float(scope_ratio.std(ddof=1)),
        "scope_ratio_cv": float(scope_ratio.std(ddof=1) / scope_ratio.mean()),
        "scope_ratio_min": float(scope_ratio.min()),
        "scope_ratio_max": float(scope_ratio.max()),
        "scope_ratio_median": float(scope_ratio.median()),
        "n_attenuated": int((scope_ratio < 1.0).sum()),
        "n_inflated": int((scope_ratio >= 1.0).sum()),
        "magnitude_rank_rho": _rank_rho(table.loc[in_scope]),
        "pseudo_r2": float(model.prsquared),
        # The diagnostic. Not used to grade the test.
        "expected_attenuation": expected_attenuation(noise_sd),
        "median_ratio": float(ratios.median()) if len(ratios) else np.nan,
        "ratio_iqr": (float(ratios.quantile(0.25)), float(ratios.quantile(0.75)))
        if len(ratios) else (np.nan, np.nan),
        "noise_sd": noise_sd,
        # The value A37 replaced. Required, not defaulted: the whole diagnosis
        # rests on comparing the two noise levels, and a hard-coded fallback
        # would let the truth file drop the record of what was superseded while
        # the report went on quoting it.
        "noise_sd_spec": _require_spec_noise(truth),
        "serviceability_z_caveat": (
            "z-scored on the analysis population, not the generator's; this one "
            "term's scale is approximate."),
    }
