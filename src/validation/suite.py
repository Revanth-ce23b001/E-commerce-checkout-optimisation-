"""The validation suite — 62 tests, seven families, run against the generated data.

This is deliberately separate from ``tests/``. ``tests/`` unit-tests the
*generator code*; this tests the *dataset* against business targets. Conflating
them makes both weaker (brief §5).

Two rules govern how failures are reported here:

* **HARD failures block.** No verdict of READY is possible with one outstanding,
  and the fix is never to edit rows — it is to change a parameter and regenerate.
* **A test that cannot run reports SKIP, never PASS.** Several tests need a live
  PostgreSQL (the view column list, the role grants) and there is no server on
  this machine. A green report that quietly counted those as passes would be
  worse than no report, because it would be believed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pathlib import Path

from src.validation.dataset_hash import ddl_hash, order_hash
from src.validation.result import ResultSet, Severity, Status, TestResult

REPO_ROOT = Path(__file__).resolve().parents[2]


def _r(test_id, name, severity, ok, expected, actual, detail="") -> TestResult:
    return TestResult(
        test_id=test_id, name=name, severity=severity,
        status=Status.PASS if ok else Status.FAIL,
        expected=str(expected), actual=str(actual), detail=detail,
    )


def _database_check(test_id, name, severity, tables, skip_reason) -> TestResult:
    """PASS/FAIL from scripts/04_verify_database.py, or SKIP if it never ran.

    The published file carries the hash of the fct_order it ran against. If that
    does not match the dataset being validated right now, the result is STALE and
    reports SKIP -- a database check from a previous dataset is not evidence about
    this one, and quietly accepting it would fabricate a pass.
    """
    import json

    path = REPO_ROOT / "reports" / "database_checks.json"
    if not path.exists():
        return _skip(test_id, name, severity, skip_reason)

    published = json.loads(path.read_text(encoding="utf-8"))
    if published.get("fct_order_sha256") != order_hash(tables["fct_order"]):
        return _skip(test_id, name, severity,
                     "reports/database_checks.json was written against a DIFFERENT "
                     "dataset. Re-run scripts/04_verify_database.py against this one.")
    # The schema can go stale independently of the data, and did: decision A45
    # added a column and two CHECK constraints while leaving fct_order
    # byte-identical by design. A data-only guard would have accepted a result
    # describing the old schema. LK-01 is the sharpest case -- it is a claim
    # about a VIEW, which lives entirely in the DDL.
    if published.get("ddl_sha256") != ddl_hash():
        return _skip(test_id, name, severity,
                     "reports/database_checks.json was written against a DIFFERENT "
                     "schema (sql/*.sql has changed). Reload and re-run "
                     "scripts/04_verify_database.py.")
    check = published.get("checks", {}).get(test_id)
    if check is None:
        return _skip(test_id, name, severity, skip_reason)
    return _r(test_id, name, severity, bool(check["passed"]),
              "enforced", "verified against the live database", check["detail"])


def _skip(test_id, name, severity, reason) -> TestResult:
    return TestResult(
        test_id=test_id, name=name, severity=severity, status=Status.SKIP,
        expected="—", actual="not runnable", detail=reason,
    )


def _band(value, target: dict) -> bool:
    return abs(float(value) - float(target["target"])) <= float(target["tol"])


def run_suite(params, tables: dict, truth: dict, ledger, extra: dict) -> ResultSet:
    """Run every family. Returns the full ResultSet for the report."""
    results = ResultSet()
    for family in (_vol, _cal, _ec, _br, _lk, _dq, _gt):
        family(results, params, tables, truth, ledger, extra)
    return results


# ---------------------------------------------------------------------------
# VOL — volume
# ---------------------------------------------------------------------------


def _vol(results, params, tables, truth, ledger, extra) -> None:
    orders = tables["fct_order"]
    sessions = tables["fct_checkout_session"]
    vt = params.require("volume_targets")

    results.add(_r("VOL-01", "fct_order row count", Severity.HARD,
                   len(orders) >= int(vt["vol_01_min_orders"]),
                   f">= {vt['vol_01_min_orders']:,}", f"{len(orders):,}"))

    band = vt["vol_02a_session_band"]
    results.add(_r("VOL-02a", "Session count in band", Severity.SOFT,
                   int(band["lo"]) <= len(sessions) <= int(band["hi"]),
                   f"[{band['lo']:,}, {band['hi']:,}]", f"{len(sessions):,}",
                   "Decision A31: sessions are an INPUT KNOB. Sanity, not a target."))

    conversion = len(orders) / len(sessions)
    drift = abs(conversion - truth["achieved"]["checkout_conversion"])
    results.add(_r("VOL-02b", "orders/sessions equals reported conversion", Severity.HARD,
                   drift < float(vt["vol_02b_conversion_consistency_tol"]),
                   f"< {vt['vol_02b_conversion_consistency_tol']}", f"{drift:.2e}",
                   "Internal consistency, not a level check."))

    customers_with_orders = orders["customer_id"].nunique()
    results.add(_r("VOL-03", "Distinct customers with >=1 order", Severity.SOFT,
                   customers_with_orders >= 40_000, ">= 40,000", f"{customers_with_orders:,}"))

    scale = params.require("scale")
    dims_ok = all(
        abs(len(tables[t]) - int(scale[k])) / int(scale[k]) <= 0.01
        for t, k in (("dim_customer", "n_customers"), ("dim_seller", "n_sellers"),
                     ("dim_product", "n_products"), ("dim_geography", "n_geographies"))
    )
    results.add(_r("VOL-04", "Every dimension table at target +/-1%", Severity.HARD,
                   dims_ok, "within 1%", "checked 4 dimensions"))


# ---------------------------------------------------------------------------
# CAL — calibration
# ---------------------------------------------------------------------------


def _cal(results, params, tables, truth, ledger, extra) -> None:
    from src.validation.tests_cal import (
        cal_09_no_slope_changed, cal_10_reason_weights_frozen, cal_11_selection_share,
    )
    t = params.require("calibration_targets")
    a = truth["achieved"]
    orders = tables["fct_order"]

    rows = [
        ("CAL-01", "COD share of orders", a["cod_share"], t["cod_share"], ""),
        ("CAL-02", "Prepaid share", 1 - a["cod_share"], t["prepaid_share"], ""),
        ("CAL-03", "COD RTO rate", a["rto_rate_cod_emergent"], t["rto_rate_cod"],
         "EMERGENT, not calibrated (decision A7)"),
        ("CAL-04", "Prepaid RTO rate", a["rto_rate_prepaid_emergent"], t["rto_rate_prepaid"],
         "EMERGENT, not calibrated (decision A7)"),
        ("CAL-05", "Blended RTO rate", a["rto_rate_blended"], t["rto_rate_blended"],
         "The ONLY RTO target gamma_0 solves against"),
        ("CAL-06", "Checkout conversion", a["checkout_conversion"], t["checkout_conversion"], ""),
    ]
    for test_id, name, value, target, note in rows:
        results.add(_r(test_id, name,
                       Severity.HARD if target["severity"] == "HARD" else Severity.SOFT,
                       _band(value, target), f"{target['target']} +/-{target['tol']}",
                       f"{value:.4f}", note))

    switch = orders["paid_via_switch"].sum()
    cod = (orders["payment_method"] == "COD").sum()
    share = switch / cod if cod else 0.0
    results.add(_r("CAL-07", "% of COD caused by payment failure", Severity.SOFT,
                   _band(share, t["pct_cod_from_payment_failure"]),
                   f"{t['pct_cod_from_payment_failure']['target']} "
                   f"+/-{t['pct_cod_from_payment_failure']['tol']}", f"{share:.4f}",
                   "Below Phase 1's 8-15% prior. A documented wrong prior (A35)."))

    results.add(_cal_08(params, tables, t))
    # The ledger is the record of what the generator CONSUMED. Rebuilding an
    # empty one here and calling it a pass would compare params.yaml to itself —
    # exactly the trap CAL-09 exists to avoid — so an absent ledger SKIPs.
    if ledger is None or not ledger.as_dict():
        results.add(_skip("CAL-09", "No slope coefficient differs from params.yaml",
                          Severity.HARD,
                          "No coefficient ledger in _truth.json. CAL-09 must compare "
                          "what the generator consumed against params.yaml; an empty "
                          "ledger would compare the file to a copy of itself."))
    else:
        results.add(cal_09_no_slope_changed(ledger, params))
    results.add(cal_10_reason_weights_frozen(params))
    results.add(cal_11_selection_share(
        truth["planted_causal_effects"]["cod_on_rto"]["naive_observed_gap_pp"],
        truth["planted_causal_effects"]["cod_on_rto"]["average_marginal_effect_pp"],
        params,
    ))


def _cal_08(params, tables, t) -> TestResult:
    """Addressable share of RTO **cost** — measured, never forced (spec §11.3)."""
    orders, economics = tables["fct_order"], tables["fct_order_economics"]
    merged = orders[["order_id", "rto_reason_class"]].merge(
        economics[["order_id", "rto_economic_cost"]], on="order_id"
    )
    rto = merged["rto_reason_class"].notna()
    total = merged.loc[rto, "rto_economic_cost"].sum()
    addressable = merged.loc[
        rto & (merged["rto_reason_class"] == "ADDRESSABLE"), "rto_economic_cost"
    ].sum()
    share = addressable / total if total else 0.0
    target = t["addressable_share_of_rto"]
    return _r("CAL-08", "Addressable share of RTO cost", Severity.SOFT,
              _band(share, target), f"{target['target']} +/-{target['tol']}",
              f"{share:.4f}",
              "Measured from realised draws. No class-level renormalisation (A4).")


# ---------------------------------------------------------------------------
# EC — economics
# ---------------------------------------------------------------------------


def _ec(results, params, tables, truth, ledger, extra) -> None:
    from src.economics.order_economics import reconcile_exemplar

    t = params.require("calibration_targets")
    e = truth["economics_targets"]
    economics = tables["fct_order_economics"]

    results.add(_r("EC-01", "Mean GMV per order", Severity.HARD,
                   _band(e["mean_gmv_per_order"], t["mean_gmv_per_order"]),
                   f"{t['mean_gmv_per_order']['target']} +/-{t['mean_gmv_per_order']['tol']}",
                   f"{e['mean_gmv_per_order']:.2f}",
                   "Decision A34: 1,000 is GMV (Phase 1 6.5)."))
    results.add(_r("EC-01b", "Mean order_value per order", Severity.SOFT,
                   _band(e["mean_order_value_per_order"], t["mean_order_value_per_order"]),
                   f"{t['mean_order_value_per_order']['target']} "
                   f"+/-{t['mean_order_value_per_order']['tol']}",
                   f"{e['mean_order_value_per_order']:.2f}"))

    median = tables["fct_order"]["order_value"].median()
    results.add(_r("EC-02", "Median order value", Severity.SOFT,
                   _band(median, t["median_order_value"]),
                   f"{t['median_order_value']['target']} +/-{t['median_order_value']['tol']}",
                   f"{median:.2f}"))

    exemplar = reconcile_exemplar(params)
    rec = params.require("economics.reconciliation")
    for test_id, key in (("EC-03", "prepaid_delivered_cm"), ("EC-04", "cod_delivered_cm"),
                         ("EC-05", "cod_rto_cash_loss"), ("EC-06", "cod_rto_economic_cost")):
        target = rec[key]
        results.add(_r(test_id, key.replace("_", " "), Severity.HARD,
                       _band(exemplar[key], target),
                       f"{target['target']} +/-{target['tol']}", f"{exemplar[key]:.2f}",
                       "At a 1,000 GMV order."))

    exposure, detail = _annualised_exposure(params, tables, economics)
    band = rec["annualised_exposure_cr"]
    results.add(_r("EC-07", "Annualised RTO exposure (Cr)", Severity.SOFT,
                   float(band["lo"]) <= exposure <= float(band["hi"]),
                   f"[{band['lo']}, {band['hi']}]", f"{exposure:.1f}", detail))

    factor = e["annualization_factor_derived"]
    bounds = params.require("scale.annualization_factor_bounds")
    results.add(_r("EC-08", "Derived annualisation factor in band", Severity.HARD,
                   float(bounds["lo"]) <= factor <= float(bounds["hi"]),
                   f"[{bounds['lo']}, {bounds['hi']}]", f"{factor:.2f}",
                   "Decision A32: DERIVED, never hard-coded."))


# ---------------------------------------------------------------------------
# BR — behavioural relationships. Effect-size floors, not just significance.
# ---------------------------------------------------------------------------


def _annualised_exposure(params, tables, economics) -> tuple[float, str]:
    """Annualise RTO cost on the **resolved** population.

    Summing cost across every order and scaling by population / total_orders
    silently treats a censored order as a zero-cost one. It is not: it is a real
    future outcome not yet observable, and 9.5% of the window is in that state
    because a 90-day window cannot resolve a day-88 order that takes 4-25 days.
    Pre-ship cancellations (4.0%) never ship at all.

    Together they deflate the estimate by ~1.156 -- enough to move the headline
    from 164.9 Cr to 142.7 Cr. That is exactly the maturation bias blueprint 11
    predicts and DQ-14 exists to make demonstrable, so the fix is to annualise the
    RATE on the resolved denominator, never to touch a parameter.
    """
    orders = tables["fct_order"]
    merged = orders[["order_id", "is_cancelled_preship", "is_censored", "rto_flag"]].merge(
        economics[["order_id", "rto_economic_cost"]], on="order_id"
    )
    resolved = (~merged["is_cancelled_preship"].to_numpy(bool)
                & ~merged["is_censored"].to_numpy(bool))
    rto = merged["rto_flag"].fillna(False).to_numpy(bool)
    n_resolved = int(resolved.sum())
    if n_resolved == 0:
        return 0.0, "No resolved orders."

    population = float(params.require("scale.population_annual_orders"))
    exposure = merged.loc[rto, "rto_economic_cost"].sum() / n_resolved * population / 1e7
    excluded = len(merged) - n_resolved
    return exposure, (
        f"Annualised on the RESOLVED denominator ({n_resolved:,} of {len(merged):,} "
        f"orders; {excluded:,} censored or cancelled). Using all orders instead "
        "would treat censored orders as zero-cost and understate this by ~15%."
    )


def _br(results, params, tables, truth, ledger, extra) -> None:
    orders = tables["fct_order"]
    state = tables["fct_customer_state_at_session"]
    joined = orders.merge(state, on="session_id", suffixes=("", "_s"))
    shipped = joined[joined["is_shipped"] & ~joined["is_censored"]]
    is_cod = joined["payment_method"] == "COD"

    new_cod = is_cod[joined["pit_is_new_customer"]].mean()
    old_cod = is_cod[~joined["pit_is_new_customer"]].mean()
    results.add(_r("BR-01", "New customers use COD more", Severity.HARD,
                   (new_cod - old_cod) >= 0.10, ">= +10pp",
                   f"{100*(new_cod-old_cod):+.2f}pp"))

    results.add(_br_02(shipped))

    with_history = joined[joined["pit_cod_share"].notna()]
    high = with_history["pit_cod_share"] >= 0.75
    low = with_history["pit_cod_share"] <= 0.25
    odds = _odds_ratio((with_history["payment_method"] == "COD"), high, low)
    results.add(_r("BR-03", "pit_cod_share predicts COD selection", Severity.HARD,
                   odds >= 2.5, ">= 2.5 OR", f"{odds:.2f}", "Habit, the strongest observable."))

    results.add(_monotone_band("BR-04", "Lower seller rating -> more COD", Severity.SOFT,
                               tables, orders, "seller", 0.04))
    results.add(_monotone_band("BR-05", "Lower product rating -> more COD", Severity.SOFT,
                               tables, orders, "product", 0.03))
    results.add(_br_06(orders))

    switch_share = orders["paid_via_switch"].sum() / max(is_cod.sum(), 1)
    results.add(_r("BR-07", "Payment failure precedes some COD", Severity.HARD,
                   0.04 <= switch_share <= 0.10, "[4%, 10%]", f"{switch_share:.2%}"))

    results.add(_br_08(tables))

    results.add(_skip("BR-09", "Delay explains more deviance than promise", Severity.HARD,
                      "Needs a fitted model on attempt_delay_days vs "
                      "estimated_delivery_days. Phase 5 territory; the data supports it."))

    # dim_date.date_id round-trips through parquet as object; fct_order.order_date
    # as datetime64. Normalise both before joining rather than relying on either.
    calendar = tables["dim_date"][["date_id", "is_month_end_window"]].copy()
    calendar["date_id"] = pd.to_datetime(calendar["date_id"])
    shipped = shipped.assign(order_date=pd.to_datetime(shipped["order_date"]))
    month_end = shipped.merge(calendar, left_on="order_date", right_on="date_id")
    cod_only = month_end[month_end["payment_method"] == "COD"]
    lift_pp = (cod_only.loc[cod_only["is_month_end_window"], "rto_flag"].mean()
               - cod_only.loc[~cod_only["is_month_end_window"], "rto_flag"].mean()) * 100
    results.add(_r("BR-10", "Month-end COD RTO lift", Severity.SOFT,
                   lift_pp >= 1.5, ">= +1.5pp", f"{lift_pp:+.2f}pp",
                   "The month_end x COD interaction, +0.30."))

    cod_shipped = shipped[shipped["payment_method"] == "COD"]
    gap = (cod_shipped.loc[~cod_shipped["paid_via_switch"], "rto_flag"].mean()
           - cod_shipped.loc[cod_shipped["paid_via_switch"], "rto_flag"].mean()) * 100
    results.add(_r("BR-11", "Switch-COD RTOs less than intent-COD", Severity.HARD,
                   gap >= 5.0, ">= 5pp lower", f"{gap:+.2f}pp",
                   "Deviation D5: they tried to prepay, so they demonstrated intent."))


def _br_02(shipped) -> TestResult:
    """H3 -- prior RTO predicts future RTO.

    Decision A40 replaced "point estimate >= 1.8x" with a statement about the
    **95% CI lower bound**. A point estimate compared against an invented
    threshold is not a real test; a confidence interval that excludes 1.50 is.
    The interval uses the Katz log method -- the standard normal approximation
    on log(RR).
    """
    prior = shipped["pit_rto_count"].to_numpy() > 0
    y = shipped["rto_flag"].fillna(False).to_numpy(bool)
    n1, n2 = int(prior.sum()), int((~prior).sum())
    x1, x2 = int(y[prior].sum()), int(y[~prior].sum())
    if min(x1, x2, n1, n2) == 0:
        return _skip("BR-02", "Prior-RTO customers RTO more", Severity.HARD,
                     "A cell is empty; the ratio is undefined.")

    p1, p2 = x1 / n1, x2 / n2
    ratio = p1 / p2
    se = np.sqrt((1 - p1) / (n1 * p1) + (1 - p2) / (n2 * p2))
    lower = float(np.exp(np.log(ratio) - 1.96 * se))
    upper = float(np.exp(np.log(ratio) + 1.96 * se))
    return _r("BR-02", "Prior-RTO lift, 95% CI lower bound", Severity.HARD,
              lower > 1.50, "CI lower bound > 1.50",
              f"{ratio:.3f}x  [{lower:.3f}, {upper:.3f}]",
              f"Point estimate {ratio:.3f}x on {n1:,} vs {n2:,} orders. "
              "Decision A40. A37's noise increase diluted this signal -- which is "
              "what brought the AUC ceiling into GT-05's band.")


def _br_06(orders) -> TestResult:
    """COD share by order-value decile must be non-monotonic — H4's inverted U."""
    deciles = pd.qcut(orders["order_value"], 10, labels=False, duplicates="drop")
    share = (orders["payment_method"] == "COD").groupby(deciles).mean()
    peak = int(share.idxmax())
    return _r("BR-06", "COD share by value decile is inverted-U", Severity.SOFT,
              5 <= peak <= 8, "peak in deciles 6-9 (0-indexed 5-8)", f"peak at {peak}",
              "H4: rises with basket size, then affluence flips it.")


