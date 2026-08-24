"""Modules 15–17 — the two-stage RTO model, delivery, and the outcome draw.

Business concept
----------------
This is deviation D4, and it is the honest source of the accuracy ceiling.

**Stage 1** scores everything knowable at the payment step. That score,
``truth.p_rto_precheckout``, is the theoretical ceiling for any risk model — no
model reading safe features can beat the probability those features actually
generated.

**Stage 2** adds a shock that exists only after the parcel moves: courier
quality, how late the first attempt actually was, whether dispatch slipped, plus
``ν ~ N(0, 0.85)`` for the customer's day and plain luck. That shock is real, is
genuinely unavailable at checkout, and structurally caps achievable AUC at
roughly 0.74–0.79.

That ceiling is a **requirement, not a defect**. Blueprint §9.3 calls a 0.95
model useless. The interview answer this buys: *"my model tops out around 0.76
and I can tell you exactly why — about a third of the variance in whether a
parcel gets delivered is generated after it leaves the warehouse. No
checkout-time model can see that. The fix isn't a better model, it's routing the
risky ones to a better courier."*

Never write ``if is_cod: rto = True``
------------------------------------
``is_cod`` is one coefficient among ~25, at **+1.60**. Every ``rto_flag`` is a
Bernoulli draw from a computed probability (CLAUDE.md rule 9).

Decision A8 lives here
----------------------
``attempt_delay_days`` — promised date to *first delivery attempt* — is the
Stage-2 shock input, and it exists for every shipped order. It is a different
variable from ``fct_order.delivery_delay_days`` (actual − estimated), which is
legitimately NULL on every RTO because the parcel never arrived. The spec
collided the two names; they are separate here, and both are hard-blocked.

Spec references
---------------
- Spec §8.1  — the two-stage formulation
- Spec §8.2  — all 23 Stage-1 coefficients and the three Stage-2 deltas
- Decisions A8 (the rename), A10 (censoring)

⚠️ ``distributions.delivery`` is [A33 PROPOSED] and not yet approved.
"""

from __future__ import annotations

import numpy as np

from src.models.logit import CoefficientLedger, logistic

BLOCK = "rto_model"

# Stage-1 terms that depend on point-in-time state and must therefore be rebuilt
# for each day of the A1 loop. Everything else is static per session.
PIT_TERMS = ("pit_rto_rate_shrunk", "is_new_customer", "log1p_orders_delivered",
             "pit_cod_share")
# Stage-1 terms decided inside the day, once the payment method is known.
INTRA_DAY_TERMS = ("is_cod", "paid_via_switch", "month_end_x_cod")


def record_dynamic_coefficients(params, ledger: CoefficientLedger) -> dict[str, float]:
    """Record the per-day RTO coefficients once, and return them for reuse.

    The coefficients are consumed thousands of times inside the day loop. They are
    recorded into the ledger here so CAL-09 sees exactly one value for each, and
    the returned floats are what the loop actually multiplies — so the ledger is
    a record of what ran, not a copy of the config file.
    """
    coefficients = params.require(f"{BLOCK}.coefficients")
    return {
        term: ledger.record(BLOCK, term, coefficients[term])
        for term in PIT_TERMS + INTRA_DAY_TERMS
    }


def stage1_dynamic(
    coefficients: dict[str, float],
    pit_rto_rate_shrunk: np.ndarray,
    pit_is_new: np.ndarray,
    pit_orders_delivered: np.ndarray,
    pit_cod_share: np.ndarray,
    is_cod: np.ndarray,
    paid_via_switch: np.ndarray,
    is_month_end: np.ndarray,
) -> np.ndarray:
    """The part of the Stage-1 logit that changes as history accumulates.

    ``pit_cod_share`` arrives with NaN for historyless customers; decision A18
    says the term is switched **off**, contributing exactly zero, rather than
    imputed. The level shift for those customers is already carried by
    ``is_new_customer`` (+0.45).
    """
    cod = is_cod.astype(np.float64)
    return (
        coefficients["pit_rto_rate_shrunk"] * pit_rto_rate_shrunk
        + coefficients["is_new_customer"] * pit_is_new.astype(np.float64)
        + coefficients["log1p_orders_delivered"] * np.log1p(pit_orders_delivered)
        + coefficients["pit_cod_share"] * np.nan_to_num(pit_cod_share, nan=0.0)
        + coefficients["is_cod"] * cod
        + coefficients["paid_via_switch"] * paid_via_switch.astype(np.float64)
        + coefficients["month_end_x_cod"] * is_month_end * cod
    )


def record_shock_coefficients(params, ledger: CoefficientLedger) -> dict[str, float]:
    """Record the three Stage-2 deltas once, for reuse inside the day loop."""
    cfg = params.require(f"{BLOCK}.post_dispatch_shock")
    # `noise_sd` is recorded alongside the three deltas. Decision A38 froze it, so
    # CAL-09 protects it like any other coefficient -- and a protected value has to
    # appear in the ledger as CONSUMED, or CAL-09 reports it as a planted
    # relationship silently absent from the data.
    return {
        name: ledger.record(BLOCK, f"shock.{name}", cfg[name])
        for name in ("courier_reliability_z_neg", "attempt_delay_days",
                     "seller_dispatch_late", "noise_sd")
    }


