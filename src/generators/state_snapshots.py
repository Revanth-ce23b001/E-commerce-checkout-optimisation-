"""Module 09 — ``fct_customer_state_at_session``. The leakage firewall.

**This is the most important module in the generator.** Everything the risk model
is allowed to know about a customer's history comes from here, and every column
is point-in-time as of ``session_start_ts``.

The rule that makes it real
---------------------------
A prior order counts toward ``pit_orders_delivered`` / ``pit_rto_count`` **only if
its outcome had RESOLVED** before this session. Outcomes take 4–25 days, so an
order placed three days ago has not resolved and must not appear. Getting this
wrong is the single easiest way to leak the future into the past — and it would
not look like a bug: the model would simply get suspiciously good, and LK-03's
AUC would drift up with no obviously-wrong column to blame.

Two different clocks, deliberately
----------------------------------
* **Placements** are applied with ``order_ts < session_start_ts`` — exact, to the
  second, so a customer's second session on the same day sees their first order.
* **Resolutions** are applied at **day granularity**: an order counts only if it
  resolved on a *strictly earlier day*. This is deliberately conservative. Under
  decision A1 the window is simulated day by day, so within-day resolution order
  is not modelled; treating a same-day resolution as not-yet-known can only ever
  *understate* what was knowable, never overstate it. Understating is safe.
  Overstating is leakage.

Decision A18 lives here
-----------------------
A customer with no prior orders gets **NULL**, not an imputed value.
``pit_cod_share`` carries +2.20 in the COD logit; imputing 0.62 there would
manufacture a habit signal for a customer who has no habit. ``pit_has_history``
is the missing indicator. The one exception is ``pit_rto_rate_shrunk``:
empirical-Bayes shrinkage at n=0 *returns* the prior by construction, which is a
computed value rather than an imputed one.

Spec references
---------------
- Spec §3.7  — the column list and the DQ-07 reconciliation invariant
- Spec §4.2  — every column here is SAFE by construction
- Brief §9.7 — "THE MOST IMPORTANT MODULE"; strict chronological order
- Decisions A18 (no imputation), A19 (declared prior), A20 (pit_orders_resolved),
  A21 (rule tier without payment_method)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.utils.shrinkage import shrink_rate

# Columns an in-window order ledger must provide for the day loop to close.
LEDGER_COLUMNS = (
    "customer_id", "order_ts", "outcome_resolved_date",
    "is_cod", "rto_flag", "is_delivered", "order_value",
)


def empty_ledger() -> pd.DataFrame:
    """An in-window order ledger with no rows.

    Modules 13–17 do not exist yet, so nothing resolves inside the window and
    every snapshot reflects pre-window history alone. The accumulation logic
    below is written for the real case and degenerates to this one — so when the
    day loop closes, this module does not change.
    """
    return pd.DataFrame({c: pd.Series(dtype="object") for c in LEDGER_COLUMNS})


def generate_state_snapshots(
    params,
    sessions: pd.DataFrame,
    customers: pd.DataFrame,
    in_window_orders: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Snapshot what was known about each customer at each session timestamp.

    Sessions must already be sorted by ``session_start_ts``; the caller
    (module 08) guarantees it and this function asserts it rather than trusting.
    """
    if not sessions["session_start_ts"].is_monotonic_increasing:
        raise ValueError(
            "Sessions are not in chronological order. Module 09 makes a strict "
            "chronological pass; an unsorted input would silently produce "
            "point-in-time values that depend on future orders (LK-04)."
        )

    ledger = empty_ledger() if in_window_orders is None else in_window_orders
    prior = float(params.require("priors.rto_prior"))
    k = float(params.require("priors.shrinkage_k"))
    tier_rules = params.require("distributions.risk_tier_rules")

    n_customers = len(customers)
    position = {cid: i for i, cid in enumerate(customers["customer_id"])}

    state = _initial_state(customers)
    accumulator = _LedgerAccumulator(ledger, position, n_customers)

    n = len(sessions)
    out = {
        name: np.zeros(n, dtype=dtype)
        for name, dtype in (
            ("pit_orders_placed", np.int64), ("pit_orders_delivered", np.int64),
            ("pit_orders_resolved", np.int64), ("pit_rto_count", np.int64),
            ("pit_cod_orders", np.int64), ("pit_prepaid_success_count", np.int64),
            ("pit_payment_failure_count", np.int64),
        )
    }
    out["pit_value_sum"] = np.zeros(n, dtype=np.float64)
    out["pit_value_count"] = np.zeros(n, dtype=np.int64)
    out["pit_last_order_day"] = np.full(n, np.nan, dtype=np.float64)

    customer_pos = sessions["customer_id"].map(position).to_numpy()
    session_dates = sessions["date_id"].to_numpy()
    session_ts = sessions["session_start_ts"].to_numpy()

    # --- the chronological pass ---------------------------------------------
    for start, stop, day in _day_slices(session_dates):
        # Resolutions from STRICTLY EARLIER days only. Conservative on purpose.
        accumulator.apply_resolutions_before(day, state)
        # Placements are exact to the second, so an earlier session the same day
        # is visible to a later one.
        accumulator.apply_placements_before(session_ts[start], state)

        for i in range(start, stop):
            accumulator.apply_placements_before(session_ts[i], state)
            c = customer_pos[i]
            for name in out:
                out[name][i] = state[name][c]

    return _assemble(
        sessions, customers, customer_pos, out, prior, k, tier_rules, position
    )