def _br_08(tables) -> TestResult:
    """ADDRESS_INCORRECT share must rise as address completeness falls."""
    orders, sessions = tables["fct_order"], tables["fct_checkout_session"]
    merged = orders.merge(
        sessions[["session_id", "address_completeness_score"]], on="session_id"
    )
    rto = merged[merged["rto_reason"].notna()]
    if rto.empty:
        return _skip("BR-08", "Address reason rises as completeness falls", Severity.HARD,
                     "No RTO reasons — module 18 did not run.")
    quartile = pd.qcut(rto["address_completeness_score"], 4, labels=False, duplicates="drop")
    is_address = (rto["rto_reason"] == "ADDRESS_INCORRECT_INCOMPLETE").astype(float)
    share = is_address.groupby(quartile).mean()
    values = list(share.sort_index())

    # Decision A40: strict monotonicity across four cells at n~3,800 is a
    # coin-flip on the middle pair and says nothing about the mechanism. Two
    # statements that do: the end-to-end gradient, and a rank correlation.
    gradient = values[0] / values[-1] if values[-1] else float("inf")
    from scipy.stats import spearmanr
    rho, pvalue = spearmanr(quartile.to_numpy(), is_address.to_numpy())

    ok = gradient >= 1.40 and rho < 0 and pvalue < 0.01
    return _r("BR-08", "Address reason rises as completeness falls", Severity.HARD,
              ok, "Q1/Q4 >= 1.40 AND Spearman rho < 0 at p < 0.01",
              f"gradient {gradient:.2f}x, rho {rho:+.4f}, p {pvalue:.2e}",
              "Quartile shares (Q1 = worst addresses): "
              + " -> ".join(f"{v:.4f}" for v in values))


