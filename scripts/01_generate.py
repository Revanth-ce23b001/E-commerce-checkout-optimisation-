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

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.config.loader import load_params  # noqa: E402
from src.config.seeds import spawn_substreams  # noqa: E402
from src.generators import materialise  # noqa: E402
from src.generators.customers import generate_customers  # noqa: E402
from src.generators.dates import generate_dates  # noqa: E402
from src.generators.geography import generate_geography  # noqa: E402
from src.generators.history import generate_history  # noqa: E402
from src.generators.products import generate_products  # noqa: E402
from src.generators.sellers import generate_sellers  # noqa: E402
from src.generators.sessions import generate_sessions  # noqa: E402
from src.generators.simulate import prepare, simulate_window, solve_intercepts  # noqa: E402
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
    solved = solve_intercepts(setup, params)
    metrics = simulate_window(
        setup, solved["alpha_0"], solved["beta_0"], solved["gamma_0"], collect=True
    )
    extra = metrics.extra

    print("       materialising tables ...", flush=True)
    sessions_out = materialise.resolve_sessions(params, sessions, extra)
    sessions_out["signup_date"] = customers.set_index("customer_id")["signup_date"]\
        .reindex(sessions_out["customer_id"]).to_numpy()
    state = materialise.build_state(params, sessions_out, extra)
    orders = materialise.build_orders(params, sessions_out, extra, dates)
    attempts = materialise.build_payment_attempts(
        params, sessions_out, extra, rng.get("payment")
    )

    tables = {
        "dim_date": dates, "dim_geography": geography, "dim_seller": sellers,
        "dim_product": products, "dim_customer": customers,
        "truth_customer_latent": latents,
        "fct_checkout_session": sessions_out.drop(columns=["signup_date"]),
        "fct_customer_state_at_session": state,
        "fct_order": orders,
        "fct_payment_attempt": attempts,
    }

    if not args.no_write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for name, frame in tables.items():
            frame.to_parquet(OUT_DIR / f"{name}.parquet", index=False)
        print(f"\nwrote {len(tables)} table(s) to {OUT_DIR}")

    report(params, setup, tables, history, solved, metrics, ledger)
    return 0


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

    print("\n3. JOINT SOLVE — 3 in-window intercepts")
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
        print(f"   AUC of truth.p_rto_precheckout  {pre:.4f}  "
              f"{'PASS' if band[0] <= pre <= band[1] else 'FAIL — see decision A37'}")
        print(f"   AUC of truth.p_rto_final        {fin:.4f}  (includes the shock)")
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

    print("\n8. ORDER VALUE (decision A34 — both reported)")
    conv = e["converted"]
    print(f"   sessions: mean gmv {setup['gmv'].mean():8.2f}   "
          f"mean order_value {setup['order_value'].mean():8.2f}")
    print(f"   ORDERS  : mean gmv {setup['gmv'][conv].mean():8.2f}   "
          f"mean order_value {setup['order_value'][conv].mean():8.2f}")
    print(f"   conversion selects on value: "
          f"{100*(setup['gmv'][conv].mean()/setup['gmv'].mean()-1):+.2f}% on gmv  <- A36")

    print("\n9. GUARDS")
    print(f"   [{cal_09_no_slope_changed(ledger, params, require_complete_coverage=False).status.value}]"
          f" CAL-09 slope immutability")
    print(f"   [{lk_06_shrinkage_prior_is_declared(float(params.require('priors.rto_prior')), float(params.require('priors.shrinkage_k')), params).status.value}]"
          f" LK-06 declared shrinkage prior")
    geo_corr = address_tier_correlation(tables)
    print(f"   [INFO] corr(address_completeness, geo_tier rank) = {geo_corr:+.4f}"
          f"   <- A28(a): stated independence, measured")
    print(f"   [INFO] intra-day repeat sessions {100*setup['index']['multi_session_share']:.2f}%"
          f"  (placements are exact, not day-batched)")
    print(rule)


def causal_effects(params, e, m) -> tuple[float, float]:
    """The naive gap and the canonical average marginal effect (decision A6)."""
    beta = float(params.require("rto_model.coefficients.is_cod"))
    d = e["shipped"] & ~e["censored"] & e["is_cod_order"]
    p = np.clip(e["p_rto_final"][d], 1e-12, 1 - 1e-12)
    lp = np.log(p / (1 - p))
    ame = float(np.mean(logistic(lp) - logistic(lp - beta))) * 100
    return (m.rto_rate_cod - m.rto_rate_prepaid) * 100, ame


def address_tier_correlation(tables) -> float:
    """Decision A28(a): report it, so the independence is stated and checked."""
    sessions = tables["fct_checkout_session"]
    geo = tables["dim_geography"].set_index("geography_id")["geo_tier"]
    rank = {"METRO": 0, "TIER1": 1, "TIER2": 2, "TIER3": 3}
    tier = sessions["delivery_geography_id"].map(geo).map(rank).to_numpy(float)
    return float(np.corrcoef(sessions["address_completeness_score"].to_numpy(float), tier)[0, 1])


if __name__ == "__main__":
    sys.exit(main())