def post_dispatch_shock(
    coefficients: dict[str, float],
    courier_reliability_z: np.ndarray,
    attempt_delay_days: np.ndarray,
    seller_dispatch_late: np.ndarray,
    nu: np.ndarray,
) -> np.ndarray:
    """Stage 2 — the shock that exists only after the parcel moves.

    ``ν`` dominates: at sd 0.85 it is larger than any single deterministic term
    here. That is deliberate. It is the customer's day, the missed phone call, the
    building with no lift — irreducible, and the reason a checkout-time model
    cannot reach 0.95.
    """
    d1 = coefficients["courier_reliability_z_neg"]
    d2 = coefficients["attempt_delay_days"]
    d3 = coefficients["seller_dispatch_late"]

    return (
        d1 * (-courier_reliability_z)          # LOW reliability raises risk
        + d2 * attempt_delay_days
        + d3 * seller_dispatch_late.astype(np.float64)
        + nu
    )


def delivery_timeline(
    params,
    order_day: np.ndarray,
    estimated_delivery_days: np.ndarray,
    base_delivery_days: np.ndarray,
    courier_reliability_z: np.ndarray,
    u_dispatch: np.ndarray,
    u_transit: np.ndarray,
) -> dict[str, np.ndarray]:
    """When the parcel moves, and how late the first attempt is.

    ``attempt_delay_days`` is decision A8's Stage-2 shock input: days between the
    promised date and the **first** delivery attempt. It exists for every shipped
    order, RTO or not — which is exactly what makes it usable as a shock input
    where ``delivery_delay_days`` is not.

    It can be negative (early delivery), and that is left alone rather than
    clipped: an order that arrives early genuinely carries less risk, and clipping
    at zero would throw away half the signal in δ₂.
    """
    cfg = params.require("distributions.delivery")
    lag_cfg = params.require("fulfilment.dispatch_lag_days")

    dispatch_lag = np.exp(
        float(lag_cfg["mu"]) + float(lag_cfg["sigma"]) * u_dispatch
    )
    seller_dispatch_late = dispatch_lag > float(
        params.require("fulfilment.seller_dispatch_late_threshold_days")
    )

    # Worse couriers are slower. This is what correlates attempt_delay_days with
    # courier_reliability_score, so the two Stage-2 terms are not independent —
    # which is realistic, and which is why delta_1 and delta_2 are both modest.
    multiplier = (
        float(cfg["transit_multiplier_mean"])
        + float(cfg["transit_multiplier_sd"]) * u_transit
        - float(cfg["courier_reliability_transit_weight"]) * courier_reliability_z
    )
    transit = np.maximum(base_delivery_days * np.maximum(multiplier, 0.15), 0.5)

    first_attempt_day = order_day + dispatch_lag + transit
    promised_day = order_day + estimated_delivery_days

    return {
        "dispatch_lag_days": dispatch_lag,
        "seller_dispatch_late": seller_dispatch_late,
        "first_attempt_day": first_attempt_day,
        "attempt_delay_days": first_attempt_day - promised_day,
    }


def resolve_outcomes(
    params,
    order_day: np.ndarray,
    timeline: dict[str, np.ndarray],
    rto_flag: np.ndarray,
    u_return: np.ndarray,
    window_days: int,
) -> dict[str, np.ndarray]:
    """Turn a drawn outcome into dates, attempt counts, and the censoring flag.

    Decision A10: an order whose outcome would resolve **after** the last day of
    the window is censored — ``is_censored = TRUE`` and every outcome column NULL.
    The underlying draw still exists in the ``truth`` schema; it is simply not
    observable yet, which is precisely what censoring means.

    DQ-14 requires this to be present rather than tidied away: blueprint §11 needs
    real censoring to *demonstrate* maturation bias instead of asserting it.
    """
    cfg = params.require("distributions.delivery")
    return_cfg = cfg["rto_return_days"]
    bounds = params.require("fulfilment.outcome_resolution_days")
    max_attempts = int(params.require("fulfilment.max_delivery_attempts"))

    # A delivered parcel resolves at the first successful attempt. An RTO burns
    # every attempt, waits for RTO initiation, then travels back.
    return_days = np.exp(float(return_cfg["mu"]) + float(return_cfg["sigma"]) * u_return)
    rto_extra = (
        (max_attempts - 1) * float(cfg["attempt_gap_days"])
        + float(cfg["rto_initiation_days"])
        + return_days
    )

    resolution_day = timeline["first_attempt_day"] + np.where(rto_flag, rto_extra, 0.0)
    days_to_resolve = np.clip(
        np.ceil(resolution_day - order_day),
        int(bounds["lo"]), int(bounds["hi"]),
    ).astype(np.int64)

    resolved_day_index = order_day + days_to_resolve
    is_censored = resolved_day_index > (window_days - 1)

    delivery_attempts = np.where(rto_flag, max_attempts, 1).astype(np.int64)
    # A late parcel often needs a second knock even when it eventually lands.
    delivery_attempts = np.where(
        ~rto_flag & (timeline["attempt_delay_days"] > 0), 2, delivery_attempts
    )

    return {
        "days_to_resolve": days_to_resolve,
        "resolved_day_index": resolved_day_index,
        "is_censored": is_censored,
        "delivery_attempts": delivery_attempts,
    }


def draw_rto(p_rto_final: np.ndarray, uniforms: np.ndarray) -> np.ndarray:
    """The Bernoulli draw. Never an assignment (CLAUDE.md rule 9)."""
    if np.any(p_rto_final < 0.0) or np.any(p_rto_final > 1.0):
        raise ValueError("p_rto_final outside [0, 1].")
    return uniforms < p_rto_final


def probability(linear_predictor: np.ndarray) -> np.ndarray:
    return logistic(linear_predictor)