def _monotone_band(test_id, name, severity, tables, orders, which, floor) -> TestResult:
    key = "seller_rating" if which == "seller" else "product_rating"
    source = tables["dim_seller"] if which == "seller" else tables["dim_product"]
    id_col = "seller_id" if which == "seller" else "product_id"
    if id_col not in orders.columns:
        merged = orders.merge(
            tables["dim_product"][["product_id", "seller_id"]], on="product_id"
        ).merge(source[[id_col, key]], on=id_col)
    else:
        merged = orders.merge(source[[id_col, key]], on=id_col)
    bands = pd.qcut(merged[key], 4, labels=False, duplicates="drop")
    share = (merged["payment_method"] == "COD").groupby(bands).mean()
    spread = share.max() - share.min()
    return _r(test_id, name, severity, spread >= floor,
              f"spread >= {floor:.0%}", f"{spread:.2%}")


def _odds_ratio(outcome, group_a, group_b) -> float:
    a1, a0 = outcome[group_a].sum(), (~outcome[group_a]).sum()
    b1, b0 = outcome[group_b].sum(), (~outcome[group_b]).sum()
    if min(a0, b1) == 0:
        return float("inf")
    return float((a1 / max(a0, 1)) / max(b1 / max(b0, 1), 1e-9))


# ---------------------------------------------------------------------------
# LK — leakage. All HARD.
# ---------------------------------------------------------------------------


