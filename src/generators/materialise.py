"""Turn the day loop's collected arrays into the analyst-visible tables.

The loop works on flat numpy arrays indexed by session position, because it runs
~100 times inside the calibration solve and pandas in that inner loop would
dominate the runtime. This module runs **once**, at the solved intercepts, and
converts that state into the tables the schema describes.

Nothing here makes a decision or draws anything — if a value is not already in
the collected arrays, it is derived deterministically or left NULL.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.generators.payment_attempts import PaymentOutcome, materialise_attempts
from src.generators.rto import BLOCK  # noqa: F401  (documents the source block)

RAILS_KEY = "payment_failure.rail_mix"


def resolve_sessions(params, sessions: pd.DataFrame, extra: dict) -> pd.DataFrame:
    """Fill the funnel columns on ``fct_checkout_session``."""
    n = len(sessions)
    rails = np.array(list(params.require(RAILS_KEY)), dtype=object)

    reached = extra["reached_payment"].astype(bool)
    cod_intent = extra["cod_intent"].astype(bool)
    converted = extra["converted"]
    is_cod = extra["is_cod_order"]

    final_method = np.array([None] * n, dtype=object)
    final_method[converted & is_cod] = "COD"
    final_method[converted & ~is_cod] = "PREPAID"

    rail_index = extra["rail_final"]
    payment_rail = np.array([None] * n, dtype=object)
    prepaid = converted & ~is_cod
    payment_rail[prepaid] = rails[np.array(rail_index[prepaid], dtype=int)]

    # Decision A25 precedence. FEE_REVEAL cannot fire while the baseline shipping
    # fee is zero, so the branch is absent rather than empty-by-accident.
    abandon_step = np.array([None] * n, dtype=object)
    abandon_step[~extra["cleared_address"].astype(bool)] = "ADDRESS"
    abandon_step[extra["cleared_address"].astype(bool) & ~reached] = "PAYMENT_PAGE"
    abandon_step[extra["pay_abandoned"].astype(bool)] = "PAYMENT_FAILURE"

    frame = sessions.copy()
    frame["address_completed"] = extra["cleared_address"].astype(bool)
    frame["payment_page_reached"] = reached
    frame["intended_payment_method"] = np.where(
        reached, np.where(cod_intent, "COD", "PREPAID"), None
    )
    frame["final_payment_method"] = final_method
    frame["payment_rail"] = payment_rail
    frame["switched_to_cod_after_failure"] = extra["switched"]
    frame["payment_attempt_count"] = extra["attempt_count"].astype(np.int16)
    frame["checkout_abandoned"] = ~converted
    frame["abandon_step"] = abandon_step
    frame["order_id"] = np.where(converted, _order_ids(converted), None)
    return frame


def build_state(params, sessions: pd.DataFrame, extra: dict,
                dates: pd.DataFrame) -> pd.DataFrame:
    """``fct_customer_state_at_session`` from the loop's per-session capture."""
    rules = params.require("distributions.risk_tier_rules")
    placed = extra["pit_placed"].astype(np.int64)
    resolved = extra["pit_resolved"].astype(np.int64)
    delivered = extra["pit_delivered"].astype(np.int64)
    rto = extra["pit_rto_count"].astype(np.int64)
    cod = extra["pit_cod_orders"].astype(np.int64)
    success = extra["pit_success"].astype(np.int64)
    failures = extra["pit_failures"].astype(np.int64)
    attempts = success + failures

    signup = pd.to_datetime(sessions["signup_date"]) if "signup_date" in sessions else None
    frame = pd.DataFrame({
        "session_id": sessions["session_id"].to_numpy(),
        "customer_id": sessions["customer_id"].to_numpy(),
        "pit_orders_placed": placed,
        "pit_orders_delivered": delivered,
        "pit_orders_resolved": resolved,
        "pit_rto_count": rto,
        "pit_rto_rate_raw": _rate(rto, resolved),
        "pit_rto_rate_shrunk": np.round(extra["pit_rto_shrunk"], 4),
        "pit_cod_orders": cod,
        "pit_cod_share": np.round(extra["pit_cod_share"], 4),
        "pit_prepaid_success_count": success,
        "pit_payment_failure_count": failures,
        "pit_payment_failure_rate": _rate(failures, attempts),
        # Decision A30 / limitation L2: in-window orders only, so NULL until a
        # customer places their first one. It was previously hardcoded to NaN,
        # which made the column permanently empty rather than merely sparse.
        "pit_avg_order_value": np.round(extra["pit_avg_order_value"], 2),
        # Same defect class as pit_avg_order_value: the day loop collects
        # pit_last_order_day and nothing consumed it, so the column reached the
        # schema permanently NULL -- and it is a WHITELISTED risk-model feature
        # (params.yaml leakage_guard, sql/04 line 70). Phase 3 would have trained
        # on a column of nulls. Both sides are day indices into dim_date.
        "pit_days_since_last_order": _days_since(sessions, extra, dates),
        "pit_has_history": placed > 0,
        "pit_is_new_customer": extra["pit_new"].astype(bool),
        "pit_has_clean_record": (delivered >= 3) & (rto == 0),
    })
    if signup is not None:
        frame["pit_tenure_days"] = (
            (pd.to_datetime(sessions["date_id"]).to_numpy() - signup.to_numpy())
            / np.timedelta64(1, "D")
        ).astype(np.int64)

    tier = np.full(len(frame), "LOW", dtype=object)
    shrunk = frame["pit_rto_rate_shrunk"].to_numpy()
    med = shrunk >= float(rules["med_rto_rate_shrunk"])
    if bool(rules["med_if_new_customer"]):
        med = med | frame["pit_is_new_customer"].to_numpy()
    tier[med] = "MED"
    tier[shrunk >= float(rules["high_rto_rate_shrunk"])] = "HIGH"
    frame["pit_risk_tier_rule_based"] = tier
    return frame


