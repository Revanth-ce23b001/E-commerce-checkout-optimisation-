"""Run the generation pipeline.

Currently implements **modules 02–12** — dates, geography, sellers, products,
customers + latents, pre-window history, sessions, point-in-time state, COD
intent, the two conversion hurdles, payment attempts and conversion. Modules
13–23 (orders onward) are not written yet.

Ends with two checkpoints: the brief's Stage-2 gate (latents must correlate with
history in the specified directions) and the modules 08–12 funnel gate.

Usage
-----
    python scripts/01_generate.py                 # full scale, from params.yaml
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
from src.generators.customers import generate_customers  # noqa: E402
from src.generators.dates import generate_dates  # noqa: E402
from src.generators.geography import generate_geography  # noqa: E402
from src.generators.history import generate_history  # noqa: E402
from src.generators.checkout_pipeline import run_checkout  # noqa: E402
from src.generators.products import generate_products  # noqa: E402
from src.generators.sellers import generate_sellers  # noqa: E402
from src.generators.sessions import generate_sessions  # noqa: E402
from src.generators.state_snapshots import empty_ledger, generate_state_snapshots  # noqa: E402
from src.models.logit import CoefficientLedger  # noqa: E402
from src.validation.tests_cal import cal_09_no_slope_changed  # noqa: E402
from src.validation.tests_lk import lk_06_shrinkage_prior_is_declared  # noqa: E402

PARAMS = REPO_ROOT / "config" / "params.yaml"
SCHEMA = REPO_ROOT / "config" / "params.schema.json"
OUT_DIR = REPO_ROOT / "data" / "raw"

# The expected sign of each latent's correlation with each pre-window column.
# Derived from the coefficients in §7.2 / §8.2, NOT chosen independently — this
# table is the checkpoint's whole point, so it must restate the planted structure
# rather than a hope about it.
EXPECTED_SIGNS = {
    ("latent_trust", "pre_window_cod_orders"): "-",       # cod latent_trust  = -0.55
    ("latent_liquidity", "pre_window_cod_orders"): "-",   # cod latent_liquidity = -0.45
    ("latent_intent", "pre_window_cod_orders"): "+",      # cod latent_intent = +0.40
    ("latent_price_sensitivity", "pre_window_cod_orders"): "+",   # +0.12, weak
    ("latent_intent", "pre_window_rto_count"): "+",       # rto latent_intent = +0.70
    ("latent_liquidity", "pre_window_rto_count"): "-",    # rto latent_liquidity = -0.55
    ("latent_trust", "pre_window_rto_count"): "-",        # rto latent_trust = -0.30
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="5,000-order dev scale")
    parser.add_argument("--seed", type=int, default=None, help="override seed.master")
    parser.add_argument("--no-write", action="store_true", help="checkpoint only")
    args = parser.parse_args(argv)

    scenario = REPO_ROOT / "config" / "scenarios" / "dev_small.yaml" if args.dev else None
    params = load_params(PARAMS, SCHEMA, scenario_path=scenario)

    master_seed = args.seed if args.seed is not None else int(params.require("seed.master"))
    rng = spawn_substreams(master_seed, params.require("seed.substreams"))
    ledger = CoefficientLedger()

    print(f"params  : {params.source_path}")
    print(f"          sha256={params.sha256[:16]}  dgp={params.dgp_sha256[:16]}")
    print(f"seed    : {master_seed}   substreams={len(rng.names)}")
    print(f"scale   : {params.require('scale.n_customers'):,} customers  "
          f"{params.require('scale.n_products'):,} products  "
          f"{params.require('scale.n_sellers'):,} sellers  "
          f"{params.require('scale.n_geographies'):,} geographies")
    print()

    print("02  dim_date ...", flush=True)
    dates = generate_dates(params, rng.get("date"))

    print("03  dim_geography ...", flush=True)
    geography = generate_geography(params, rng.get("geography"))

    print("04  dim_seller ...", flush=True)
    sellers = generate_sellers(params, rng.get("seller"))

    print("05  dim_product ...", flush=True)
    products = generate_products(params, sellers, rng.get("product"))

    print("06  dim_customer + truth_customer_latent ...", flush=True)
    customers, latents = generate_customers(
        params, geography, rng.get("customer"), rng.get("latent")
    )

    print("07  pre-window history (2 calibrators) ...", flush=True)
    result = generate_history(
        params, customers, latents, geography, rng.get("history"), ledger
    )
    customers = customers.merge(result.history, on="customer_id", how="left")

    print("08  fct_checkout_session ...", flush=True)
    sessions = generate_sessions(
        params, dates, customers, products, geography, latents, rng.get("session")
    )

    print("09  fct_customer_state_at_session (chronological pass) ...", flush=True)
    # Modules 13-17 do not exist, so nothing resolves inside the window and every
    # pit_* value reflects pre-window history alone. The ledger interface is the
    # real one; when the A1 day loop closes, this call does not change.
    state = generate_state_snapshots(params, sessions, customers, empty_ledger())

    print("10-12  COD intent, hurdles, payment attempts, conversion ...", flush=True)
    checkout = run_checkout(
        params, sessions, state, customers, latents, products, sellers, geography,
        dates, rng.get("cod"), rng.get("payment"), rng.get("conversion"), ledger,
    )

    tables = {
        "dim_date": dates,
        "dim_geography": geography,
        "dim_seller": sellers,
        "dim_product": products,
        "dim_customer": customers,
        "truth_customer_latent": latents,
        "fct_checkout_session": checkout.sessions,
        "fct_customer_state_at_session": state,
        "fct_payment_attempt": checkout.payment_attempts,
        "fct_checkout_event": checkout.checkout_events,
        "truth_order_probability": checkout.truth,
    }

    if not args.no_write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for name, frame in tables.items():
            frame.to_parquet(OUT_DIR / f"{name}.parquet", index=False)
        print(f"\nwrote {len(tables)} table(s) to {OUT_DIR}")

    print_checkpoint(params, tables, result, ledger)
    print_checkout_checkpoint(params, tables, state, checkout, ledger)
    return 0


def print_checkpoint(params, tables, result, ledger) -> None:
    """The Stage-2 checkpoint: calibration, correlations, constraints."""
    rule = "=" * 78
    customers = tables["dim_customer"]
    latents = tables["truth_customer_latent"]

    print(f"\n{rule}\nSTAGE-2 CHECKPOINT — modules 02-07\n{rule}")

    print("\n1. ROW COUNTS")
    for name, frame in tables.items():
        print(f"   {name:<26} {len(frame):>9,}")
    print(f"   {'pre-window orders drawn':<26} {result.n_pre_window_orders:>9,}")

    print("\n2. PRE-WINDOW CALIBRATION  (2 intercepts solved; zero slopes moved)")
    for calibration in (result.cod_calibration, result.rto_calibration):
        mark = "PASS" if calibration.converged else "FAIL"
        print(f"   [{mark}] {calibration.describe()}")
    print(f"   realised pre-window COD share      {result.realised_cod_share:.4f}")
    print(f"   realised pre-window RTO rate       {result.realised_rto_rate:.4f}  "
          f"(denominator: shipped, i.e. not pre-ship cancelled)")
    print(f"   realised pre-ship cancel rate      {result.realised_cancel_rate:.4f}")

    print("\n3. LATENT -> HISTORY CORRELATIONS  (the confounding, or its absence)")
    merged = customers.merge(latents, on="customer_id")
    has_orders = merged["pre_window_orders"] > 0
    sub = merged[has_orders]
    print(f"   computed over {len(sub):,} customers with >=1 pre-window order")
    print(f"   {'latent':<26} {'vs column':<24} {'r':>8}  {'exp':>4}  {'':>4}")

    cod_rate = sub["pre_window_cod_orders"] / sub["pre_window_orders"]
    rto_rate = sub["pre_window_rto_count"] / sub["pre_window_orders"]
    rates = {"pre_window_cod_orders": cod_rate, "pre_window_rto_count": rto_rate}

    failures = []
    for (latent, column), expected in EXPECTED_SIGNS.items():
        r = float(np.corrcoef(sub[latent], rates[column])[0, 1])
        actual = "+" if r > 0 else "-"
        ok = actual == expected
        if not ok:
            failures.append(f"{latent} vs {column}: r={r:+.4f}, expected {expected}")
        print(f"   {latent:<26} {column:<24} {r:>+8.4f}  {expected:>4}  "
              f"{'ok' if ok else 'WRONG SIGN':>4}")

    print("\n   (rates, not counts — a raw count correlates with order frequency,")
    print("    which is deliberately not latent-driven, and would mask the signal)")

    print("\n4. CONSTRAINTS  (brief §9.5)")
    checks = {
        "delivered + rto <= orders":
            (customers["pre_window_delivered"] + customers["pre_window_rto_count"]
             <= customers["pre_window_orders"]),
        "cod_orders <= orders":
            customers["pre_window_cod_orders"] <= customers["pre_window_orders"],
        "prepaid_success <= orders - cod_orders":
            customers["pre_window_prepaid_success"]
            <= customers["pre_window_orders"] - customers["pre_window_cod_orders"],
        "orders capped by tenure":
            customers["pre_window_orders"]
            <= customers["tenure_days_at_window_start"]
            // int(params.require("distributions.pre_window.min_days_between_orders")),
    }
    for name, ok in checks.items():
        bad = int((~ok).sum())
        print(f"   [{'PASS' if bad == 0 else 'FAIL'}] {name:<44} {bad} violation(s)")
    zero_share = float((customers["pre_window_orders"] == 0).mean())
    print(f"   [INFO] customers with NO pre-window history      {zero_share:.1%}")
    print("          -> their pit_cod_share will be NULL (decision A18)")

    print("\n5. CAL-09  (slope immutability, across all five model blocks)")
    # Partial run: modules 08 onward have not executed, so most coefficients are
    # legitimately unconsumed. The value-mismatch arm still applies in full.
    cal09 = cal_09_no_slope_changed(ledger, params, require_complete_coverage=False)
    print(f"   [{cal09.status.value}] {cal09.actual}")
    if cal09.detail:
        print(f"          {cal09.detail}")

    print(f"\n{rule}")
    if failures:
        print("CHECKPOINT FAILED — latent/history signs are wrong:")
        for line in failures:
            print(f"  {line}")
    else:
        print("Latent -> history directions all match the planted coefficients.")
    print(f"{rule}")


def print_checkout_checkpoint(params, tables, state, checkout, ledger) -> None:
    """The modules 08-12 checkpoint: funnel, calibration, leakage-relevant state."""
    rule = "=" * 78
    sessions = tables["fct_checkout_session"]
    n = len(sessions)

    print(f"\n{rule}\nCHECKPOINT — modules 08-12 (checkout funnel)\n{rule}")

    print("\n1. ROW COUNTS")
    for name in ("fct_checkout_session", "fct_customer_state_at_session",
                 "fct_payment_attempt", "fct_checkout_event", "truth_order_probability"):
        print(f"   {name:<32} {len(tables[name]):>9,}")
    orders = int((~sessions["checkout_abandoned"]).sum())
    print(f"   {'converted sessions (-> orders)':<32} {orders:>9,}")
    print(f"   {'events per session':<32} "
          f"{len(tables['fct_checkout_event']) / n:>9.2f}")

    print("\n2. JOINT SOLVE  (beta_0 and alpha_0 are interdependent)")
    for calibration in (checkout.conversion_calibration, checkout.cod_calibration):
        mark = "PASS" if calibration.converged else "FAIL"
        print(f"   [{mark}] {calibration.describe()}")
    print(f"   final-pass drift: beta_0 {checkout.solve_drift['beta_0']:.2e}, "
          f"alpha_0 {checkout.solve_drift['alpha_0']:.2e}")

    print("\n3. CALIBRATION TARGETS")
    targets = params.require("calibration_targets")
    rows = [
        ("CAL-01 COD share of orders", checkout.cod_share, targets["cod_share"]),
        ("CAL-06 checkout conversion", checkout.conversion_rate,
         targets["checkout_conversion"]),
        ("CAL-07 % of COD from payment failure", checkout.switch_cod_share_of_cod,
         targets["pct_cod_from_payment_failure"]),
    ]
    for name, actual, target in rows:
        lo = float(target["target"]) - float(target["tol"])
        hi = float(target["target"]) + float(target["tol"])
        ok = lo <= actual <= hi
        print(f"   [{'PASS' if ok else 'FAIL'}] {name:<38} {actual:.4f}  "
              f"target {float(target['target']):.3f} +/-{float(target['tol']):.3f}  "
              f"({target['severity']})")

    print("\n4. FUNNEL  (Branch-5 diagnosis)")
    for step, share in checkout.abandon_breakdown.items():
        note = ""
        if step == "FEE_REVEAL" and share == 0:
            note = "  <- 0 by design: baseline shipping_fee_charged = 0"
        print(f"   abandoned at {step:<16} {share:>7.2%}{note}")
    print(f"   converted{'':<20} {checkout.conversion_rate:>7.2%}")
    print(f"   switch-COD share of all orders  {checkout.switch_cod_share_of_orders:>7.2%}"
          f"   (spec 10.3 expects ~4.2%)")

    print("\n5. POINT-IN-TIME STATE  (decision A18: no imputation)")
    null_cod = state["pit_cod_share"].isna().mean()
    print(f"   pit_cod_share NULL                {null_cod:>7.2%}  "
          f"(= sessions with no prior order)")
    print(f"   pit_has_history TRUE              {state['pit_has_history'].mean():>7.2%}")
    print(f"   pit_rto_rate_shrunk NULL          "
          f"{state['pit_rto_rate_shrunk'].isna().mean():>7.2%}  (never NULL by design)")
    print(f"   pit_is_new_customer TRUE          {state['pit_is_new_customer'].mean():>7.2%}")
    tiers = state["pit_risk_tier_rule_based"].value_counts(normalize=True)
    print("   rule tier mix                     "
          + "  ".join(f"{t} {tiers.get(t, 0):.1%}" for t in ("LOW", "MED", "HIGH")))

    print("\n6. LK-06  (shrinkage prior is the declared constant)")
    lk06 = lk_06_shrinkage_prior_is_declared(
        float(params.require("priors.rto_prior")),
        float(params.require("priors.shrinkage_k")),
        params,
    )
    print(f"   [{lk06.status.value}] {lk06.actual}")

    print("\n7. CAL-09  (slope immutability)")
    cal09 = cal_09_no_slope_changed(ledger, params, require_complete_coverage=False)
    print(f"   [{cal09.status.value}] {cal09.actual}")

    print(f"\n{rule}")
    print("PROVISIONAL: modules 13-17 do not exist, so no in-window order resolves.")
    print("Every pit_* value reflects PRE-WINDOW history only. When the A1 day loop")
    print("closes, pit_cod_share and pit_rto_rate_shrunk gain in-window history and")
    print("BOTH intercepts must be re-solved. These are working numbers, not a")
    print("calibration.")
    print(rule)


if __name__ == "__main__":
    sys.exit(main())