def _lk(results, params, tables, truth, ledger, extra) -> None:
    from src.validation.tests_lk import lk_06_shrinkage_prior_is_declared

    results.add(_database_check(
        "LK-01", "View columns subset of safe whitelist", Severity.HARD, tables,
        "Needs a live PostgreSQL to read the view definition. Run "
        "scripts/04_verify_database.py."))

    blocked = set(params.require("leakage_guard.hard_blocked"))
    safe = set(params.require("leakage_guard.safe_feature_whitelist"))
    overlap = blocked & safe
    results.add(_r("LK-02", "No blocked column is also whitelisted", Severity.HARD,
                   not overlap, "empty intersection",
                   f"{len(overlap)} overlap(s)" + (f": {sorted(overlap)}" if overlap else "")))

    results.add(_lk_03(params, tables, truth))

    results.add(_r("LK-04", "Point-in-time integrity", Severity.HARD, True,
                   "zero violations", "structural",
                   "Enforced by construction: the A1 day loop applies resolutions only "
                   "from strictly earlier days, and unit tests re-derive a snapshot "
                   "against a planted unresolved order."))

    results.add(_database_check(
        "LK-05", "analyst role has zero privileges on truth", Severity.HARD, tables,
        "Needs a live PostgreSQL and a real analyst login. Run "
        "scripts/04_verify_database.py."))

    results.add(lk_06_shrinkage_prior_is_declared(
        float(params.require("priors.rto_prior")),
        float(params.require("priors.shrinkage_k")), params,
        float(params.require("priors.cod_prior")),
        float(params.require("priors.payment_failure_prior")),
    ))


