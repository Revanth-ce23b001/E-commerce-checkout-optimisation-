"""Modules 11a / 11b / 12 — conversion, abandonment, and the checkout-event
projection.

Business concept
----------------
Abandonment has to be *causally connected to what happened*. A session that
abandons because a bank declined a card is a different business problem from one
that abandons because the address form was painful, and ``abandon_step`` is what
lets the analysis tell them apart. That is Branch 5 of the opportunity model.

Decision A26 — why there are two hurdles
----------------------------------------
Spec §14 runs payment attempts before conversion, so that payment failure can
*cause* abandonment. But a single conversion draw taken afterwards could label a
session as abandoning at the ADDRESS step when it had already paid successfully —
an impossible session, which the DDL's ``ses_funnel_monotone`` correctly rejects.

So the conversion logit is evaluated as two sequential hurdles::

    p_address = p_convert ** s          (module 11a, BEFORE payment)
    p_payment = p_convert ** (1 - s)    (module 11b, before the payment attempt)

Their product is exactly ``p_convert`` by construction, so splitting costs no
accuracy. Under this ordering ``ses_funnel_monotone`` becomes *unreachable*
rather than merely unviolated — the constraint turns into a proof that the
ordering is right.

Decision A25 — where a session is recorded as dying
---------------------------------------------------
Precedence, first match wins: ``PAYMENT_FAILURE`` (set by 11c, immutable) →
``FEE_REVEAL`` (shipping fee charged and it dominates the logit) →
``PAYMENT_PAGE`` → ``ADDRESS``. Deterministic, no new randomness, no new
coefficient.

**In the baseline ``shipping_fee_charged`` is 0 for every order**, so the
fee-reveal term contributes nothing and ``FEE_REVEAL`` never fires. That is
correct, not a bug: the shipping fee is an *intervention lever*, and FEE_REVEAL
is the diagnosis that exists to catch it when the lever is pulled.

Spec references
---------------
- Spec §3.6, §3.8 — the funnel columns and the event table
- Brief §9.10     — abandonment must be causally connected
- Decisions A2 (7 slopes), A12 (events are a projection), A25, A26
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.models.logit import CoefficientLedger, LogitAssembler, logistic

BLOCK = "conversion_model"


def build_conversion_predictor(
    params,
    sessions: pd.DataFrame,
    state: pd.DataFrame,
    ledger: CoefficientLedger,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Assemble the 7-slope conversion logit **without** its intercept.

    Decision A2 kept this deliberately small. Conversion is not a
    hypothesis-bearing surface in Phase 1, so it gets seven slopes drawn from
    blueprint §3 Branch 5 — fee reveal, address friction, promise length,
    device — rather than a 21-slope apparatus of invented assumptions.
    """
    coefficients = params.require(f"{BLOCK}.coefficients")
    centering = params.require("distributions.centering")
    economics = params.require("economics")
    n = len(sessions)

    assembler = LogitAssembler(block=BLOCK, n_rows=n, ledger=ledger)

    assembler.add_numeric(
        "pit_is_new_customer",
        coefficients["pit_is_new_customer"],
        state["pit_is_new_customer"].to_numpy(float),
    )
    assembler.add_numeric(
        "log_order_value",
        coefficients["log_order_value"],
        np.log(
            np.maximum(sessions["order_value"].to_numpy(float), 1.0)
            / float(centering["order_value_scale"])
        ),
    )
    assembler.add_numeric(
        "address_completeness",
        coefficients["address_completeness"],
        sessions["address_completeness_score"].to_numpy(float),
    )
    assembler.add_numeric(
        "est_delivery_days_centered",
        coefficients["est_delivery_days_centered"],
        sessions["estimated_delivery_days"].to_numpy(float)
        - float(centering["est_delivery_days_center"]),
    )
    # Baseline shipping fee is 0, so this term is identically zero today. It is
    # assembled anyway so the coefficient is recorded (CAL-09) and so the lever
    # works the moment a scenario turns the fee on.
    fee = float(economics["shipping_fee_charged"])
    assembler.add_numeric(
        "shipping_fee_charged_gt0",
        coefficients["shipping_fee_charged_gt0"],
        np.full(n, 1.0 if fee > 0 else 0.0),
    )
    assembler.add_numeric(
        "device_web",
        coefficients["device_web"],
        (sessions["device_type"].to_numpy() == "WEB").astype(float),
    )
    assembler.add_numeric(
        "cart_size_ge3",
        coefficients["cart_size_ge3"],
        (sessions["cart_size"].to_numpy() >= 3).astype(float),
    )

    components = assembler.components()
    slope_sum = np.zeros(n, dtype=np.float64)
    for contribution in components.values():
        slope_sum += contribution
    return slope_sum, components


