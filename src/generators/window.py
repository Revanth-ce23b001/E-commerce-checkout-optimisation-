"""The decision-A1 day loop: modules 09–17 simulated day by day, in order.

Why the loop exists
-------------------
Module 09 needs in-window order *outcomes*, which module 17 produces. With ~2.8
sessions per customer, repeat sessions are the common case, so this is not an edge
case — it is the mechanism behind H3, BR-02 and BR-03. Decision A1 resolved it by
simulating the window one day at a time and running the full 10→17 chain for each
day's sessions before advancing.

**Why day batching is safe for outcomes.** The minimum outcome resolution is 4
days, so no order placed on a given day can resolve that same day. Applying only
resolutions from *strictly earlier* days is therefore provably as accurate as
per-session processing, and conservative where it is not exact: understating what
was knowable is safe, overstating it is leakage.

**Placements are exact, not batched.** Sessions within a day are processed in
occurrence-rank order — all first-of-day sessions, then the rare second, then the
rarer third — so a customer's later session sees their earlier order. Ranks above
zero are a small share of traffic, which makes this cheap; batching them would
have silently under-counted ``pit_orders_placed`` for those sessions.

The joint solve
---------------
``alpha_0`` (conversion), ``beta_0`` (COD) and ``gamma_0`` (RTO) are mutually
dependent through the point-in-time features, so they are solved alternately
until all three stop moving. The two pre-window intercepts are **not** part of
this: pre-window history is generated before the window opens and no in-window
quantity can reach back into it, so they are provably independent and are solved
once in module 07.

Decision A7 governs ``gamma_0``: it is calibrated against the **blended** RTO rate
alone. CAL-03 and CAL-04 are emergent and are reported, never solved for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.random import Generator

from src.generators import payment_attempts as pay
from src.generators import predictors as pred
from src.generators import rto as rto_mod
from src.generators.conversion import split_hurdles
from src.generators.orders import draw_cancellations, m2_risk_tier
from src.models.calibrate import CalibrationResult, scaled_tolerance, solve_intercept
from src.models.logit import logistic
from src.utils.shrinkage import shrink_rate


@dataclass
class WindowState:
    """Per-customer running counters. Seeded from pre-window history."""

    placed: np.ndarray
    resolved: np.ndarray
    delivered: np.ndarray
    rto_count: np.ndarray
    cod_orders: np.ndarray
    prepaid_success: np.ndarray
    payment_failures: np.ndarray
    value_sum: np.ndarray
    value_count: np.ndarray
    last_order_day: np.ndarray

    @staticmethod
    def pre_window_arrays(customers: pd.DataFrame) -> dict[str, np.ndarray]:
        """Extract the pre-window seed once, outside the calibration loop."""
        return {
            "placed": customers["pre_window_orders"].to_numpy(np.int64),
            "delivered": customers["pre_window_delivered"].to_numpy(np.int64),
            "rto_count": customers["pre_window_rto_count"].to_numpy(np.int64),
            "cod_orders": customers["pre_window_cod_orders"].to_numpy(np.int64),
            "prepaid_success": customers["pre_window_prepaid_success"].to_numpy(np.int64),
            "payment_failures": customers["pre_window_payment_failures"].to_numpy(np.int64),
        }

    @classmethod
    def from_arrays(cls, n: int, pre: dict[str, np.ndarray]) -> "WindowState":
        """A fresh state for one pass. Every array is copied, so a pass can never
        inherit counters from the previous bisection iteration."""
        return cls(
            placed=pre["placed"].copy(),
            resolved=(pre["delivered"] + pre["rto_count"]).copy(),
            delivered=pre["delivered"].copy(),
            rto_count=pre["rto_count"].copy(),
            cod_orders=pre["cod_orders"].copy(),
            prepaid_success=pre["prepaid_success"].copy(),
            payment_failures=pre["payment_failures"].copy(),
            value_sum=np.zeros(n, dtype=np.float64),
            value_count=np.zeros(n, dtype=np.int64),
            last_order_day=np.full(n, np.nan, dtype=np.float64),
        )


@dataclass
class WindowMetrics:
    conversion_rate: float = 0.0
    cod_share: float = 0.0
    rto_rate_blended: float = 0.0
    rto_rate_cod: float = 0.0
    rto_rate_prepaid: float = 0.0
    n_orders: int = 0
    n_shipped: int = 0
    n_censored: int = 0
    n_cancelled: int = 0
    switch_cod_orders: int = 0
    extra: dict = field(default_factory=dict)


def build_day_index(sessions: pd.DataFrame, customers: pd.DataFrame) -> dict:
    """Group session positions by day and by within-customer-day occurrence rank.

    The rank groups are what make placements exact rather than day-batched: rank 0
    is processed, counters update, then rank 1 sees them.
    """
    position = {cid: i for i, cid in enumerate(customers["customer_id"])}
    customer_position = sessions["customer_id"].map(position).to_numpy()
    day_index = sessions["date_id"].map(
        {d: i for i, d in enumerate(sorted(sessions["date_id"].unique()))}
    ).to_numpy()

    frame = pd.DataFrame({"day": day_index, "customer": customer_position})
    rank = frame.groupby(["day", "customer"]).cumcount().to_numpy()

    batches: list[list[np.ndarray]] = []
    for day in range(day_index.max() + 1):
        on_day = day_index == day
        day_batches = []
        for r in range(rank[on_day].max() + 1 if on_day.any() else 0):
            positions = np.flatnonzero(on_day & (rank == r))
            if len(positions):
                day_batches.append(positions)
        batches.append(day_batches)

    return {
        "customer_position": customer_position,
        "day_index": day_index,
        "batches": batches,
        "max_rank": int(rank.max()) + 1,
        "multi_session_share": float((rank > 0).mean()),
    }


def pit_arrays(state: WindowState, customers_at: np.ndarray, prior: float, k: float) -> dict:
    """Read the running counters for one batch of sessions."""
    placed = state.placed[customers_at]
    resolved = state.resolved[customers_at]
    rto = state.rto_count[customers_at]
    cod = state.cod_orders[customers_at]
    success = state.prepaid_success[customers_at]
    failures = state.payment_failures[customers_at]
    attempts = success + failures

    return {
        "placed": placed,
        "resolved": resolved,
        "delivered": state.delivered[customers_at],
        "rto_count": rto,
        "cod_orders": cod,
        "prepaid_success": success,
        "payment_failures": failures,
        # Decision A18: NULL where the denominator is zero. Never imputed.
        "cod_share": np.where(placed > 0, cod / np.maximum(placed, 1), np.nan),
        "payment_failure_rate": np.where(
            attempts > 0, failures / np.maximum(attempts, 1), np.nan
        ),
        # The A18 exception: shrinkage at n=0 RETURNS the declared prior.
        "rto_rate_shrunk": shrink_rate(
            rto.astype(np.float64), resolved.astype(np.float64), prior, k
        ),
        "is_new": state.delivered[customers_at] == 0,
    }