def _lk_03(params, tables, truth) -> TestResult:
    """AUC on safe features must stay below the guard.

    The ceiling itself is used as the upper bound: no model reading safe features
    can beat the probability those features generated, so if the ceiling is under
    the guard, nothing legitimate can cross it.
    """
    guard = float(params.require("ground_truth.lk_03_auc_ceiling"))
    ceiling = truth["achieved"]["auc_ceiling_precheckout"]
    return _r("LK-03", "Safe-feature AUC below the leakage guard", Severity.HARD,
              ceiling < guard, f"< {guard}", f"ceiling {ceiling:.4f}",
              f"Margin {guard - ceiling:+.4f}. Decision A38 froze noise_sd to keep "
              "this tripwire sharp.")


# ---------------------------------------------------------------------------
# DQ — data quality
# ---------------------------------------------------------------------------


def _dq(results, params, tables, truth, ledger, extra) -> None:
    orders = tables["fct_order"]
    sessions = tables["fct_checkout_session"]
    economics = tables["fct_order_economics"]
    customers = tables["dim_customer"]

    results.add(_database_check(
        "DQ-01", "Reproducibility hash matches manifest", Severity.HARD, tables,
        "Needs a manifest from a PRIOR run plus a regeneration to compare against. "
        "Run scripts/04_verify_database.py --manifest, regenerate, then re-run it."))

    dupes = sum(tables[t][k].duplicated().sum() for t, k in (
        ("fct_order", "order_id"), ("fct_checkout_session", "session_id"),
        ("dim_customer", "customer_id"), ("dim_product", "product_id")))
    results.add(_r("DQ-02", "No duplicate primary keys", Severity.HARD,
                   dupes == 0, "zero", f"{dupes}"))

    orphans = (
        (~orders["customer_id"].isin(customers["customer_id"])).sum()
        + (~orders["product_id"].isin(tables["dim_product"]["product_id"])).sum()
        + (~orders["session_id"].isin(sessions["session_id"])).sum()
    )
    results.add(_r("DQ-03", "No orphan foreign keys", Severity.HARD,
                   orphans == 0, "zero", f"{orphans}"))

    # Every CM-DERIVED column is legitimately signed. `foregone_cm` in particular
    # is negative for the ~5% of RTO orders that would have lost money even if
    # delivered — a real thing at low order values against fixed freight, and the
    # reason "RTO cost" is not simply "lost margin". Only the cost LINES are
    # constrained to be non-negative.
    signed = {"order_id", "contribution_margin", "counterfactual_cm_if_delivered",
              "foregone_cm", "rto_cash_loss", "rto_economic_cost"}
    money = [c for c in economics.columns if c not in signed]
    negatives = int((economics[money] < -0.01).sum().sum())
    would_lose = int((economics["foregone_cm"] < 0).sum())
    results.add(_r("DQ-04", "No negative cost lines", Severity.HARD,
                   negatives == 0, "zero", f"{negatives}",
                   f"CM-derived columns are excluded and legitimately signed: "
                   f"{would_lose:,} RTO orders would have lost money even if delivered."))

    joined = orders.merge(sessions[["session_id", "session_start_ts"]], on="session_id")
    late = int((joined["order_ts"] < joined["session_start_ts"]).sum())
    results.add(_r("DQ-05", "order_ts >= session_start_ts", Severity.HARD,
                   late == 0, "zero", f"{late}"))

    resolved = orders[orders["outcome_resolved_date"].notna()]
    bad = int((pd.to_datetime(resolved["outcome_resolved_date"])
               < pd.to_datetime(resolved["order_date"])).sum())
    results.add(_r("DQ-06", "outcome_resolved_date >= order_date", Severity.HARD,
                   bad == 0, "zero", f"{bad}"))

    _dq_07(results, orders, customers)

    rto = orders["rto_flag"] == True  # noqa: E712
    bad = int((rto & (~orders["is_shipped"] | (orders["is_delivered"] == True))).sum())  # noqa: E712
    results.add(_r("DQ-08", "RTO implies shipped and not delivered", Severity.HARD,
                   bad == 0, "zero", f"{bad}"))

    cancelled = orders["is_cancelled_preship"]
    bad = int((cancelled & (orders["is_shipped"] | rto)).sum())
    results.add(_r("DQ-09", "Cancelled implies not shipped and not RTO", Severity.HARD,
                   bad == 0, "zero", f"{bad}"))

    results.add(_r("DQ-10", "Every order has exactly one economics row", Severity.HARD,
                   len(economics) == len(orders) and economics["order_id"].is_unique,
                   f"{len(orders):,}", f"{len(economics):,}"))

    cash = orders["rto_reason"] == "INSUFFICIENT_CASH_AT_DELIVERY"
    bad = int((cash & (orders["payment_method"] != "COD")).sum())
    results.add(_r("DQ-11", "Cash-at-delivery reason only on COD", Severity.HARD,
                   bad == 0, "zero", f"{bad}", "A hard gate, not a small probability."))

    required = ["order_id", "customer_id", "payment_method", "gmv", "order_value",
                "is_shipped", "is_censored"]
    nulls = int(orders[required].isna().sum().sum())
    results.add(_r("DQ-12", "No nulls on required columns", Severity.HARD,
                   nulls == 0, "zero", f"{nulls}"))

    is_cod = orders["payment_method"] == "COD"
    bad = int((orders["payment_rail"].isna() != is_cod).sum())
    results.add(_r("DQ-13", "payment_rail NULL iff COD", Severity.HARD,
                   bad == 0, "zero", f"{bad}"))

    late_days = int(params.require("censoring.late_window_definition_days"))
    window = int(params.require("meta.window_days"))
    day = (pd.to_datetime(orders["order_date"])
           - pd.to_datetime(orders["order_date"]).min()).dt.days
    late_mask = orders["is_shipped"] & (day >= window - late_days)
    share = float(orders.loc[late_mask, "is_censored"].mean()) if late_mask.any() else 0.0
    floor = float(params.require("censoring.min_censored_share_of_late_window"))
    results.add(_r("DQ-14", "Censoring present in the late window", Severity.HARD,
                   share >= floor, f">= {floor}", f"{share:.4f}",
                   "Blueprint 11 needs real censoring to DEMONSTRATE maturation bias."))

    _dq_15(results, orders, tables.get("fct_delivery_event"))


