"""Load the risk-model training population, enforce the firewall, split on time.

Three things happen here and nothing else happens anywhere else:

1. **The only read.** ``analytics.vw_risk_model_input`` is the sole permitted
   source (spec §4.4, Phase 3 closeout §2). If a feature is not in the view,
   that is a finding about the feature, not a reason to widen the view.

2. **The firewall is asserted, not assumed.** Every loaded column must appear in
   ``params.leakage_guard.safe_feature_whitelist``; no column may appear in
   ``hard_blocked``. The Stage-4 bar (``attempt_delay_days``,
   ``delivery_delay_days``) is asserted a second time, by name, because Phase 3
   closeout §3.2 names it the single most likely leakage vector in Phase 4 — H6
   makes realised delay the most attractive-looking feature in the warehouse and
   it is determined after dispatch. A model containing it does not predict RTO,
   it observes one.

3. **The censoring horizon.** The view already drops censored orders, which is
   correct per-row and catastrophic in aggregate at the end of the window:
   RTO resolves slower than delivery, so `is_censored = FALSE` removes RTOs
   preferentially. Measured, the last three weeks of the window carry RTO rates
   of 11.5%, 1.9% and 0.0% against a stable ~18.7% earlier. Training or testing
   on that tail teaches the model that late orders do not fail. The horizon cut
   is the fix; see ``clean_window``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

VIEW = "analytics.vw_risk_model_input"

# Columns the view exposes for M2 only. M1 must project these away
# (spec §4.3). Enforced in `features.py`, named here so both models read one list.
M2_ONLY = ("payment_method", "paid_via_switch", "payment_attempt_count")

KEYS = ("session_id", "customer_id", "session_start_ts")
TARGET = "rto_flag"

# Asserted by name in addition to the whitelist sweep. Phase 3 closeout §3.2.
STAGE_4_BAR = ("attempt_delay_days", "delivery_delay_days", "delivery_attempts",
               "actual_delivery_days")


@dataclass(frozen=True)
class Split:
    """A time-ordered train/test split, plus everything needed to describe it."""

    train: pd.DataFrame
    test: pd.DataFrame
    cut_date: pd.Timestamp
    horizon_date: pd.Timestamp
    n_dropped_censoring: int

    def describe(self) -> pd.DataFrame:
        rows = []
        for name, frame in (("train", self.train), ("test", self.test)):
            rows.append({
                "split": name,
                "rows": len(frame),
                "from": frame["session_start_ts"].min().date(),
                "to": frame["session_start_ts"].max().date(),
                "rto_rate": round(frame[TARGET].mean(), 4),
                "cod_share": round((frame["payment_method"] == "COD").mean(), 4),
                "customers": frame["customer_id"].nunique(),
            })
        return pd.DataFrame(rows)


def load_view() -> pd.DataFrame:
    """Read the view through the restricted `analyst` role."""
    from load_helpers import read_env, connect

    conn = connect(read_env(), user="analyst", password="analyst_dev_only")
    try:
        frame = pd.read_sql(f"SELECT * FROM {VIEW}", conn)
    finally:
        conn.close()
    return frame


def assert_firewall(frame: pd.DataFrame, params: dict) -> None:
    """LK-01 and LK-02, re-run against what was actually loaded into memory.

    The view is verified at load time by ``scripts/04_verify_database.py``. This
    repeats the check against the DataFrame, because the object the model sees is
    the DataFrame, not the view definition.
    """
    guard = params["leakage_guard"]
    allowed = set(guard["safe_feature_whitelist"])
    blocked = set(guard["hard_blocked"])
    columns = set(frame.columns)

    # LK-02 first. A hard-blocked column is also outside the whitelist, so
    # checking LK-01 first would report every leak as "unknown column" and bury
    # the specific finding under the generic one.
    leaked = sorted(columns & blocked)
    if leaked:
        raise AssertionError(f"LK-02: hard-blocked columns present: {leaked}")

    unknown = sorted(columns - allowed)
    if unknown:
        raise AssertionError(f"LK-01: columns outside the whitelist: {unknown}")

    breached = sorted(columns & set(STAGE_4_BAR))
    if breached:
        raise AssertionError(
            f"Stage-4 bar (closeout §3.2): post-dispatch columns present: {breached}"
        )


def clean_window(frame: pd.DataFrame, order_dates: pd.Series) -> tuple[pd.DataFrame, int]:
    """Cut the population at the last date with zero censoring.

    ``order_dates`` is a Series of ``is_censored`` indexed by order date over the
    FULL shipped population — including the censored rows the view has already
    dropped. That is the only way to see the bias: from inside the view the
    censored orders are invisible, and the collapsing RTO rate looks like a real
    seasonal effect rather than a selection artefact.
    """
    daily = order_dates.groupby(order_dates.index).mean()
    censored_days = daily[daily > 0]
    horizon = pd.Timestamp(censored_days.index.min()) if len(censored_days) else None
    if horizon is None:
        return frame, 0
    keep = frame["session_start_ts"] < horizon
    return frame.loc[keep].copy(), int((~keep).sum())


def build_split(frame: pd.DataFrame, train_fraction: float = 0.70) -> Split:
    """Time-based split. Never random — Phase 1 §9.3.

    A random split leaks temporal structure: the same customer's later orders end
    up in train while their earlier ones sit in test, so point-in-time history
    features are evaluated against a future the model has already seen.
    """
    ordered = frame.sort_values("session_start_ts").reset_index(drop=True)
    cut_index = int(len(ordered) * train_fraction)
    cut_date = ordered.loc[cut_index, "session_start_ts"].normalize()
    train = ordered[ordered["session_start_ts"] < cut_date].copy()
    test = ordered[ordered["session_start_ts"] >= cut_date].copy()
    return Split(train=train, test=test, cut_date=cut_date,
                 horizon_date=ordered["session_start_ts"].max(),
                 n_dropped_censoring=0)