# ---------------------------------------------------------------------------
# state accumulation
# ---------------------------------------------------------------------------


def _initial_state(customers: pd.DataFrame) -> dict[str, np.ndarray]:
    """Per-customer counters, seeded from pre-window history.

    Pre-window orders are all resolved by definition — they happened before the
    window opened — so they land in both the placed and resolved counters.
    """
    orders = customers["pre_window_orders"].to_numpy(dtype=np.int64)
    delivered = customers["pre_window_delivered"].to_numpy(dtype=np.int64)
    rto = customers["pre_window_rto_count"].to_numpy(dtype=np.int64)

    return {
        "pit_orders_placed": orders.copy(),
        "pit_orders_resolved": (delivered + rto).copy(),
        "pit_orders_delivered": delivered.copy(),
        "pit_rto_count": rto.copy(),
        "pit_cod_orders": customers["pre_window_cod_orders"].to_numpy(dtype=np.int64).copy(),
        "pit_prepaid_success_count":
            customers["pre_window_prepaid_success"].to_numpy(dtype=np.int64).copy(),
        "pit_payment_failure_count":
            customers["pre_window_payment_failures"].to_numpy(dtype=np.int64).copy(),
        # Pre-window orders carry no recorded value (dim_customer has no such
        # column in spec §3.5), so pit_avg_order_value builds from IN-WINDOW
        # orders only and is NULL until a customer has one. Raised as A30 —
        # inventing a pre-window average would be an unflagged assumption.
        "pit_value_sum": np.zeros(len(customers), dtype=np.float64),
        "pit_value_count": np.zeros(len(customers), dtype=np.int64),
        "pit_last_order_day": np.full(len(customers), np.nan, dtype=np.float64),
    }


class _LedgerAccumulator:
    """Applies in-window placements and resolutions to the running state, in order."""

    def __init__(self, ledger: pd.DataFrame, position: dict, n_customers: int):
        self._empty = len(ledger) == 0
        if self._empty:
            return

        missing = set(LEDGER_COLUMNS) - set(ledger.columns)
        if missing:
            raise ValueError(f"In-window order ledger is missing columns: {sorted(missing)}")

        by_placement = ledger.sort_values("order_ts")
        self._place_customer = by_placement["customer_id"].map(position).to_numpy()
        self._place_ts = by_placement["order_ts"].to_numpy()
        self._place_is_cod = by_placement["is_cod"].to_numpy().astype(bool)
        self._place_value = by_placement["order_value"].to_numpy(dtype=np.float64)
        self._place_day = pd.to_datetime(by_placement["order_ts"]).dt.date.to_numpy()
        self._place_cursor = 0

        resolved = ledger[ledger["outcome_resolved_date"].notna()].sort_values(
            "outcome_resolved_date"
        )
        self._res_customer = resolved["customer_id"].map(position).to_numpy()
        self._res_date = resolved["outcome_resolved_date"].to_numpy()
        self._res_rto = resolved["rto_flag"].to_numpy().astype(bool)
        self._res_delivered = resolved["is_delivered"].to_numpy().astype(bool)
        self._res_cursor = 0

    def apply_placements_before(self, ts, state: dict) -> None:
        if self._empty:
            return
        while self._place_cursor < len(self._place_ts) and self._place_ts[self._place_cursor] < ts:
            i = self._place_cursor
            c = self._place_customer[i]
            state["pit_orders_placed"][c] += 1
            if self._place_is_cod[i]:
                state["pit_cod_orders"][c] += 1
            state["pit_value_sum"][c] += self._place_value[i]
            state["pit_value_count"][c] += 1
            state["pit_last_order_day"][c] = _to_ordinal(self._place_day[i])
            self._place_cursor += 1

    def apply_resolutions_before(self, day, state: dict) -> None:
        if self._empty:
            return
        while self._res_cursor < len(self._res_date) and self._res_date[self._res_cursor] < day:
            i = self._res_cursor
            c = self._res_customer[i]
            state["pit_orders_resolved"][c] += 1
            if self._res_rto[i]:
                state["pit_rto_count"][c] += 1
            if self._res_delivered[i]:
                state["pit_orders_delivered"][c] += 1
            self._res_cursor += 1


