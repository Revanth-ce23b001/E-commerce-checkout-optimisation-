"""``fct_delivery_event`` and ``truth_order_probability``.

Both are **projections** of state the day loop already resolved — like
``fct_checkout_event`` under decision A12, they consume no randomness. Modelling
them separately would risk them disagreeing with the order row they describe.

``fct_delivery_event`` is where ``attempt_delay_days`` lives (decision A8). It is
the Stage-2 shock input, it exists for every shipped order whether it RTOs or
not, and it is hard-blocked from every model. It is a different variable from
``fct_order.delivery_delay_days``, which is legitimately NULL on every RTO.

``truth_order_probability`` is one row per **session**, not per order, because
``p_convert`` and ``p_cod_intent`` exist for sessions that never became orders —
and those sessions are the CAL-06 denominator.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DELIVERY_COLUMNS = [
    "order_id", "event_seq", "event_ts", "event_name",
    "attempt_number", "attempt_delay_days", "ndr_code", "courier_partner",
]


def build_delivery_events(
    params,
    orders: pd.DataFrame,
    extra: dict,
    geography: pd.DataFrame,
) -> pd.DataFrame:
    """Walk each order's realised delivery path.

    The sequences are short and deterministic:

    * cancelled  → ORDER_PLACED, CANCELLED_PRESHIP
    * censored   → ORDER_PLACED, DISPATCHED            (outcome not yet visible)
    * delivered  → ORDER_PLACED, DISPATCHED, DELIVERED
    * RTO        → ORDER_PLACED, DISPATCHED, 3x DELIVERY_ATTEMPT_FAILED,
                   RTO_INITIATED, RTO_RECEIVED
    """
    cfg = params.require("distributions.delivery")
    couriers = np.array(list(cfg["courier_partners"]), dtype=object)
    max_attempts = int(params.require("fulfilment.max_delivery_attempts"))
    gap = float(cfg["attempt_gap_days"])
    initiation = float(cfg["rto_initiation_days"])

    idx = np.flatnonzero(extra["converted"])
    order_ts = pd.to_datetime(orders["order_ts"].to_numpy())
    order_id = orders["order_id"].to_numpy()

    cancelled = orders["is_cancelled_preship"].to_numpy(bool)
    shipped = orders["is_shipped"].to_numpy(bool)
    censored = orders["is_censored"].to_numpy(bool)
    rto = orders["rto_flag"].fillna(False).to_numpy(bool)
    delivered = orders["is_delivered"].fillna(False).to_numpy(bool)

    dispatch_lag = extra["dispatch_lag"][idx]
    attempt_delay = extra["attempt_delay"][idx]
    days_to_resolve = extra["days_to_resolve"][idx]

    # Courier is a property of the destination cluster, not of the order.
    geo_position = {g: i for i, g in enumerate(geography["geography_id"])}
    courier = couriers[
        orders["delivery_geography_id"].map(geo_position).to_numpy() % len(couriers)
    ]
    ndr = orders["ndr_code"].to_numpy() if "ndr_code" in orders else np.array([None] * len(orders))

    # first attempt = promised date + however late it was
    promise = orders["estimated_delivery_days"].to_numpy(float)
    first_attempt_offset = promise + attempt_delay

    frames: list[pd.DataFrame] = []

    def emit(mask, name, day_offset, attempt=None, with_ndr=False, with_delay=False):
        if not mask.any():
            return
        n = int(mask.sum())
        frames.append(pd.DataFrame({
            "order_id": order_id[mask],
            "event_name": name,
            "event_ts": order_ts[mask] + pd.to_timedelta(day_offset[mask], unit="D"),
            "attempt_number": np.full(n, attempt, dtype=object) if attempt else None,
            "attempt_delay_days": (np.round(attempt_delay[mask]).astype(object)
                                   if with_delay else None),
            "ndr_code": ndr[mask] if with_ndr else None,
            "courier_partner": courier[mask],
        }))

    zero = np.zeros(len(orders))
    emit(np.ones(len(orders), dtype=bool), "ORDER_PLACED", zero)
    emit(cancelled, "CANCELLED_PRESHIP", np.minimum(dispatch_lag, 1.0))
    emit(shipped, "DISPATCHED", dispatch_lag)

    observable = shipped & ~censored
    emit(observable & delivered, "DELIVERED", days_to_resolve.astype(float))

    # An RTO exhausts every attempt (A33 condition b), so all three are emitted.
    for attempt in range(1, max_attempts + 1):
        offset = first_attempt_offset + gap * (attempt - 1)
        emit(observable & rto, "DELIVERY_ATTEMPT_FAILED", offset,
             attempt=attempt, with_ndr=True, with_delay=True)
    emit(observable & rto, "RTO_INITIATED",
         first_attempt_offset + gap * (max_attempts - 1) + initiation)
    emit(observable & rto, "RTO_RECEIVED", days_to_resolve.astype(float))

    events = pd.concat(frames, ignore_index=True)
    events = events.sort_values(["order_id", "event_ts"], kind="stable", ignore_index=True)
    events["event_seq"] = (events.groupby("order_id").cumcount() + 1).astype(np.int16)
    return events[DELIVERY_COLUMNS]


def build_truth_probabilities(sessions: pd.DataFrame, extra: dict) -> pd.DataFrame:
    """One row per SESSION. 🔴 HIDDEN — schema ``truth``, REVOKEd from ``analyst``.

    ``p_rto_precheckout`` is the AUC ceiling: the RTO probability from Stage-1/2
    information alone, frozen before any Stage-4 fact exists. No risk model
    reading safe features can beat it. The gap to ``p_rto_final`` is the
    post-dispatch shock — the honest source of the ceiling.
    """
    return pd.DataFrame({
        "session_id": sessions["session_id"].to_numpy(),
        "p_convert": np.round(extra["p_convert"], 5),
        "p_cod_intent": np.round(extra["p_cod_intent"], 5),
        # Component traces are the JSONB columns in the truth schema. Left NULL
        # here rather than faked: the day loop assembles the logit in two halves
        # (static + per-day dynamic) for speed, so a faithful per-term trace would
        # need the loop to materialise ~25 arrays per day. Recorded as a gap
        # rather than filled with something that only looks like a trace.
        "logit_cod_components": None,
        "p_rto_precheckout": np.round(extra["p_rto_precheckout"], 5),
        "p_rto_final": np.round(extra["p_rto_final"], 5),
        "logit_rto_components": None,
        "post_dispatch_shock": np.round(extra["shock"], 4),
    })
