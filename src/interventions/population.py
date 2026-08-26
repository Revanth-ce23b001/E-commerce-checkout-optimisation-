"""Assemble the simulation population, and say exactly what it is.

The population is the **M2 test window** — the same 22,520 shipped, uncensored
orders ``scripts/07_fit_m2.py`` scored, reached the same way: the firewalled view,
the censoring horizon cut, the time-ordered split. It is re-derived rather than
taken from ``m2_scores.parquet`` alone because the simulation needs columns the
score file does not carry (the §8.4 protection predicates, the saved-instrument
flag, the geography), and re-running the same three functions is the only way to
be sure the extra columns line up with the scores row for row.

The join is asserted, not assumed: if ``m2_scores.parquet`` and the rebuilt frame
disagree on a single ``session_id``, the run stops. A silently mismatched score
column would produce a plausible, wrong, un-diagnosable answer.

THE SESSION DENOMINATOR
-----------------------
CM per checkout-start session needs the sessions, including the ones that never
became an order. The view has no such row, so the count comes from
``fct_checkout_session`` over the same date range. That window is clean by
construction — the horizon cut lands on the first day carrying any censoring, so
inside it every order has a resolved outcome — and the only orders it contains
that the view drops are pre-ship cancellations, which carry zero on every
economic line under decision A23 and therefore change the numerator not at all.

WHAT THE TRUTH CHANNEL IS FOR
-----------------------------
``p_rto_final`` is joined here, and it is the only place in this phase that reads
the truth schema. It is used to EVALUATE outcomes; it never touches targeting.
Every tier, threshold and eligibility flag is computed from ``m2_score``. The
separation is the design: target with what the business can see, score against
what is true.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.risk import dataset

RAW = Path("data") / "raw"


def _read(repo_root: Path, name: str) -> pd.DataFrame:
    return pd.read_parquet(repo_root / RAW / f"{name}.parquet")


def build(repo_root: Path, params, scores_path: Path) -> dict:
    """The scored, costed, truth-annotated test window."""
    raw = dataset.load_view()
    dataset.assert_firewall(raw, params.raw)

    orders = _read(repo_root, "fct_order")
    economics = _read(repo_root, "fct_order_economics")
    products = _read(repo_root, "dim_product")
    sessions = _read(repo_root, "fct_checkout_session")
    truth_p = _read(repo_root, "truth_order_probability")

    shipped = orders[orders["is_shipped"] == True]  # noqa: E712
    censoring = pd.Series(shipped["is_censored"].to_numpy(float),
                          index=pd.to_datetime(shipped["order_date"]))
    clean, dropped = dataset.clean_window(raw, censoring)
    split = dataset.build_split(clean)
    test = split.test.reset_index(drop=True)

    scores = pd.read_parquet(scores_path)
    if set(scores["session_id"]) != set(test["session_id"]):
        raise AssertionError(
            "m2_scores.parquet does not describe the population this run rebuilt "
            "({:,} scored vs {:,} rebuilt). Re-run `make m2`.".format(
                len(scores), len(test)))

    frame = test.merge(
        scores[["session_id", "m2_score", "pstar_tier"]], on="session_id", how="left")

    # order_value, discount_pct, rto_flag and is_month_end_window already come
    # from the view; taking them again from fct_order would create _x/_y columns
    # and let a later line silently read the wrong one.
    order_cols = ["order_id", "session_id", "product_id", "order_date", "gmv"]
    frame = frame.merge(orders[order_cols], on="session_id", how="left")
    frame = frame.merge(
        economics[["order_id", "contribution_margin", "cogs_value",
                   "forward_shipping_cost", "packaging_cost",
                   "payment_processing_fee"]], on="order_id", how="left")
    frame = frame.merge(
        products[["product_id", "shrink_rate", "weight_band", "cogs_ratio"]],
        on="product_id", how="left")
    frame = frame.merge(truth_p[["session_id", "p_rto_final"]],
                        on="session_id", how="left")

    frame["order_date"] = pd.to_datetime(frame["order_date"])

    weight = params.require("economics.forward_freight_weight_factor")
    frame["weight_factor"] = frame["weight_band"].map(
        {k: float(v) for k, v in weight.items()})

    missing = [c for c in ("m2_score", "contribution_margin", "p_rto_final",
                           "weight_factor", "is_month_end_window")
               if frame[c].isna().any()]
    if missing:
        raise AssertionError(f"nulls after the join in: {missing}")

    lo = frame["session_start_ts"].min().normalize()
    hi = frame["session_start_ts"].max().normalize() + pd.Timedelta(days=1)
    in_window = sessions[(sessions["session_start_ts"] >= lo)
                         & (sessions["session_start_ts"] < hi)]
    started = int(in_window["checkout_started"].sum())

    placed = orders.merge(sessions[["session_id", "session_start_ts"]],
                          on="session_id")
    placed = placed[(placed["session_start_ts"] >= lo)
                    & (placed["session_start_ts"] < hi)]

    return {
        "frame": frame,
        "sessions": started,
        "window": (lo, hi),
        "orders_placed": int(len(placed)),
        "preship_cancelled": int(placed["is_cancelled_preship"].sum()),
        "censored_in_window": int(placed["is_censored"].sum()),
        "dropped_by_horizon": int(dropped),
        "horizon": split.cut_date,
        "abandon_steps": in_window["abandon_step"].value_counts(dropna=False),
        "payment_failure_abandons": int(
            (in_window["abandon_step"] == "PAYMENT_FAILURE").sum()),
    }


def funnel(pop: dict) -> pd.DataFrame:
    """The population, stated once, so no later table has to restate it."""
    sessions = pop["sessions"]
    frame = pop["frame"]
    rows = [
        ("checkout sessions started", sessions, "CM/session denominator"),
        ("orders placed", pop["orders_placed"],
         "{:.2%} checkout conversion".format(pop["orders_placed"] / sessions)),
        ("pre-ship cancelled", pop["preship_cancelled"],
         "zero on every economic line (A23) - carried, not simulated"),
        ("censored inside the window", pop["censored_in_window"],
         "zero by construction: the horizon cut lands on the first censored day"),
        ("shipped, resolved, scored", len(frame),
         "the simulation population"),
    ]
    return pd.DataFrame(rows, columns=["stage", "count", "note"])
