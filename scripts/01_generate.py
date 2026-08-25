"""Run the generation pipeline: modules 02–17.

Dimensions, latents, pre-window history, sessions, and then the decision-A1 day
loop — point-in-time state, COD intent, the two conversion hurdles, payment
attempts, orders, cancellations, the two-stage RTO model, delivery and the
outcome draw — with the three in-window intercepts solved jointly around it.

Modules 18–23 (RTO reasons, economics, roll-up, PostgreSQL, validation) are not
written yet.

Usage
-----
    python scripts/01_generate.py                 # full scale
    python scripts/01_generate.py --dev           # 5,000-order dev scale
    python scripts/01_generate.py --seed 7        # override master seed
    python scripts/01_generate.py --no-write      # checkpoint only, write nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.config.loader import load_params  # noqa: E402
from src.config.seeds import spawn_substreams  # noqa: E402
from src.generators import materialise  # noqa: E402
from src.generators.components import build_component_traces  # noqa: E402
from src.generators.conversion import project_checkout_events  # noqa: E402
from src.generators.events import (  # noqa: E402
    build_delivery_events, build_truth_probabilities,
)
from src.generators.customers import generate_customers  # noqa: E402
from src.generators.dates import generate_dates  # noqa: E402
from src.generators.geography import generate_geography  # noqa: E402
from src.generators.history import generate_history  # noqa: E402
from src.generators.products import generate_products  # noqa: E402
from src.generators.sellers import generate_sellers  # noqa: E402
from src.generators.sessions import generate_sessions  # noqa: E402
from src.generators.rollup import rollup_customers, write_truth  # noqa: E402
from src.generators.rto_reasons import build_reason_drivers, generate_rto_reasons  # noqa: E402
from src.generators.simulate import prepare, simulate_window, solve_all  # noqa: E402
from src.economics.order_economics import (  # noqa: E402
    derive_breakeven, generate_economics, reconcile_exemplar, solve_ndr_base,
)
from src.models.logit import CoefficientLedger, logistic  # noqa: E402
from src.validation.tests_cal import cal_09_no_slope_changed, cal_11_selection_share  # noqa: E402
from src.validation.tests_lk import lk_06_shrinkage_prior_is_declared  # noqa: E402

PARAMS = REPO_ROOT / "config" / "params.yaml"
SCHEMA = REPO_ROOT / "config" / "params.schema.json"
OUT_DIR = REPO_ROOT / "data" / "raw"

EXPECTED_SIGNS = {
    ("latent_trust", "pre_window_cod_orders"): "-",
    ("latent_liquidity", "pre_window_cod_orders"): "-",
    ("latent_intent", "pre_window_cod_orders"): "+",
    ("latent_price_sensitivity", "pre_window_cod_orders"): "+",
    ("latent_intent", "pre_window_rto_count"): "+",
    ("latent_liquidity", "pre_window_rto_count"): "-",
    ("latent_trust", "pre_window_rto_count"): "-",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    scenario = REPO_ROOT / "config" / "scenarios" / "dev_small.yaml" if args.dev else None
    params = load_params(PARAMS, SCHEMA, scenario_path=scenario)
    seed = args.seed if args.seed is not None else int(params.require("seed.master"))
    rng = spawn_substreams(seed, params.require("seed.substreams"))
    ledger = CoefficientLedger()

    print(f"params : sha256={params.sha256[:16]}  dgp={params.dgp_sha256[:16]}")
    print(f"seed   : {seed}   sessions={params.require('scale.n_sessions'):,}\n")

    print("02-05  dimensions ...", flush=True)
    dates = generate_dates(params, rng.get("date"))
    geography = generate_geography(params, rng.get("geography"))
    sellers = generate_sellers(params, rng.get("seller"))
    products = generate_products(params, sellers, rng.get("product"))

    print("06-07  customers, latents, pre-window history ...", flush=True)
    customers, latents = generate_customers(
        params, geography, rng.get("customer"), rng.get("latent")
    )
    history = generate_history(
        params, customers, latents, geography, rng.get("history"), ledger
    )
    customers = customers.merge(history.history, on="customer_id", how="left")

    print("08     sessions ...", flush=True)
    sessions = generate_sessions(
        params, dates, customers, products, geography, latents, rng.get("session")
    )

    print("09-17  day loop + joint solve (this takes a minute) ...", flush=True)
    rngs = {k: rng.get(k) for k in ("cod", "payment", "conversion", "rto", "delivery")}
    setup = prepare(
        params, sessions, customers, latents, products, sellers, geography,
        dates, rngs, ledger,
    )
    solved = solve_all(setup, params, history)
    metrics = simulate_window(
        setup, solved["alpha_0"], solved["beta_0"], solved["gamma_0"],
        solved["price_scalar"], solved["noise_sd"], collect=True,
    )
    # The scalar is a LEVEL: it multiplies every list price, leaving the category
    # ratios untouched. Applied to the stored tables so dim_product and
    # fct_checkout_session agree with what the loop actually simulated.
    scalar = solved["price_scalar"]
    products["list_price"] = (products["list_price"] * scalar).round(2)
    for column in ("cart_value", "prospective_gmv", "order_value"):
        sessions[column] = (sessions[column] * scalar).round(2)
    extra = metrics.extra

    print("       materialising tables ...", flush=True)
    sessions_out = materialise.resolve_sessions(params, sessions, extra)
    sessions_out["signup_date"] = customers.set_index("customer_id")["signup_date"]\
        .reindex(sessions_out["customer_id"]).to_numpy()
    state = materialise.build_state(params, sessions_out, extra, dates)
    orders = materialise.build_orders(params, sessions_out, extra, dates, products)
    attempts = materialise.build_payment_attempts(
        params, sessions_out, extra, rng.get("payment")
    )

    print("18-21  reasons, economics, roll-up, truth file ...", flush=True)
    order_positions = np.flatnonzero(extra["converted"])
    drivers = build_reason_drivers(
        orders, state, latents, geography, sessions_out, products,
        extra["attempt_delay"][order_positions],
        setup["ctx"]["is_month_end"][order_positions],
    )
    reasons = generate_rto_reasons(params, orders, drivers, rng.get("reason"))
    orders = orders.merge(reasons, on="order_id", how="left")

    # Decision A38: solve the NDR base so the realised RTO mean is the Phase 1
    # registry value, then write it back before the cost lines are computed.
    params.raw["economics"]["support_ndr_base_solved"] = solve_ndr_base(params, orders)
    economics = generate_economics(
        params, orders, products, geography, rng.get("economics")
    )
    customers = rollup_customers(customers, orders, economics)
    hypotheses = _hypotheses(orders, state, metrics)

    # Decision A45. The audit sample can only be drawn AFTER the loop, because
    # three of its four strata are defined by outcomes. The trace is therefore
    # reconstructed -- and build_component_traces re-derives every sampled
    # probability from its own trace and raises if it disagrees with what the day
    # loop stored. A decomposition nobody checked is worse than an empty column.
    print("       component traces (A45 audit sample) ...", flush=True)
    traces, trace_summary = build_component_traces(
        params, setup, solved, extra, sessions_out, rng.get("truth_sampling"),
    )
    worst = max(trace_summary["max_reconstruction_error"].values())
    print(f"       {trace_summary['sessions_sampled']:,} sessions traced "
          f"({trace_summary['rto_traces']:,} with an RTO stage); "
          f"max reconstruction error {worst:.2e}")

    truth = write_truth(
        REPO_ROOT / "data" / "truth" / "_truth.json", params, solved, metrics,
        orders, economics, _auc(params, extra), ledger, hypotheses, trace_summary,
    )

    # ndr_code belongs on fct_delivery_event (spec 3.11), not on fct_order. It is
    # carried on the order frame only so the delivery-event projection can read
    # it, and is dropped before the table is written.
    orders_out = orders.drop(columns=["ndr_code"], errors="ignore")

    # Module-20 roll-up. customers.py leaves true_cod_propensity as NaN because
    # sessions do not exist yet at module 06, and the fill was never written --
    # the live load found it, exactly as it found pit_avg_order_value: a
    # placeholder that the parquet layer was happy to keep forever.
    # Spec 3.13: customer-level mean P(COD) across their sessions.
    truth_probabilities = build_truth_probabilities(sessions_out, extra, traces)
    propensity = (
        truth_probabilities[["session_id", "p_cod_intent"]]
        .merge(sessions_out[["session_id", "customer_id"]], on="session_id")
        .groupby("customer_id")["p_cod_intent"].mean().round(4)
    )
    latents = latents.copy()
    latents["true_cod_propensity"] = (
        latents["customer_id"].map(propensity).to_numpy()
    )
    # Decision A18 again: a customer with no in-window session has no denominator,
    # so the mean is NULL rather than imputed. Roughly 6% of the base.
    unsessioned = int(latents["true_cod_propensity"].isna().sum())
    print(f"       true_cod_propensity: {len(latents) - unsessioned:,} populated, "
          f"{unsessioned:,} NULL (no in-window session)")

    tables = {
        "dim_date": dates, "dim_geography": geography, "dim_seller": sellers,
        "dim_product": products, "dim_customer": customers,
        "truth_customer_latent": latents,
        "fct_checkout_session": sessions_out.drop(columns=["signup_date"]),
        "fct_customer_state_at_session": state,
        "fct_order": orders_out,
        "fct_payment_attempt": attempts,
        "fct_order_economics": economics,
        "fct_checkout_event": project_checkout_events(sessions_out),
        "fct_delivery_event": build_delivery_events(params, orders, extra, geography),
        "truth_order_probability": truth_probabilities,
    }
    # Not a table: carried through so the A36 ratio check can compare the
    # realised category mix against what params.yaml declares.
    declared_means = params.require("distributions.category_mean_gmv")

    if not args.no_write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        # Clear first. A parquet left behind by an earlier pipeline shape would
        # still satisfy every foreign key -- session ids are stable -- while
        # carrying values from a different parameterisation. Silent, and exactly
        # the kind of inconsistency the loader would happily import.
        stale = [f for f in OUT_DIR.glob("*.parquet") if f.stem not in tables]
        for f in OUT_DIR.glob("*.parquet"):
            f.unlink()
        if stale:
            print(f"  removed {len(stale)} stale table(s): "
                  + ", ".join(sorted(f.stem for f in stale)))
        for name, frame in tables.items():
            frame.to_parquet(OUT_DIR / f"{name}.parquet", index=False)
        print(f"\nwrote {len(tables)} table(s) to {OUT_DIR}")

    report(params, setup, {**tables, "_declared_category_means": declared_means},
           history, solved, metrics, ledger)
    report_economics(params, economics, orders, truth)
    return 0


def _hypotheses(orders, state, metrics) -> dict:
    """Every pre-registered prior, next to what the data actually produced.

    Recorded as PRIOR **vs** OBSERVED rather than collapsed to the observed
    value. Two of these miss — H2 high, H11 low — and blueprint §4 says a
    documented wrong prior is the strongest signal of genuine analytical work.
    Phase 5 cannot demonstrate the miss if the prior has been overwritten.
    """
    joined = orders.merge(state, on="session_id", suffixes=("", "_s"))
    is_cod = joined["payment_method"] == "COD"
    new = joined["pit_is_new_customer"].to_numpy(bool)
    h2 = float(is_cod[new].mean() - is_cod[~new].mean()) * 100

    cod_orders = int(is_cod.sum())
    h11 = float(orders["paid_via_switch"].sum() / max(cod_orders, 1))

    shipped = joined[joined["is_shipped"] & ~joined["is_censored"]]
    prior_rto = shipped["pit_rto_count"].to_numpy() > 0
    outcome = shipped["rto_flag"].fillna(False).to_numpy(bool)
    h3 = float(outcome[prior_rto].mean() / max(outcome[~prior_rto].mean(), 1e-9))

    return {
        "H2_new_customer_cod_lift_pp": {
            "prior": "12-18pp", "observed": round(h2, 2),
            "verdict": "ABOVE PRIOR",
            "mechanism": "spec 7.2 priced is_new_customer (+0.70) in isolation and "
                         "ignored that a new customer also escapes log1p_orders_delivered "
                         "(-0.18) and log1p_prepaid_success (-0.35). See decision A43.",
        },
        "H3_prior_rto_lift_multiple": {
            "prior": "2.0-2.5x", "observed": round(h3, 3),
            "verdict": "BELOW PRIOR",
            "mechanism": "decision A37 raised post-dispatch noise from 0.85 to 3.3125 to "
                         "bring the AUC ceiling into GT-05's band, which dilutes every "
                         "pre-checkout signal including pit_rto_rate_shrunk (+2.80). "
                         "BR-02 was restated as a CI lower bound under A40.",
        },
        "H11_pct_cod_from_payment_failure": {
            "prior": "8-15%", "observed": round(h11, 4),
            "verdict": "BELOW PRIOR",
            "mechanism": "parameters come from plausible external PG-failure ranges, not "
                         "from the prior. Spec 10.3 predicted this. See decision A35.",
        },
        "H12_achievable_auc_ceiling": {
            "prior": "0.74-0.79", "observed": round(metrics.auc_precheckout, 4),
            "verdict": "IN PRIOR",
            "mechanism": "noise_sd was CALIBRATED against this target (A37) and then "
                         "frozen (A38), so it is a solved value rather than an "
                         "independent confirmation.",
        },
    }


def _auc(params, extra) -> float:
    from sklearn.metrics import roc_auc_score
    mask = extra["shipped"] & ~extra["censored"]
    return float(roc_auc_score(extra["rto_flag"][mask], extra["p_rto_precheckout"][mask]))


def report_economics(params, economics, orders, truth) -> None:
    """Module 19 reconciliation, with shrink and NDR broken out (decision A38)."""
    rule = "=" * 78
    print(f"\n{rule}\nMODULE 19 — ECONOMICS RECONCILIATION\n{rule}")

    cfg = params.require("economics")
    weights = params.require("distributions.category_weights")
    shrink_blend = sum(
        float(cfg["shrink_rate_by_category"][k]) * float(weights[k]) for k in weights
    )
    rto = orders["rto_flag"].fillna(False).to_numpy(bool)
    ndr_mean = float(economics.loc[rto.nonzero()[0], "support_ndr_cost"].mean())         if rto.any() else 0.0

    print("\n1. THE TWO A38 LINES, BROKEN OUT")
    print(f"   shrink, category-weighted   {shrink_blend:.4%} of COGS   "
          f"<- FORMULA WINS; spec 12.2's '8.0%' was an arithmetic error")
    target = cfg["support_ndr_target_mean"]
    ok = abs(ndr_mean - float(target["target"])) <= float(target["tol"])
    print(f"   NDR, realised mean on RTO   {ndr_mean:8.2f}   "
          f"target {float(target['target']):.2f} +/-{float(target['tol']):.2f}  "
          f"{'PASS' if ok else 'FAIL'}")
    print(f"   NDR base solved             {float(params.require('economics.support_ndr_base_solved')):8.4f}"
          f"   <- PARAMETER WINS; Phase 1 6.4 registry")

    print("\n2. EC-03..EC-06 AT A 1,000 GMV ORDER")
    exemplar = reconcile_exemplar(params)
    rec = params.require("economics.reconciliation")
    for key, test in (("prepaid_delivered_cm", "EC-03"), ("cod_delivered_cm", "EC-04"),
                      ("cod_rto_cash_loss", "EC-05"), ("cod_rto_economic_cost", "EC-06")):
        got, t = exemplar[key], rec[key]
        ok = abs(got - float(t["target"])) <= float(t["tol"])
        print(f"   [{'PASS' if ok else 'FAIL'}] {test} {key:<24} {got:8.2f}  "
              f"target {float(t['target']):7.1f} +/-{float(t['tol']):.0f}")

    print("\n3. p* — DERIVED from realised economics (decision A38)")
    t = rec["breakeven_rto_prob"]
    nominal = exemplar["breakeven_rto_prob"]
    derived = truth["economics_targets"]["breakeven_rto_probability_derived"]
    for label, value in (("exemplar", nominal), ("DERIVED (realised)", derived)):
        ok = abs(value - float(t["target"])) <= float(t["tol"])
        print(f"   [{'PASS' if ok else 'FAIL'}] p* {label:<20} {value:.4f}  "
              f"target {float(t['target']):.3f} +/-{float(t['tol']):.3f}")
    print("   Phase 3+ tiering uses the DERIVED value — the threshold must be")
    print("   economically true, not nominal.")

    print("\n4. EMPIRICAL MEANS ACROSS THE ACTUAL ORDER DISTRIBUTION")
    print("   (spec 12.4: these WILL differ from the exemplar. Report, do not tune.)")
    e = truth["economics_targets"]
    print(f"   mean COD RTO cash loss      {e['cod_rto_cash_loss']:8.2f}")
    print(f"   mean COD RTO economic cost  {e['cod_rto_economic_cost']:8.2f}")
    print(f"   mean COD delivered CM       {e['cod_delivered_cm']:8.2f}")
    # Annualise on the RESOLVED denominator: a censored order is a real future
    # outcome, not a zero-cost one, and counting it as zero understates the
    # headline by ~15% (decision A41).
    resolved = (~orders["is_cancelled_preship"].to_numpy(bool)
                & ~orders["is_censored"].to_numpy(bool))
    rto_mask = orders["rto_flag"].fillna(False).to_numpy(bool)
    exposure = (
        economics.loc[rto_mask, "rto_economic_cost"].sum() / max(int(resolved.sum()), 1)
        * float(params.require("scale.population_annual_orders")) / 1e7
    )
    print(f"   annualised RTO exposure     {exposure:8.1f} Cr   "
          f"EC-07 band 150-180 (SOFT)  {'PASS' if 150 <= exposure <= 180 else 'FAIL'}")
    print(rule)


def report(params, setup, tables, history, solved, m, ledger) -> None:
    rule = "=" * 78
    e = m.extra
    print(f"\n{rule}\nCHECKPOINT — modules 02-17\n{rule}")

    print("\n1. VOLUME")
    print(f"   sessions                      {setup['n']:>9,}")
    print(f"   orders (VOL-01 >= 100,000)    {m.n_orders:>9,}  "
          f"{'PASS' if m.n_orders >= int(params.require('volume_targets.vol_01_min_orders')) else 'FAIL'}")
    print(f"   shipped                       {m.n_shipped:>9,}")
    print(f"   cancelled pre-ship            {m.n_cancelled:>9,}")
    print(f"   censored                      {m.n_censored:>9,}")
    consistency = abs(m.n_orders / setup["n"] - m.conversion_rate)
    print(f"   VOL-02b |orders/sessions - conversion| = {consistency:.2e}  "
          f"{'PASS' if consistency < float(params.require('volume_targets.vol_02b_conversion_consistency_tol')) else 'FAIL'}")

    factor = float(params.require("scale.population_annual_orders")) / m.n_orders
    bounds = params.require("scale.annualization_factor_bounds")
    print(f"\n2. ANNUALISATION FACTOR (DERIVED, decision A32)")
    print(f"   24,000,000 / {m.n_orders:,} = {factor:.2f}   "
          f"EC-08 band [{bounds['lo']}, {bounds['hi']}]  "
          f"{'PASS' if bounds['lo'] <= factor <= bounds['hi'] else 'FAIL'}")
    print(f"   (annualization_factor_expected = "
          f"{params.require('scale.annualization_factor_expected')}, reporting only)")

    print("\n3. SEVEN-WAY JOINT SOLVE — every level; zero slopes")
    for name, c in solved["calibrations"].items():
        print(f"   [{'PASS' if c.converged else 'FAIL'}] {c.describe()}")
    print(f"   drift on the final pass: "
          + ", ".join(f"{k} {v:.2e}" for k, v in solved["drift"].items()))
    print(f"   pre-window (module 07, provably independent of the loop):")
    for c in (history.cod_calibration, history.rto_calibration):
        print(f"   [{'PASS' if c.converged else 'FAIL'}] {c.describe()}")

    print("\n4. CALIBRATION")
    t = params.require("calibration_targets")
    rows = [
        ("CAL-01 COD share", m.cod_share, t["cod_share"], ""),
        ("CAL-05 RTO blended", m.rto_rate_blended, t["rto_rate_blended"], ""),
        ("CAL-06 conversion", m.conversion_rate, t["checkout_conversion"], ""),
        ("CAL-03 RTO COD", m.rto_rate_cod, t["rto_rate_cod"], "  <- EMERGENT, not calibrated"),
        ("CAL-04 RTO prepaid", m.rto_rate_prepaid, t["rto_rate_prepaid"],
         "  <- EMERGENT, not calibrated"),
    ]
    for name, actual, target, note in rows:
        lo = float(target["target"]) - float(target["tol"])
        hi = float(target["target"]) + float(target["tol"])
        ok = lo <= actual <= hi
        print(f"   [{'PASS' if ok else 'FAIL'}] {name:<22} {actual:.4f}  "
              f"target {float(target['target']):.3f} +/-{float(target['tol']):.3f} "
              f"({target['severity']}){note}")

    print("\n5. CAL-11 — THE GATE (decision A7)")
    naive, ame = causal_effects(params, e, m)
    cal11 = cal_11_selection_share(naive, ame, params)
    print(f"   naive COD-prepaid gap   {naive:6.2f}pp")
    print(f"   AME (canonical, A6)     {ame:6.2f}pp")
    print(f"   [{cal11.status.value}] selection share {cal11.actual}  band {cal11.expected}")
    print(f"   GT-02 (naive > AME AND CAL-11): "
          f"{'PASS' if naive > ame and cal11.status.value == 'PASS' else 'FAIL'}")

    print("\n6. AUC CEILING (GT-05 expects 0.74-0.79; LK-03 guards at 0.85)")
    try:
        from sklearn.metrics import roc_auc_score
        d = e["shipped"] & ~e["censored"]
        pre = roc_auc_score(e["rto_flag"][d], e["p_rto_precheckout"][d])
        fin = roc_auc_score(e["rto_flag"][d], e["p_rto_final"][d])
        band = params.require("ground_truth.gt_05.auc_ceiling_band")
        guard = float(params.require("ground_truth.lk_03_auc_ceiling"))
        print(f"   AUC of truth.p_rto_precheckout  {pre:.4f}  band [{band[0]}, {band[1]}]  "
              f"{'PASS' if band[0] <= pre <= band[1] else 'FAIL'}")
        print(f"   AUC of truth.p_rto_final        {fin:.4f}  (includes the shock)")
        print(f"   LK-03 tripwire margin           {guard - pre:+.4f}  "
              f"(guard {guard} - ceiling)  "
              f"{'PASS' if guard - pre >= 0.05 else 'FAIL — detector still blunt'}")
    except ImportError:
        print("   scikit-learn not available")

    print("\n7. DQ-14 CENSORING")
    late = int(params.require("censoring.late_window_definition_days"))
    window = int(params.require("meta.window_days"))
    day = setup["index"]["day_index"]
    late_mask = e["shipped"] & (day >= window - late)
    share = float(e["censored"][late_mask].mean()) if late_mask.any() else 0.0
    floor = float(params.require("censoring.min_censored_share_of_late_window"))
    print(f"   censored orders                 {m.n_censored:,}  "
          f"({m.n_censored / m.n_orders:.2%} of all orders)")
    print(f"   share of late-window orders     {share:.4f}  floor {floor}  "
          f"{'PASS' if share >= floor else 'FAIL'}")

    print("\n8. ORDER VALUE — decision A34: ₹1,000 is GMV (Phase 1 §6.5)")
    for label, value, key in (
        ("EC-01  mean GMV per order", m.mean_gmv, "mean_gmv_per_order"),
        ("EC-01b mean order_value  ", m.mean_order_value, "mean_order_value_per_order"),
    ):
        target = t[key]
        ok = abs(value - float(target["target"])) <= float(target["tol"])
        print(f"   [{'PASS' if ok else 'FAIL'}] {label}  {value:8.2f}  "
              f"target {float(target['target']):.0f} +/-{float(target['tol']):.0f} "
              f"({target['severity']})")
    scalar = solved["price_scalar"]
    conv = e["converted"]
    session_mean = float(setup["gmv"].mean() * scalar)
    print(f"   price_scalar solved         {scalar:.6f}   <- A36, a LEVEL")
    print(f"   sessions mean GMV           {session_mean:8.2f}")
    print(f"   conversion selects on value {100*(m.mean_gmv/session_mean-1):+.2f}%"
          f"  (why the scalar is needed)")

    print("\n9. GUARDS")
    print(f"   [{cal_09_no_slope_changed(ledger, params, require_complete_coverage=False).status.value}]"
          f" CAL-09 slope immutability")
    print(f"   [{lk_06_shrinkage_prior_is_declared(float(params.require('priors.rto_prior')), float(params.require('priors.shrinkage_k')), params, float(params.require('priors.cod_prior')), float(params.require('priors.payment_failure_prior'))).status.value}]"
          f" LK-06 declared shrinkage prior")
    geo_corr = address_tier_correlation(tables)
    print(f"   [INFO] corr(address_completeness, geo_tier rank) = {geo_corr:+.4f}"
          f"   <- A28(a): stated independence, measured")
    print(f"   [INFO] intra-day repeat sessions {100*setup['index']['multi_session_share']:.2f}%"
          f"  (placements are exact, not day-batched)")
    ratios = category_ratios(tables)
    print(f"   [INFO] implied per-category scalar, max spread "
          f"{ratios:.2e}   <- A36: the SCALAR moved, the RATIOS did not")
    print(rule)


def causal_effects(params, e, m) -> tuple[float, float]:
    """The naive gap and the canonical average marginal effect (decision A6)."""
    beta = float(params.require("rto_model.coefficients.is_cod"))
    d = e["shipped"] & ~e["censored"] & e["is_cod_order"]
    p = np.clip(e["p_rto_final"][d], 1e-12, 1 - 1e-12)
    lp = np.log(p / (1 - p))
    ame = float(np.mean(logistic(lp) - logistic(lp - beta))) * 100
    return (m.rto_rate_cod - m.rto_rate_prepaid) * 100, ame


def category_ratios(tables) -> float:
    """Decision A36: confirm the scalar moved the LEVEL and not the category mix.

    The scalar is applied multiplicatively, so every category's mean price must
    have moved by the same factor. Any drift here would mean a ratio was edited.
    """
    products = tables["dim_product"]
    realised = products.groupby("category")["list_price"].mean()
    declared = pd.Series(
        {k: float(v) for k, v in tables["_declared_category_means"].items()}
    ).reindex(realised.index)

    # The scalar multiplies every category by the SAME factor, so the implied
    # per-category scalar must be constant. Its spread is what would move if a
    # ratio had been edited. Sampling noise contributes too — Electronics has
    # sigma 0.75, so its sample mean is the noisiest — which is why this is
    # reported rather than asserted.
    implied = realised / declared
    return float((implied / implied.mean() - 1.0).abs().max())





def address_tier_correlation(tables) -> float:
    """Decision A28(a): report it, so the independence is stated and checked."""
    sessions = tables["fct_checkout_session"]
    geo = tables["dim_geography"].set_index("geography_id")["geo_tier"]
    rank = {"METRO": 0, "TIER1": 1, "TIER2": 2, "TIER3": 3}
    tier = sessions["delivery_geography_id"].map(geo).map(rank).to_numpy(float)
    return float(np.corrcoef(sessions["address_completeness_score"].to_numpy(float), tier)[0, 1])


if __name__ == "__main__":
    sys.exit(main())