def _day_slices(session_dates: np.ndarray):
    """Yield ``(start, stop, day)`` for each contiguous run of same-day sessions."""
    start = 0
    for i in range(1, len(session_dates) + 1):
        if i == len(session_dates) or session_dates[i] != session_dates[start]:
            yield start, i, session_dates[start]
            start = i


def _to_ordinal(day) -> float:
    return float(pd.Timestamp(day).toordinal())


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def _assemble(
    sessions: pd.DataFrame,
    customers: pd.DataFrame,
    customer_pos: np.ndarray,
    out: dict,
    prior: float,
    k: float,
    tier_rules: dict,
    position: dict,
) -> pd.DataFrame:
    placed = out["pit_orders_placed"]
    resolved = out["pit_orders_resolved"]
    rto = out["pit_rto_count"]
    delivered = out["pit_orders_delivered"]

    frame = pd.DataFrame({
        "session_id": sessions["session_id"].to_numpy(),
        "customer_id": sessions["customer_id"].to_numpy(),
    })

    signup = pd.to_datetime(customers["signup_date"]).to_numpy()[customer_pos]
    frame["pit_tenure_days"] = (
        (pd.to_datetime(sessions["date_id"]).to_numpy() - signup)
        / np.timedelta64(1, "D")
    ).astype(np.int64)

    frame["pit_orders_placed"] = placed
    frame["pit_orders_delivered"] = delivered
    frame["pit_orders_resolved"] = resolved
    frame["pit_rto_count"] = rto

    # Decision A18: NULL, not imputed, when the denominator is zero.
    frame["pit_rto_rate_raw"] = _rate_or_null(rto, resolved)
    # The exception. Shrinkage at n=0 RETURNS priors.rto_prior by construction —
    # computed, not imputed — so this column is never NULL. LK-06 asserts the
    # prior used here is the declared constant and not something derived from the
    # generated population.
    frame["pit_rto_rate_shrunk"] = np.round(
        shrink_rate(rto.astype(np.float64), resolved.astype(np.float64), prior, k), 4
    )

    frame["pit_cod_orders"] = out["pit_cod_orders"]
    frame["pit_cod_share"] = _rate_or_null(out["pit_cod_orders"], placed)
    frame["pit_prepaid_success_count"] = out["pit_prepaid_success_count"]
    frame["pit_payment_failure_count"] = out["pit_payment_failure_count"]

    prepaid_attempts = out["pit_prepaid_success_count"] + out["pit_payment_failure_count"]
    frame["pit_payment_failure_rate"] = _rate_or_null(
        out["pit_payment_failure_count"], prepaid_attempts
    )

    session_ordinal = pd.to_datetime(sessions["date_id"]).map(
        lambda d: float(d.toordinal())
    ).to_numpy()
    days_since = session_ordinal - out["pit_last_order_day"]
    frame["pit_days_since_last_order"] = pd.array(
        np.where(np.isnan(days_since), np.nan, days_since), dtype="Int64"
    )

    frame["pit_avg_order_value"] = _rate_or_null(
        out["pit_value_sum"], out["pit_value_count"], decimals=2
    )

    frame["pit_has_history"] = placed > 0
    frame["pit_is_new_customer"] = delivered == 0
    frame["pit_has_clean_record"] = (delivered >= 3) & (rto == 0)
    frame["pit_risk_tier_rule_based"] = _rule_tier(
        frame["pit_rto_rate_shrunk"].to_numpy(),
        frame["pit_is_new_customer"].to_numpy(),
        tier_rules,
    )
    return frame


def _rate_or_null(
    numerator: np.ndarray, denominator: np.ndarray, decimals: int = 4
) -> pd.Series:
    """``numerator / denominator``, or NULL where the denominator is zero.

    Decision A18: never zero, never a population prior. Unknown is unknown.
    """
    numerator = np.asarray(numerator, dtype=np.float64)
    denominator = np.asarray(denominator, dtype=np.float64)
    has_denominator = denominator > 0
    values = np.divide(
        numerator, denominator, out=np.full_like(numerator, np.nan), where=has_denominator
    )
    return pd.Series(np.round(values, decimals))


def _rule_tier(
    rto_rate_shrunk: np.ndarray, is_new: np.ndarray, rules: dict
) -> np.ndarray:
    """Decision A21 — the M1 baseline: prior RTO + tenure. No payment_method.

    The HIGH cut sits at p*, the break-even RTO probability, so the rule baseline
    and the economic threshold agree by construction rather than by coincidence.
    """
    tier = np.full(len(rto_rate_shrunk), "LOW", dtype=object)
    med = rto_rate_shrunk >= float(rules["med_rto_rate_shrunk"])
    if bool(rules["med_if_new_customer"]):
        med = med | is_new
    tier[med] = "MED"
    tier[rto_rate_shrunk >= float(rules["high_rto_rate_shrunk"])] = "HIGH"
    return tier