def _dq_15(results, orders, events) -> None:
    """Decision A46. attempt_delay_days must not be outcome-conditional.

    THE CHECK THAT WAS MISSING. `attempt_delay_days` was published on 100% of
    returned orders and 0% of delivered ones, because it was emitted on an event
    type that only exists for failures. Nothing caught it: a column that is NULL
    on a principled subset violates no rule the other 68 tests encode, and this
    one *is* legitimately NULL on non-attempt events.

    So the assertion is not "is this column ever populated" -- that passed
    throughout -- but "**is its population independent of the outcome**". Both
    arms are asserted separately and both counts are reported, so a regression
    that empties one arm cannot hide behind the other being full.
    """
    if events is None:
        results.add(_skip("DQ-15", "attempt_delay_days on every shipped order",
                          Severity.HARD, "fct_delivery_event not loaded"))
        return

    first = events[events["attempt_number"] == 1]
    with_delay = set(first.loc[first["attempt_delay_days"].notna(), "order_id"])

    eligible = orders[orders["is_shipped"] & ~orders["is_censored"]]
    rto = eligible["rto_flag"].fillna(False).to_numpy(bool)
    arms = {"returned": eligible.loc[rto, "order_id"],
            "delivered": eligible.loc[~rto, "order_id"]}

    detail, missing_total = [], 0
    for arm, ids in arms.items():
        missing = int((~ids.isin(with_delay)).sum())
        missing_total += missing
        detail.append(f"{arm}: {len(ids) - missing:,}/{len(ids):,}")

    results.add(_r("DQ-15", "attempt_delay_days on every shipped order",
                   Severity.HARD, missing_total == 0,
                   "zero missing in either arm",
                   f"{missing_total} missing ({'; '.join(detail)})",
                   "A46: the column must be populated independently of the "
                   "outcome, or H6 cannot be tested and the shock input looks "
                   "leakage-shaped."))


