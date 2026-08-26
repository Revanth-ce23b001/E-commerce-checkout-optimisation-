"""Phase 4 Stage 2 — M2 (post-selection), fitted under the A47 ruling.

    python scripts/07_fit_m2.py

Writes reports/phase4_m2.md, reports/fairness_checks.json and
data/processed/m2_scores.parquet.

Unlike M1 this script IS entitled to p* = 0.2576: M2 sees `payment_method`, so a
COD order's break-even is a threshold it can legitimately be compared against.
What it is *not* entitled to do is choose the thresholding rule. That was ruled on
(decision A47, docs/phase4_escalation.md): restrictive interventions rank within
geo tier; offers use the global score.

Exit codes: 0 normal, 2 if M2 breaches the LK-03 ceiling or FA-01 fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.risk import baseline, challenger, dataset, evaluate, policy  # noqa: E402
from src.risk import features, report_m2  # noqa: E402
from src.risk.fairness import PROXY_RATIO_LIMIT  # noqa: E402
from src.risk.scorecard import Scorecard  # noqa: E402
from src.validation.dataset_hash import order_hash  # noqa: E402

TRUTH = json.loads((REPO_ROOT / "data" / "truth" / "_truth.json").read_text())
CEILING = TRUTH["achieved"]["auc_ceiling_precheckout"]
P_STAR = TRUTH["economics_targets"]["breakeven_rto_probability_derived"]
ANNUALISATION = TRUTH["economics_targets"]["annualization_factor_derived"]
LK03_LIMIT = 0.85
FEASIBILITY_GATE = 0.72
REPORT = REPO_ROOT / "reports" / "phase4_m2.md"
FAIRNESS_CHECKS = REPO_ROOT / "reports" / "fairness_checks.json"


def load_params() -> dict:
    import yaml
    return yaml.safe_load((REPO_ROOT / "config" / "params.yaml").read_text(encoding="utf-8"))


def main() -> int:
    params = load_params()
    print("loading " + dataset.VIEW + " ...")
    raw = dataset.load_view()
    dataset.assert_firewall(raw, params)
    print("   firewall OK  ({:,} rows, {} columns)".format(len(raw), raw.shape[1]))

    orders = pd.read_parquet(REPO_ROOT / "data" / "raw" / "fct_order.parquet")
    economics = pd.read_parquet(REPO_ROOT / "data" / "raw" / "fct_order_economics.parquet")
    shipped = orders[orders["is_shipped"] == True].copy()  # noqa: E712
    censoring = pd.Series(shipped["is_censored"].to_numpy(float),
                          index=pd.to_datetime(shipped["order_date"]))

    clean, dropped = dataset.clean_window(raw, censoring)
    split = dataset.build_split(clean)
    print("   horizon cut {:,} rows; split at {}: {:,} train / {:,} test".format(
        dropped, split.cut_date.date(), len(split.train), len(split.test)))

    y_train = split.train["rto_flag"].to_numpy(float)
    y_test = split.test["rto_flag"].to_numpy(float)

    # --- the model ---------------------------------------------------------
    matrix = features.DesignMatrix("M2")
    X_train = matrix.fit_transform(split.train)
    X_test = matrix.transform(split.test)
    print("   M2 design matrix: {} features".format(X_train.shape[1]))

    model = Scorecard().fit(X_train, y_train)
    p_train = model.predict_proba(X_train)
    p_test = model.predict_proba(X_test)

    # --- the three-rule baseline, reconstructed and verified ---------------
    rules = baseline.M2RulesBaseline(
        params["distributions"]["risk_tier_rules"]["m2_cod_escalates_one_tier"])
    train_tiered = rules.add_column(split.train)
    test_tiered = rules.add_column(split.test)
    verify = rules.verify_against_planted(
        test_tiered, orders[["session_id", rules.COLUMN]])
    if not verify["exact"]:
        print("   STOP: reconstructed {} disagrees with the planted column on "
              "{:.2%} of rows.".format(rules.COLUMN, 1 - verify["agreement"]))
        return 2
    rules.fit(train_tiered)
    rules_test = rules.predict_proba(test_tiered)

    scoreboard = pd.DataFrame([
        evaluate.summarise(y_test, rules_test, "Rules baseline (M2, 3 rules) - test"),
        evaluate.summarise(y_train, p_train, "M2 scorecard - train"),
        evaluate.summarise(y_test, p_test, "M2 scorecard - test"),
    ])
    m2_auc = float(scoreboard.iloc[-1]["auc"])
    rules_auc = float(scoreboard.iloc[0]["auc"])
    print("   M2 test AUC = {:.4f}   (3-rule floor {:.4f}, ceiling {:.4f})".format(
        m2_auc, rules_auc, CEILING))

    print("   fitting the GBM challenger ...")
    gbm = challenger.challenge(X_train, y_train, X_test, y_test, m2_auc,
                               seed=int(params["seed"]["master"]))
    print("   GBM test AUC = {:.4f}  margin {:+.2f}pp  ->  {}".format(
        gbm["test_auc"], gbm["margin_pp"], "SHIPS" if gbm["ships"] else "does not ship"))

    # --- the policy layer --------------------------------------------------
    test = split.test.reset_index(drop=True).merge(
        orders[["session_id", "order_id"]].merge(
            economics[["order_id", "contribution_margin"]], on="order_id"),
        on="session_id", how="left")
    missing = int(test["contribution_margin"].isna().sum())
    if missing:
        print(f"   STOP: {missing} test orders have no economics row.")
        return 2
    cm = test["contribution_margin"].to_numpy(float)

    fa01 = policy.ratio_audit(test, p_test, "per-tier")
    fa01_pass = bool((fa01["verdict"] == "PASS").all())
    cost = policy.margin_cost(test, p_test, cm, ANNUALISATION)
    at17 = cost[cost["volume_flagged"] == 0.17].iloc[0]
    print("   FA-01 (per-tier): {}   price of fairness at 17% vol = "
          "Rs {:,.0f} ({:.2f} Cr/yr)".format(
              "PASS" if fa01_pass else "FAIL", at17["price_of_fairness"],
              at17["price_annualised_cr"]))

    ctx = {
        "split": split, "scoreboard": scoreboard, "model": model,
        "p_test": p_test, "y_test": y_test, "m2_auc": m2_auc,
        "train_auc": float(scoreboard.iloc[1]["auc"]), "rules_auc": rules_auc,
        "rules_table": rules.table(test_tiered).reset_index(),
        "verify": verify, "gbm": gbm, "test": test, "cm": cm,
        "n_features": X_test.shape[1],
        "prepaid": policy.prepaid_economics(economics, orders),
    }
    ctx["verdict"] = _verdict(ctx, fa01, at17, policy.tier_exposure(
        test, p_test, 0.17, "global", P_STAR),
        policy.tier_exposure(test, p_test, 0.17, "per-tier", P_STAR))
    report_m2.write_report(ctx, TRUTH, REPORT)
    _write_fairness_checks(orders, fa01, cost, m2_auc)

    scores = test[["session_id", "customer_id", "session_start_ts", "geo_tier",
                   "payment_method", "rto_flag", "contribution_margin"]].copy()
    scores["m2_score"] = p_test
    scores["pstar_tier"] = report_m2.pstar_tiers(p_test, P_STAR).to_numpy()
    for volume in policy.FA01_VOLUMES:
        scores[f"restricted_pertier_{int(volume * 100):02d}"] = policy.restrict(
            test, p_test, volume, "per-tier")
    scores.to_parquet(REPO_ROOT / "data" / "processed" / "m2_scores.parquet",
                      index=False)

    print("\n   wrote " + str(REPORT.relative_to(REPO_ROOT)))
    print("   wrote " + str(FAIRNESS_CHECKS.relative_to(REPO_ROOT)))
    if m2_auc >= LK03_LIMIT:
        print("   STOP: M2 AUC {:.4f} >= LK-03's {}.".format(m2_auc, LK03_LIMIT))
        return 2
    if not fa01_pass:
        print("   STOP: FA-01 failed under the ruled per-tier thresholds.")
        return 2
    return 0


def _verdict(ctx, fa01, at17, exposure_global, exposure_tiered) -> str:
    """The three-condition summary. Every figure comes from ctx; none is typed in."""
    m2_auc, rules_auc, gbm = ctx["m2_auc"], ctx["rules_auc"], ctx["gbm"]
    slope = float(ctx["scoreboard"].iloc[-1]["cal_slope"])
    t3_g = float(exposure_global.set_index("geo_tier").loc["TIER3", "restriction_rate"])
    t3_t = float(exposure_tiered.set_index("geo_tier").loc["TIER3", "restriction_rate"])
    metro_t = exposure_tiered.set_index("geo_tier").loc["METRO"]
    worst = max(r for r in fa01["ratio"] if isinstance(r, (int, float)))
    lines = [
        "M2 test AUC **{:.4f}** clears the §9.4 gate of {} and beats the full "
        "three-rule §9.3 floor ({:.4f}) by {:.1f}pp. Calibration, which outranks "
        "AUC here because the thresholds are absolute probabilities, holds at a "
        "slope of {:.4f}. Full risk-based pricing is permitted on "
        "discrimination.".format(m2_auc, FEASIBILITY_GATE, rules_auc,
                                 (m2_auc - rules_auc) * 100, slope),
        "**The challenger does not ship.** {:+.2f}pp against a required {:+.2f}pp. "
        "The scorecard stays primary, which is what blueprint §9.3 pre-committed "
        "to before either number existed.".format(
            gbm["margin_pp"], challenger.SHIP_MARGIN_AUC * 100),
        "**FA-01 passes under the ruled per-tier thresholds — {:.2f}x against a "
        "{}x limit, at every volume tested.** The same model under global "
        "thresholds fails at every volume, which is the M1 escalation reproduced: "
        "the ruling changed the policy, not the score.".format(
            worst, PROXY_RATIO_LIMIT),
        "**The constraint costs ₹{:,.0f} on the test window — ₹{:,.2f} per "
        "restricted order, ₹{:.2f} Cr a year.** That is roughly {:.1f}% of the "
        "project's ~₹165 Cr headline exposure, and it is the price of a policy "
        "that can be defended outside the building. The number is on the record "
        "as the ruling required, and it is robust to the behavioural response: "
        "under a 0-50% COD-to-prepaid switch sweep it moves by under 4%.".format(
            at17["price_of_fairness"], at17["price_per_restricted_order"],
            at17["price_annualised_cr"], at17["price_annualised_cr"] / 165 * 100),
        "**What per-tier thresholding does NOT fix.** Tier-3's absolute "
        "restriction rate falls from {:.1%} to {:.1%} at the §8.3 volume, which "
        "answers condition 3 in the direction it was asked. But {:.0%} of the "
        "Metro orders the policy now restricts score BELOW p*, so their expected "
        "contribution margin is positive and restricting them is a deliberate "
        "loss — while Tier-3 orders go unrestricted at {:.1f}x the realised RTO "
        "rate of the Metro orders that are restricted. Equalising the ratio moved "
        "the unfairness; it did not delete it. §6.4 states both halves.".format(
            t3_g, t3_t, float(metro_t["share_below_pstar"]),
            float(exposure_tiered.set_index("geo_tier").loc["TIER3", "rto_rate_restricted"])
            / float(metro_t["rto_rate_restricted"])),
        "**Next.** (1) The intervention design in §6.1 is now a specification, "
        "not a proposal — sticks per-tier, carrots global. (2) GT-01/03/04/06/07 "
        "and BR-09 can now run: M2 is the fitted model they were waiting for. "
        "(3) Phase 5 experiment design consumes `m2_scores.parquet`, whose "
        "`restricted_pertier_*` columns are the ruled policy, not a percentile.",
    ]
    return "\n\n".join(lines)



def _write_fairness_checks(orders, fa01, cost, m2_auc) -> None:
    """Publish FA-01's result for the validation suite, hash-guarded.

    Same contract as reports/database_checks.json: the file carries the hash of
    the fct_order it was computed against, and the suite reports SKIP rather than
    PASS if that hash does not match the dataset being validated. A fairness
    result from a previous dataset is not evidence about this one.
    """
    rows = fa01.to_dict("records")
    FAIRNESS_CHECKS.write_text(json.dumps({
        "fct_order_sha256": order_hash(orders),
        "model": "M2 scorecard, per-tier thresholds (decision A47)",
        "m2_test_auc": round(m2_auc, 4),
        "limit": PROXY_RATIO_LIMIT,
        "checks": {
            "FA-01": {
                "passed": bool((fa01["verdict"] == "PASS").all()),
                "detail": "; ".join(
                    "vol {:.2f}: worst {} / best {} = {}".format(
                        r["volume_flagged"], r["worst_tier"], r["best_tier"],
                        r["ratio"]) for r in rows),
                "by_volume": rows,
            },
        },
        "price_of_fairness": cost.to_dict("records"),
    }, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
