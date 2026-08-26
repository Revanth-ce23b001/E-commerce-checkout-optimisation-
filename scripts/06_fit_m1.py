"""Phase 4 Stage 1 — the rules baseline and M1 (pre-selection).

    python scripts/06_fit_m1.py

Writes reports/phase4_m1.md and data/processed/m1_scores.parquet. Prints a short
verdict. Stops before M2 by design: the p*-anchored tiering belongs to M2, and
M1's job is to answer "how risky is this order before we know how they'll pay".

Exit codes: 0 normal, 2 if M1's AUC breaches the 0.80 diagnostic ceiling.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.risk import baseline, dataset, evaluate, report as reporting  # noqa: E402
from src.risk import features  # noqa: E402
from src.risk.scorecard import Scorecard  # noqa: E402

TRUTH = json.loads((REPO_ROOT / "data" / "truth" / "_truth.json").read_text())
AUC_CEILING = TRUTH["achieved"]["auc_ceiling_precheckout"]
P_STAR = TRUTH["economics_targets"]["breakeven_rto_probability_derived"]
LK03_LIMIT = 0.85
DIAGNOSTIC_LIMIT = 0.80
FEASIBILITY_GATE = 0.72
REPORT = REPO_ROOT / "reports" / "phase4_m1.md"


def load_params() -> dict:
    import yaml
    return yaml.safe_load((REPO_ROOT / "config" / "params.yaml").read_text())


def shipped_orders() -> pd.DataFrame:
    """The FULL shipped population, censored rows included.

    Read from Parquet rather than the view on purpose: the view has already
    dropped the censored rows, so from inside it the censoring bias is invisible
    and the collapsing late-window RTO rate looks like a seasonal effect.
    """
    orders = pd.read_parquet(REPO_ROOT / "data" / "raw" / "fct_order.parquet")
    return orders[orders["is_shipped"] == True].copy()  # noqa: E712


def censoring_series(shipped: pd.DataFrame) -> pd.Series:
    return pd.Series(shipped["is_censored"].to_numpy(float),
                     index=pd.to_datetime(shipped["order_date"]))


def censoring_table(shipped: pd.DataFrame) -> pd.DataFrame:
    frame = shipped.copy()
    frame["week_of"] = pd.to_datetime(frame["order_date"]).dt.to_period("W").dt.start_time
    rows = []
    for week, group in frame.groupby("week_of"):
        resolved = group.loc[~group["is_censored"].astype(bool), "rto_flag"]
        rows.append({
            "week_of": str(week.date()),
            "shipped": len(group),
            "censored_pct": round(float(group["is_censored"].mean()) * 100, 2),
            "rto_rate_of_resolved": round(float(resolved.mean()), 4) if len(resolved) else np.nan,
        })
    return pd.DataFrame(rows).tail(8).reset_index(drop=True)


def md_table(frame: pd.DataFrame, floatfmt: str = "{:.4f}") -> str:
    display = frame.copy()
    for col in display.columns:
        if pd.api.types.is_float_dtype(display[col]):
            display[col] = display[col].map(
                lambda v: "" if pd.isna(v) else floatfmt.format(v))
    header = "| " + " | ".join(str(c) for c in display.columns) + " |"
    rule = "|" + "|".join("---" for _ in display.columns) + "|"
    body = ["| " + " | ".join(str(v) for v in row) + " |"
            for row in display.itertuples(index=False)]
    return "\n".join([header, rule] + body)


def main() -> int:
    params = load_params()
    print("loading " + dataset.VIEW + " ...")
    raw = dataset.load_view()
    dataset.assert_firewall(raw, params)
    print("   firewall OK  ({:,} rows, {} columns)".format(len(raw), raw.shape[1]))

    shipped = shipped_orders()
    full_rows, full_rto = len(raw), float(raw["rto_flag"].mean())
    clean, dropped = dataset.clean_window(raw, censoring_series(shipped))
    clean_rto = float(clean["rto_flag"].mean())
    print("   censoring horizon: dropped {:,} rows ({:.1%}); RTO {:.4f} -> {:.4f}".format(
        dropped, dropped / full_rows, full_rto, clean_rto))

    split = dataset.build_split(clean)
    split = dataset.Split(split.train, split.test, split.cut_date,
                          split.horizon_date, dropped)
    print("   split at {}: {:,} train / {:,} test".format(
        split.cut_date.date(), len(split.train), len(split.test)))

    y_train = split.train["rto_flag"].to_numpy(float)
    y_test = split.test["rto_flag"].to_numpy(float)

    rules = baseline.RulesBaseline().fit(split.train)
    rules_test = rules.predict_proba(split.test)

    matrix = features.DesignMatrix("M1")
    X_train = matrix.fit_transform(split.train)
    X_test = matrix.transform(split.test)
    print("   M1 design matrix: {} features".format(X_train.shape[1]))

    model = Scorecard().fit(X_train, y_train)
    p_train = model.predict_proba(X_train)
    p_test = model.predict_proba(X_test)

    scoreboard = pd.DataFrame([
        evaluate.summarise(y_test, rules_test, "Rules baseline (M1, 2 rules) - test"),
        evaluate.summarise(y_train, p_train, "M1 scorecard - train"),
        evaluate.summarise(y_test, p_test, "M1 scorecard - test"),
    ])
    m1_auc = float(scoreboard.loc[2, "auc"])
    print("\n   M1 test AUC = {:.4f}   (ceiling {:.4f}, LK-03 {}, gate {})".format(
        m1_auc, AUC_CEILING, LK03_LIMIT, FEASIBILITY_GATE))

    context = {
        "split": split, "scoreboard": scoreboard, "rules": rules, "model": model,
        "X_test": X_test, "p_test": p_test, "y_test": y_test, "m1_auc": m1_auc,
        "full_rows": full_rows, "full_rto": full_rto, "dropped": dropped,
        "clean_rto": clean_rto, "censoring": reporting.censoring_table(shipped),
        "X_train": X_train, "y_train": y_train, "matrix": matrix,
    }
    reporting.write_report(context, TRUTH, REPORT)

    scores = split.test[["session_id", "customer_id", "session_start_ts",
                         "geo_tier", "payment_method", "rto_flag"]].copy()
    scores["m1_score"] = p_test
    scores.to_parquet(REPO_ROOT / "data" / "processed" / "m1_scores.parquet",
                      index=False)

    print("\n   wrote " + str(REPORT.relative_to(REPO_ROOT)))
    if m1_auc >= DIAGNOSTIC_LIMIT:
        print("   STOP: M1 AUC {:.4f} >= {}. Diagnose leakage before M2.".format(
            m1_auc, DIAGNOSTIC_LIMIT))
        return 2
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