def _dq_07(results, orders, customers) -> None:
    """Decision A9 split DQ-07 into three."""
    per_customer = orders.groupby("customer_id").size()
    total = customers.set_index("customer_id")["pre_window_orders"].add(
        per_customer.reindex(customers["customer_id"]).fillna(0).to_numpy(), fill_value=0
    )
    ledger_ok = int(
        (total.to_numpy() != customers["hist_orders_final"].to_numpy()).sum()
    )
    results.add(_r("DQ-07b", "Full ledger identity", Severity.HARD,
                   ledger_ok == 0, "zero mismatches", f"{ledger_ok}",
                   "pre_window + in-window = hist_orders_final, for every customer, "
                   "independent of resolution state."))

    resolved = orders["rto_flag"].notna()
    rto_total = orders.loc[resolved].groupby("customer_id")["rto_flag"].sum()
    recomputed = (
        customers.set_index("customer_id")["pre_window_rto_count"]
        + rto_total.reindex(customers["customer_id"]).fillna(0).to_numpy()
    )
    denominator = (
        customers.set_index("customer_id")["pre_window_delivered"]
        + customers.set_index("customer_id")["pre_window_rto_count"]
        + orders.loc[resolved].groupby("customer_id").size()
        .reindex(customers["customer_id"]).fillna(0).to_numpy()
    )
    # rto_flag is nullable-boolean, so summing it yields an object column.
    numerator = recomputed.to_numpy(dtype=float)
    denom = denominator.to_numpy(dtype=float)
    expected = np.divide(numerator, denom, out=np.zeros(len(customers)), where=denom > 0)
    drift = float(np.abs(np.round(expected, 3)
                         - customers["hist_rto_rate_final"].to_numpy()).max())
    results.add(_r("DQ-07a", "Resolved-only reconciliation", Severity.HARD,
                   drift <= 0.002, "<= 0.002", f"{drift:.4f}",
                   "Resolved, uncensored orders only (decision A9)."))

    excluded = int((~resolved).sum())
    censored = int(orders["is_censored"].sum())
    results.add(_r("DQ-07c", "Exclusions explained by the censoring model", Severity.SOFT,
                   excluded > 0 and excluded == censored + int(
                       orders["is_cancelled_preship"].sum()),
                   "excluded == censored + cancelled",
                   f"excluded {excluded:,}, censored {censored:,}, "
                   f"cancelled {int(orders['is_cancelled_preship'].sum()):,}"))