def split_hurdles(
    p_convert: np.ndarray, address_share: float
) -> tuple[np.ndarray, np.ndarray]:
    """Split one conversion probability into two sequential hurdles.

    ``p_address * p_payment == p_convert`` exactly, so the split is a
    presentational decomposition rather than a change to the model.
    """
    if not 0.0 < address_share < 1.0:
        raise ValueError(f"address_hurdle_share must be in (0, 1), got {address_share}.")
    p = np.clip(p_convert, 1e-12, 1.0)
    return p**address_share, p ** (1.0 - address_share)


def draw_hurdles(
    slope_sum: np.ndarray,
    intercept: float,
    noise: np.ndarray,
    u_address: np.ndarray,
    u_payment: np.ndarray,
    address_share: float,
) -> dict[str, np.ndarray]:
    """Modules 11a and 11b: who clears the address step, and the payment page."""
    p_convert = logistic(slope_sum + intercept + noise)
    p_address, p_payment = split_hurdles(p_convert, address_share)

    cleared_address = u_address < p_address
    cleared_payment_page = cleared_address & (u_payment < p_payment)
    return {
        "p_convert": p_convert,
        "cleared_address": cleared_address,
        "reached_payment_page": cleared_payment_page,
    }


def assemble_conversion(
    params,
    sessions: pd.DataFrame,
    hurdles: dict[str, np.ndarray],
    is_cod_intent: np.ndarray,
    payment,
    conversion_components: dict[str, np.ndarray],
) -> pd.DataFrame:
    """Module 12 — resolve every session to a final state.

    Order of resolution matters and follows the A25 precedence exactly:
    a payment failure overrides everything, because it is a fact about what
    happened rather than an inference from the logit.
    """
    n = len(sessions)
    economics = params.require("economics")

    cleared_address = hurdles["cleared_address"]
    reached_payment = hurdles["reached_payment_page"]

    final_method = np.array([None] * n, dtype=object)
    abandon_step = np.array([None] * n, dtype=object)
    switched = np.zeros(n, dtype=bool)
    attempt_count = np.zeros(n, dtype=np.int16)
    payment_rail = np.array([None] * n, dtype=object)

    # Hurdle failures. FEE_REVEAL vs PAYMENT_PAGE is decided by whether the fee
    # term is the dominant negative contributor (decision A25).
    fee_dominant = _fee_is_dominant_negative(conversion_components, float(
        economics["shipping_fee_charged"]
    ))
    abandon_step[~cleared_address] = "ADDRESS"
    payment_page_abandon = cleared_address & ~reached_payment
    abandon_step[payment_page_abandon & fee_dominant] = "FEE_REVEAL"
    abandon_step[payment_page_abandon & ~fee_dominant] = "PAYMENT_PAGE"

    # COD intent converts straight through: nothing left to fail.
    converts_cod = reached_payment & is_cod_intent
    final_method[converts_cod] = "COD"

    # Prepaid intent goes through the payment machine. The outcome carries
    # boolean masks over ALL sessions, so no index reconciliation is needed.
    rails = np.array(list(params.require("payment_failure.rail_mix")), dtype=object)
    attempt_count = payment.attempt_count.copy()

    final_method[payment.succeeded] = "PREPAID"
    payment_rail[payment.succeeded] = rails[payment.rail_index[payment.succeeded]]

    final_method[payment.switched_to_cod] = "COD"
    switched[payment.switched_to_cod] = True

    # Immutable and always wins (decision A25).
    abandon_step[payment.abandoned] = "PAYMENT_FAILURE"

    # pandas infers a str dtype here and turns None into NaN, so `!= None` is
    # True for every row. Null tests use pd.isna() throughout for that reason.
    abandoned = pd.isna(final_method)

    result = sessions.copy()
    result["address_completed"] = cleared_address
    result["payment_page_reached"] = reached_payment
    result["intended_payment_method"] = np.where(
        reached_payment, np.where(is_cod_intent, "COD", "PREPAID"), None
    )
    result["final_payment_method"] = final_method
    result["payment_rail"] = payment_rail
    result["switched_to_cod_after_failure"] = switched
    result["payment_attempt_count"] = attempt_count
    result["checkout_abandoned"] = abandoned
    result["abandon_step"] = abandon_step

    _assert_funnel_coherent(result)
    return result