def build_orders(params, sessions: pd.DataFrame, extra: dict,
                 dates: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """``fct_order`` — one row per converted session (spec §3.10)."""
    converted = extra["converted"]
    idx = np.flatnonzero(converted)
    shipped = extra["shipped"][idx]
    censored = extra["censored"][idx]
    rto = extra["rto_flag"][idx]
    cancelled = extra["cancelled"][idx]

    day_ids = dates["date_id"].to_numpy()
    order_date = pd.to_datetime(sessions["date_id"].to_numpy()[idx])
    est = sessions["estimated_delivery_days"].to_numpy()[idx]

    observable = shipped & ~censored
    delivered = observable & ~rto
    actual_days = np.where(observable, extra["days_to_resolve"][idx], np.nan)
    resolved_index = extra["resolved_day"][idx]
    resolved_date = np.where(
        observable,
        np.clip(resolved_index, 0, len(day_ids) - 1).astype(int),
        -1,
    )

    status = np.full(len(idx), "SHIPPED", dtype=object)
    status[cancelled] = "CANCELLED_PRESHIP"
    status[delivered] = "DELIVERED"
    status[observable & rto] = "RTO"

    frame = pd.DataFrame({
        "order_id": _order_ids(converted)[converted],
        "session_id": sessions["session_id"].to_numpy()[idx],
        "customer_id": sessions["customer_id"].to_numpy()[idx],
        "product_id": sessions["candidate_product_id"].to_numpy()[idx],
        # Denormalised from dim_product (spec 3.10). The live load caught its
        # absence: it is NOT NULL and a foreign key, so the COPY column list
        # silently shifted and the first row failed.
        "seller_id": products.set_index("product_id")["seller_id"]
            .reindex(sessions["candidate_product_id"].to_numpy()[idx]).to_numpy(),
        "delivery_geography_id": sessions["delivery_geography_id"].to_numpy()[idx],
        "order_ts": sessions["session_start_ts"].to_numpy()[idx],
        "order_date": order_date,
        "quantity": sessions["quantity"].to_numpy()[idx],
        "gmv": sessions["prospective_gmv"].to_numpy()[idx],
        # order_value = gmv - discount_amount is a CHECK constraint, so the
        # rupee amount has to be stored, not just the rate.
        "discount_amount": np.round(
            sessions["prospective_gmv"].to_numpy()[idx]
            - sessions["order_value"].to_numpy()[idx], 2),
        "discount_pct": sessions["discount_pct"].to_numpy()[idx],
        "order_value": sessions["order_value"].to_numpy()[idx],
        # Both zero in the baseline. cod_fee_charged is the INTERVENTION LEVER.
        "shipping_fee_charged": float(params.require("economics.shipping_fee_charged")),
        "cod_fee_charged": float(params.require("economics.cod_fee_charged")),
        "payment_method": np.where(extra["is_cod_order"][idx], "COD", "PREPAID"),
        # DQ-13: NULL if and only if the order is COD. The DDL enforces the same
        # thing as a CHECK, and the economics module reads it to pick the PG rate.
        "payment_rail": sessions["payment_rail"].to_numpy()[idx],
        "paid_via_switch": extra["switched"][idx],
        "estimated_delivery_days": est,
        "promised_delivery_date": order_date + pd.to_timedelta(est, unit="D"),
        "order_risk_tier_rule_based": extra["order_tier"][idx],
        "order_status": status,
        "is_cancelled_preship": cancelled,
        "cancel_actor": extra["cancel_actor"][idx],
        "is_shipped": shipped,
        "is_censored": censored,
        # Decision A10: a censored order carries NO outcome at all.
        "rto_flag": np.where(observable, rto, None),
        "is_delivered": np.where(observable, delivered, None),
        "actual_delivery_days": actual_days,
        # Decision A8: legitimately NULL on every RTO -- the parcel never arrived.
        # This is the DIAGNOSTIC column, not the shock input.
        "delivery_delay_days": np.where(delivered, actual_days - est, np.nan),
        "delivery_attempts": np.where(observable, extra["attempts"][idx], np.nan),
        "outcome_resolved_date": np.where(
            observable, day_ids[resolved_date], None
        ),
    })
    return frame


def build_payment_attempts(params, sessions: pd.DataFrame, extra: dict, rng):
    """Reassemble a PaymentOutcome from the collected masks, then materialise."""
    outcome = PaymentOutcome(
        eligible=extra["pay_eligible"].astype(bool),
        succeeded=extra["pay_succeeded"].astype(bool),
        switched_to_cod=extra["switched"],
        abandoned=extra["pay_abandoned"].astype(bool),
        attempt_count=extra["attempt_count"].astype(np.int16),
        rail_index=np.where(extra["rail_final"] >= 0, extra["rail_final"], 0).astype(int),
        failed_first=extra["failed_first"].astype(bool),
        retried=extra["retried"].astype(bool),
        succeeded_second=extra["succeeded_second"].astype(bool),
        switched_rail=extra["switched_rail"].astype(bool),
    )
    return materialise_attempts(params, sessions, outcome, rng)


def _days_since(sessions: pd.DataFrame, extra: dict, dates: pd.DataFrame):
    """Session day index minus the customer's last in-window order day.

    NULL until the customer's first in-window order (decision A30 / limitation
    L2), which is the same convention pit_avg_order_value follows.
    """
    positions = {value: index for index, value in enumerate(dates["date_id"])}
    session_day = np.array([positions[value] for value in sessions["date_id"]],
                           dtype=float)
    gap = session_day - extra["pit_last_order_day"]
    return pd.array(np.where(np.isnan(gap), np.nan, gap), dtype="Int64")


def _order_ids(converted: np.ndarray) -> np.ndarray:
    ids = np.array([None] * len(converted), dtype=object)
    ids[converted] = [f"ORD_{i:08d}" for i in range(int(converted.sum()))]
    return ids


def _rate(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """Decision A18: NULL where the denominator is zero. Never imputed."""
    numerator = numerator.astype(float)
    denominator = denominator.astype(float)
    return np.round(
        np.divide(numerator, denominator,
                  out=np.full_like(numerator, np.nan), where=denominator > 0),
        4,
    )
