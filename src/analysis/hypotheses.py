"""Phase 3 section D — H2 to H6 and H11, each against its pre-registered prior.

Effect sizes, never bare p-values. At 105,605 orders a 0.2pp difference is
significant and meaningless, so every result here carries a magnitude and an
interval; significance is reported only where it changes the reading.

Order of operations, deliberately
---------------------------------
Each hypothesis is measured from the data FIRST. Only then is the result
compared against ``_truth.json``. Three of these (H2, H3, H11) have published
answers, and reading them first would turn the analysis into a search for a
number rather than a measurement of one.

Denominators
------------
* COD-choice hypotheses (H2, H4, H5a) are measured on **all orders** — the
  question is what customers chose, and a cancelled order is still a choice.
* RTO hypotheses (H3, H5b, H6) are measured on **shipped AND NOT censored**.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def resolved(frame: pd.DataFrame) -> pd.DataFrame:
    """The RTO denominator. Defined once so no hypothesis can quietly differ."""
    return frame[frame["is_shipped"] & ~frame["is_censored"]].copy()


def _diff_ci(a: pd.Series, b: pd.Series) -> tuple[float, float, float]:
    """Difference in proportions with a 95% interval, in percentage points."""
    d = a.mean() - b.mean()
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return d * 100, (d - 1.96 * se) * 100, (d + 1.96 * se) * 100


# ---------------------------------------------------------------------------
# H2 — new customers adopt COD more
# ---------------------------------------------------------------------------


def h2_new_customer_cod(frame: pd.DataFrame) -> dict:
    """COD share by orders-completed bucket, then within geo tier.

    Phase 1's stated alternative is that the tenure effect is a **geography
    artefact** — new customers may simply skew Tier-3, where COD is the norm
    regardless of tenure. The two-way cut is the test of that, and it is the
    part that decides whether onboarding is the lever.
    """
    frame = frame.copy()
    frame["is_cod"] = (frame["payment_method"] == "COD").astype(int)
    new = frame[frame["pit_is_new_customer"].astype(bool)]["is_cod"]
    established = frame[~frame["pit_is_new_customer"].astype(bool)]["is_cod"]
    lift, lo, hi = _diff_ci(new, established)

    buckets = pd.cut(frame["pit_orders_delivered"].fillna(0),
                     bins=[-1, 0, 2, 5, 15, np.inf],
                     labels=["0", "1-2", "3-5", "6-15", "16+"])
    by_bucket = (frame.assign(bucket=buckets)
                 .groupby("bucket", observed=True)["is_cod"]
                 .agg(["mean", "size"]).rename(columns={"mean": "cod_share", "size": "orders"}))

    # Does the gradient survive INSIDE each tier?
    within = []
    for tier, group in frame.groupby("geo_tier"):
        n = group[group["pit_is_new_customer"].astype(bool)]["is_cod"]
        e = group[~group["pit_is_new_customer"].astype(bool)]["is_cod"]
        if len(n) < 30 or len(e) < 30:
            continue
        d, l, h = _diff_ci(n, e)
        within.append({"geo_tier": tier, "n_new": len(n), "n_established": len(e),
                       "cod_share_new": float(n.mean()),
                       "cod_share_established": float(e.mean()),
                       "lift_pp": d, "ci_low": l, "ci_high": h})
    return {
        "lift_pp": lift, "ci_low_pp": lo, "ci_high_pp": hi,
        "cod_share_new": float(new.mean()),
        "cod_share_established": float(established.mean()),
        "by_bucket": by_bucket, "within_tier": pd.DataFrame(within),
    }


# ---------------------------------------------------------------------------
# H3 — prior RTO predicts future RTO
# ---------------------------------------------------------------------------


def h3_prior_rto_lift(frame: pd.DataFrame) -> dict:
    """Forward RTO rate by prior-RTO count, as a lift over the no-prior base.

    ``pit_rto_count`` counts only prior orders whose outcome had **resolved**
    before this session began, so the split is time-ordered and cannot leak the
    current outcome backwards.

    Phase 1 flags small-n instability for customers with 1-2 orders, which is
    why the shrunk rate exists — but the hypothesis is stated on the raw count,
    so that is what is measured here.
    """
    base = resolved(frame).copy()
    base["rto"] = base["rto_flag"].fillna(False).astype(int)
    has_prior = base["pit_rto_count"].fillna(0) > 0
    with_prior, without = base[has_prior]["rto"], base[~has_prior]["rto"]
    lift = float(with_prior.mean() / without.mean())

    # Interval by the delta method on a ratio of two proportions.
    p1, n1 = with_prior.mean(), len(with_prior)
    p0, n0 = without.mean(), len(without)
    se_log = np.sqrt((1 - p1) / (p1 * n1) + (1 - p0) / (p0 * n0))
    by_count = (base.assign(prior=base["pit_rto_count"].fillna(0).clip(upper=3))
                .groupby("prior", observed=True)["rto"]
                .agg(["mean", "size"]).rename(columns={"mean": "rto_rate", "size": "orders"}))
    by_count["lift_vs_zero"] = by_count["rto_rate"] / by_count["rto_rate"].iloc[0]
    return {
        "lift": lift,
        "ci_low": float(np.exp(np.log(lift) - 1.96 * se_log)),
        "ci_high": float(np.exp(np.log(lift) + 1.96 * se_log)),
        "rto_rate_with_prior": float(p1), "rto_rate_without": float(p0),
        "n_with_prior": int(n1), "n_without": int(n0),
        "by_count": by_count,
    }


# ---------------------------------------------------------------------------
# H4 — order value and COD preference: inverted U, or monotone?
# ---------------------------------------------------------------------------


def h4_order_value_cod(frame: pd.DataFrame) -> dict:
    """Test for NON-MONOTONICITY explicitly, not for a positive slope.

    Phase 1 predicts an inverted U with the peak below the top decile, and the
    product consequence is specific: if the relationship is non-monotone, a
    **linear value-based COD fee is the wrong instrument** and the policy needs
    bands.

    A linear fit here would come back positive and significant and would be a
    false confirmation. So the test is (a) does a quadratic term improve the
    fit, (b) is it negative, (c) does the fitted peak fall inside the observed
    range, and (d) do the raw deciles actually turn. All four must agree before
    the inverted U is claimed.
    """
    import statsmodels.api as sm

    work = frame.copy()
    work["is_cod"] = (work["payment_method"] == "COD").astype(int)
    work["log_ov"] = np.log(work["order_value"].clip(lower=1.0))

    deciles = pd.qcut(work["order_value"], 10, labels=False, duplicates="drop")
    by_decile = (work.assign(decile=deciles + 1)
                 .groupby("decile", observed=True)
                 .agg(orders=("is_cod", "size"), cod_share=("is_cod", "mean"),
                      median_value=("order_value", "median")))

    x = work["log_ov"].to_numpy()
    centre = x.mean()
    xc = x - centre
    y = work["is_cod"].to_numpy(float)

    linear = sm.Logit(y, sm.add_constant(np.column_stack([xc]))).fit(disp=False)
    quad = sm.Logit(y, sm.add_constant(np.column_stack([xc, xc ** 2]))).fit(disp=False)

    b1, b2 = quad.params[1], quad.params[2]
    peak_log = -b1 / (2 * b2) + centre if b2 != 0 else np.nan
    peak_value = float(np.exp(peak_log))
    lr_stat = 2 * (quad.llf - linear.llf)
    from scipy import stats
    lr_p = float(stats.chi2.sf(lr_stat, 1))

    observed_peak = int(by_decile["cod_share"].idxmax())
    return {
        "by_decile": by_decile,
        "linear_coefficient": float(linear.params[1]),
        "quad_linear_term": float(b1), "quad_square_term": float(b2),
        "quad_square_se": float(quad.bse[2]),
        "square_term_negative": bool(b2 < 0),
        "lr_stat": float(lr_stat), "lr_p": lr_p,
        "fitted_peak_order_value": peak_value,
        "peak_inside_range": bool(work["order_value"].min() < peak_value
                                  < work["order_value"].max()),
        "observed_peak_decile": observed_peak,
        "top_decile_below_peak": bool(
            by_decile.loc[10, "cod_share"] < by_decile["cod_share"].max()),
        "cod_share_at_peak": float(by_decile["cod_share"].max()),
        "cod_share_top_decile": float(by_decile.loc[10, "cod_share"]),
    }


# ---------------------------------------------------------------------------
# H5 — ratings on COD, and the planted null on review_count
# ---------------------------------------------------------------------------


def h5a_ratings_on_cod(frame: pd.DataFrame) -> dict:
    """Seller and product rating on COD selection, controlling for the obvious.

    Phase 1 calls this "the key test for the trust claim". The stated risk is
    that ratings are too compressed (most 4.0-4.5) to carry signal, so the
    effect size matters far more than the p-value.
    """
    import statsmodels.api as sm

    work = frame.copy()
    work["is_cod"] = (work["payment_method"] == "COD").astype(int)
    X = pd.DataFrame({
        "seller_rating": work["seller_rating"].astype(float),
        "product_rating": work["product_rating"].astype(float),
        "log1p_review_count": np.log1p(work["review_count"].astype(float)),
        "log_order_value": np.log(work["order_value"].clip(lower=1.0)),
        "cart_size": work["cart_size"].astype(float),
    })
    X = pd.concat([X, pd.get_dummies(work["category"].astype(str), prefix="cat",
                                     drop_first=True, dtype=float),
                   pd.get_dummies(work["geo_tier"].astype(str), prefix="geo",
                                  drop_first=True, dtype=float)], axis=1)
    model = sm.Logit(work["is_cod"].to_numpy(float),
                     sm.add_constant(X).astype(float)).fit(disp=False, maxiter=200)

    out = {}
    for term in ("seller_rating", "product_rating", "log1p_review_count"):
        coef, se = float(model.params[term]), float(model.bse[term])
        # Effect of a one-star drop, expressed in percentage points of COD share
        # at the population baseline -- interpretable, unlike a log-odds.
        base = float(work["is_cod"].mean())
        pp = (1 / (1 + np.exp(-(np.log(base / (1 - base)) - coef))) - base) * 100
        out[term] = {"coefficient": coef, "se": se,
                     "ci_low": coef - 1.96 * se, "ci_high": coef + 1.96 * se,
                     "one_unit_drop_pp": pp}
    out["rating_spread"] = {
        "seller_p10_p90": [float(work["seller_rating"].quantile(0.10)),
                           float(work["seller_rating"].quantile(0.90))],
        "product_p10_p90": [float(work["product_rating"].quantile(0.10)),
                            float(work["product_rating"].quantile(0.90))],
    }
    return out


def h5b_review_count_on_rto(frame: pd.DataFrame) -> dict:
    """The PLANTED NULL. GT-04 arriving early.

    ``log1p_review_count_centered`` carries **-0.05** in the RTO logit — chosen
    to be indistinguishable from zero at this sample size. If a fitted model
    returns a large or confidently-signed effect here, that is over-fitting
    dressed as a finding, and it is the failure mode GT-04 exists to catch.

    The honest pass condition is not "the coefficient is exactly -0.05". It is
    that the interval is tight around zero and the implied effect is negligible
    against the effects that are real.
    """
    import statsmodels.api as sm

    work = resolved(frame).copy()
    work["rto"] = work["rto_flag"].fillna(False).astype(int)
    X = pd.DataFrame({
        "log1p_review_count": np.log1p(work["review_count"].astype(float)),
        "product_rating": work["product_rating"].astype(float),
        "seller_rating": work["seller_rating"].astype(float),
        "log_order_value": np.log(work["order_value"].clip(lower=1.0)),
        "address_completeness": work["address_completeness_score"].astype(float),
        "estimated_delivery_days": work["estimated_delivery_days"].astype(float),
        "is_cod": (work["payment_method"] == "COD").astype(float),
        "pit_rto_count": work["pit_rto_count"].fillna(0).astype(float),
    })
    X = pd.concat([X, pd.get_dummies(work["geo_tier"].astype(str), prefix="geo",
                                     drop_first=True, dtype=float),
                   pd.get_dummies(work["category"].astype(str), prefix="cat",
                                  drop_first=True, dtype=float)], axis=1)
    model = sm.Logit(work["rto"].to_numpy(float),
                     sm.add_constant(X).astype(float)).fit(disp=False, maxiter=200)

    coef = float(model.params["log1p_review_count"])
    se = float(model.bse["log1p_review_count"])
    spread = float(np.log1p(work["review_count"]).quantile(0.90)
                   - np.log1p(work["review_count"]).quantile(0.10))
    base = float(work["rto"].mean())
    lp = np.log(base / (1 - base))
    pp = (1 / (1 + np.exp(-(lp + coef * spread))) - base) * 100
    return {
        "coefficient": coef, "se": se,
        "ci_low": coef - 1.96 * se, "ci_high": coef + 1.96 * se,
        "p_value": float(model.pvalues["log1p_review_count"]),
        "p10_p90_spread_pp": pp,
        # Benchmark: the effect that IS real, on the same scale.
        "address_completeness_coefficient": float(model.params["address_completeness"]),
        "is_cod_coefficient": float(model.params["is_cod"]),
    }


# ---------------------------------------------------------------------------
# H6 — promise vs realised delay
# ---------------------------------------------------------------------------


def h6_promise_vs_delay(frame: pd.DataFrame) -> dict:
    """Promise length is testable. Realised delay is NOT — and that is the finding.

    Phase 1 asks for the explanatory power of ``estimated_delivery_days``
    (knowable at checkout) against realised delay (knowable only after dispatch),
    in the same model.

    **That model cannot be built from this warehouse**, because the realised
    delay variable is published *conditional on the outcome it would explain*:
    ``fct_order.delivery_delay_days`` exists only on delivered orders, and
    ``fct_delivery_event.attempt_delay_days`` only on returned ones. Knowing
    which column is populated tells you the outcome with certainty, so any model
    containing it is circular. The promise side is measured; the delay side is
    reported as unidentifiable and the reason is stated.
    """
    import statsmodels.api as sm

    work = resolved(frame).copy()
    work["rto"] = work["rto_flag"].fillna(False).astype(int)

    by_promise = (work.groupby("estimated_delivery_days")
                  .agg(orders=("rto", "size"), rto_rate=("rto", "mean")))

    # Promise is endogenous to serviceability, so the within-tier gradient is
    # what matters, not the raw one.
    X = pd.DataFrame({"estimated_delivery_days": work["estimated_delivery_days"].astype(float)})
    X = pd.concat([X, pd.get_dummies(work["geo_tier"].astype(str), prefix="geo",
                                     drop_first=True, dtype=float)], axis=1)
    model = sm.Logit(work["rto"].to_numpy(float),
                     sm.add_constant(X).astype(float)).fit(disp=False, maxiter=200)
    coef = float(model.params["estimated_delivery_days"])
    base = float(work["rto"].mean())
    lp = np.log(base / (1 - base))
    per_day_pp = (1 / (1 + np.exp(-(lp + coef))) - base) * 100

    delivered = work[work["is_delivered"].fillna(False)]
    returned = work[work["rto"] == 1]
    return {
        "by_promise": by_promise,
        "promise_coefficient": coef,
        "promise_se": float(model.bse["estimated_delivery_days"]),
        "promise_per_extra_day_pp": per_day_pp,
        "promise_pseudo_r2": float(model.prsquared),
        # The observability wall.
        "delivered_with_delay": int(delivered["delivery_delay_days"].notna().sum()),
        "delivered_without_delay": int(delivered["delivery_delay_days"].isna().sum()),
        "returned_with_attempt_delay": int(returned["attempt_delay_days"].notna().sum()),
        "returned_without_attempt_delay": int(returned["attempt_delay_days"].isna().sum()),
        "mean_delay_delivered": float(delivered["delivery_delay_days"].mean()),
        "mean_attempt_delay_returned": float(returned["attempt_delay_days"].mean()),
        "delay_identifiable": False,
    }


# ---------------------------------------------------------------------------
# H11 — payment failure as a cause of COD, and what kind of COD it makes
# ---------------------------------------------------------------------------


def h11_switch_cod(frame: pd.DataFrame, sessions: pd.DataFrame) -> dict:
    """Share of COD born from a failed prepaid attempt, and how it then behaves.

    The share is the headline, but the **consequence** is the product argument:
    if switch-COD orders return *less* than intent-COD orders, then fixing
    payment reliability recovers the better half of the COD book rather than a
    random slice, and the intervention is worth more than its size suggests.
    """
    cod_orders = int((sessions["final_payment_method"] == "COD").sum())
    switched = int(sessions["switched_to_cod_after_failure"].sum())

    base = resolved(frame).copy()
    base["rto"] = base["rto_flag"].fillna(False).astype(int)
    cod = base[base["payment_method"] == "COD"]
    switch = cod[cod["paid_via_switch"].fillna(False).astype(bool)]
    intent = cod[~cod["paid_via_switch"].fillna(False).astype(bool)]
    prepaid = base[base["payment_method"] == "PREPAID"]

    diff, lo, hi = _diff_ci(switch["rto"], intent["rto"])
    return {
        "pct_of_cod_from_failure": switched / cod_orders,
        "switch_cod_orders": switched, "cod_orders": cod_orders,
        "rto_rate_switch_cod": float(switch["rto"].mean()),
        "rto_rate_intent_cod": float(intent["rto"].mean()),
        "rto_rate_prepaid": float(prepaid["rto"].mean()),
        "n_switch": len(switch), "n_intent": len(intent),
        "difference_pp": diff, "ci_low_pp": lo, "ci_high_pp": hi,
    }