def _fee_is_dominant_negative(
    components: dict[str, np.ndarray], shipping_fee: float
) -> np.ndarray:
    """Is the fee-reveal term the largest negative contribution to the logit?

    With a zero baseline fee the term is identically zero and this is always
    False, so FEE_REVEAL never fires — correctly.
    """
    fee_term = components.get("shipping_fee_charged_gt0")
    if fee_term is None or shipping_fee <= 0:
        return np.zeros(len(next(iter(components.values()))), dtype=bool)

    stacked = np.column_stack([
        np.minimum(v, 0.0) for k, v in components.items() if k != "shipping_fee_charged_gt0"
    ])
    most_negative_other = stacked.min(axis=1) if stacked.size else np.zeros_like(fee_term)
    return (fee_term < 0) & (fee_term <= most_negative_other)


def _assert_funnel_coherent(frame: pd.DataFrame) -> None:
    """The invariants the DDL enforces, checked before anything is written.

    ``ses_funnel_monotone`` should be *unreachable* under the A26 ordering. If it
    ever fires, the module order has regressed and the whole point-in-time story
    is suspect — so it is checked here rather than discovered at load time.
    """
    reached_without_address = (
        frame["payment_page_reached"] & ~frame["address_completed"]
    ).sum()
    if reached_without_address:
        raise ValueError(
            f"{reached_without_address} session(s) reached the payment page without "
            "completing the address step. Decision A26 makes this unreachable — the "
            "module ordering has regressed."
        )

    abandoned = frame["checkout_abandoned"].to_numpy()
    has_method = ~frame["final_payment_method"].isna().to_numpy()
    has_step = ~frame["abandon_step"].isna().to_numpy()
    if (abandoned & has_method).any():
        raise ValueError("An abandoned session carries a final payment method.")
    if (~abandoned & has_step).any():
        raise ValueError("A converted session carries an abandon_step.")
    if (abandoned & ~has_step).any():
        raise ValueError("An abandoned session has no abandon_step — Branch 5 undiagnosable.")


# ---------------------------------------------------------------------------
# fct_checkout_event — decision A12: a PROJECTION, not a process
# ---------------------------------------------------------------------------


def project_checkout_events(sessions: pd.DataFrame) -> pd.DataFrame:
    """Emit ``fct_checkout_event`` from resolved session state.

    Decision A12: this table is **not** an independent stochastic process. Every
    row is implied by what already happened, so it is walked out deterministically
    — no new randomness, no new parameters, no new seed substream. Modelling it
    separately would risk it disagreeing with the session row it describes.

    Timestamps are interpolated from ``session_start_ts`` at fixed offsets.
    """
    steps: list[tuple[str, np.ndarray, int]] = []
    started = np.ones(len(sessions), dtype=bool)
    steps.append(("CHECKOUT_STARTED", started, 0))
    steps.append(("ADDRESS_COMPLETED", sessions["address_completed"].to_numpy(bool), 30))
    steps.append(("PAYMENT_PAGE_REACHED", sessions["payment_page_reached"].to_numpy(bool), 60))
    steps.append(("FEE_DISPLAYED", sessions["payment_page_reached"].to_numpy(bool), 65))
    steps.append(("METHOD_SELECTED", sessions["payment_page_reached"].to_numpy(bool), 75))
    steps.append(("PAYMENT_ATTEMPTED", sessions["payment_attempt_count"].to_numpy() > 0, 105))
    steps.append(("METHOD_SWITCHED",
                  sessions["switched_to_cod_after_failure"].to_numpy(bool), 150))
    steps.append(("ORDER_PLACED", ~sessions["checkout_abandoned"].to_numpy(bool), 180))
    steps.append(("ABANDONED", sessions["checkout_abandoned"].to_numpy(bool), 200))

    session_ids = sessions["session_id"].to_numpy()
    session_ts = pd.to_datetime(sessions["session_start_ts"].to_numpy())
    method = sessions["final_payment_method"].to_numpy()
    rail = sessions["payment_rail"].to_numpy() if "payment_rail" in sessions else None

    frames = []
    for name, mask, offset in steps:
        if not mask.any():
            continue
        detail = None
        if name == "METHOD_SELECTED":
            detail = [
                f'{{"method": "{m}"}}' if m is not None else '{"method": null}'
                for m in method[mask]
            ]
        elif name == "PAYMENT_ATTEMPTED" and rail is not None:
            detail = [
                f'{{"rail": "{r}"}}' if r is not None else '{"rail": null}'
                for r in rail[mask]
            ]
        frames.append(pd.DataFrame({
            "session_id": session_ids[mask],
            "event_name": name,
            "event_ts": session_ts[mask] + pd.Timedelta(seconds=offset),
            "seconds_since_session_start": offset,
            "event_detail": detail if detail is not None else None,
        }))

    events = pd.concat(frames, ignore_index=True)
    events = events.sort_values(
        ["session_id", "seconds_since_session_start"], ignore_index=True
    )
    events["event_seq"] = (
        events.groupby("session_id").cumcount() + 1
    ).astype(np.int16)
    return events