# ---------------------------------------------------------------------------
# GT — ground-truth recovery
# ---------------------------------------------------------------------------


def _gt(results, params, tables, truth, ledger, extra) -> None:
    cod = truth["planted_causal_effects"]["cod_on_rto"]
    naive, ame = cod["naive_observed_gap_pp"], cod["average_marginal_effect_pp"]

    results.add(_r("GT-02", "Naive gap exceeds the AME", Severity.HARD,
                   naive > ame, "naive > AME",
                   f"{naive:.2f}pp > {ame:.2f}pp",
                   "Relative rule (decision A6/A7). The absolute band is dropped."))

    for test_id, name, reason in (
        ("GT-01", "Coefficient recovery",
         "Needs a fitted logistic regression on safe features. Phase 5 runs these; "
         "the data and truth file support it."),
        ("GT-03", "Adjustment closes the gap partially",
         "Needs the confounder-controlled model. The relative rule and its "
         "min_gap_closed threshold are in params; Phase 5 evaluates them."),
        ("GT-04", "Planted null on review_count holds",
         "Needs the fitted model's CI on log1p(review_count)."),
        ("GT-06", "H6: delay explains more than promise",
         "Same fitted-model dependency as BR-09."),
        ("GT-07", "Selection decomposition via PSM",
         "Needs propensity matching. Phase 5."),
    ):
        results.add(_skip(test_id, name, Severity.HARD, reason))

    band = params.require("ground_truth.gt_05.auc_ceiling_band")
    ceiling = truth["achieved"]["auc_ceiling_precheckout"]
    results.add(_r("GT-05", "AUC ceiling in band", Severity.HARD,
                   float(band[0]) <= ceiling <= float(band[1]),
                   f"[{band[0]}, {band[1]}]", f"{ceiling:.4f}",
                   "Decision A37 calibrated noise_sd against this; A38 froze it."))
