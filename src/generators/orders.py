"""Modules 13–14 — ``fct_order`` and pre-ship cancellations.

Business concept
----------------
**``is_shipped`` is the RTO-rate denominator** (CLAUDE.md invariant 8). Pre-ship
cancellations are removed from the RTO population *before* the RTO draw, because
an order that never left the warehouse cannot come back from a doorstep. Getting
this wrong makes every RTO rate in the project wrong — and it would not look
wrong, it would just look like a slightly lower failure rate.

Both modules are deliberately thin. Everything economically interesting about an
order was already decided at Stage 2 (order value, discount, promise) or Stage 3
(payment method, switch flag); module 13 materialises it, and module 14 removes a
few percent before the outcome is drawn.

``order_risk_tier_rule_based`` is the **M2** baseline (decision A21). It is the
blueprint §9.3 three-rule heuristic *including* payment method, which is why it is
hard-blocked from the M1 feature set. Its Stage-2 sibling —
``pit_risk_tier_rule_based``, prior RTO + tenure only — lives on
``fct_customer_state_at_session`` and is safe.

Spec references
---------------
- Spec §3.10  — the column list
- Brief §9.11 — orders and cancellations; is_shipped is the denominator
- Decision A21 — the two rule tiers
- Decision A29 — quantity is Stage 2, so gmv is fully determined at session time
"""

from __future__ import annotations

import numpy as np

CANCEL_ACTORS = ("CUSTOMER", "SELLER", "SYSTEM")


def build_orders(
    session_positions: np.ndarray,
    session_arrays: dict[str, np.ndarray],
    payment_method: np.ndarray,
    paid_via_switch: np.ndarray,
    pit_rto_rate_shrunk: np.ndarray,
    pit_is_new: np.ndarray,
    tier_rules: dict,
) -> dict[str, np.ndarray]:
    """Materialise converted sessions as orders. One order per session (§3.10).

    Works on positional arrays rather than DataFrames because it runs inside the
    decision-A1 day loop, which executes ~100 times per calibration solve. Pandas
    in that inner loop would dominate the runtime for no benefit.
    """
    is_cod = payment_method == 1  # 1 = COD, 0 = PREPAID

    return {
        "session_position": session_positions,
        "order_ts": session_arrays["session_start_ts"][session_positions],
        "order_day": session_arrays["day_index"][session_positions],
        "customer_position": session_arrays["customer_position"][session_positions],
        "gmv": session_arrays["prospective_gmv"][session_positions],
        "order_value": session_arrays["order_value"][session_positions],
        "discount_pct": session_arrays["discount_pct"][session_positions],
        "estimated_delivery_days": session_arrays["estimated_delivery_days"][session_positions],
        "is_cod": is_cod,
        "paid_via_switch": paid_via_switch,
        "order_risk_tier_rule_based": m2_risk_tier(
            pit_rto_rate_shrunk, pit_is_new, is_cod, tier_rules
        ),
    }


def m2_risk_tier(
    rto_rate_shrunk: np.ndarray,
    is_new: np.ndarray,
    is_cod: np.ndarray,
    rules: dict,
) -> np.ndarray:
    """The M2 (post-selection) rule baseline — blueprint §9.3, all three rules.

    Prior RTO + tenure, then payment method escalates one tier. This is the floor
    any risk model has to beat, and the ₹25.7% break-even threshold belongs to it
    rather than to M1 (spec §4.3): applying an M1 score to an M2 threshold is a
    category error that would mis-tier a large share of traffic.
    """
    rank = np.zeros(len(rto_rate_shrunk), dtype=np.int8)
    med = rto_rate_shrunk >= float(rules["med_rto_rate_shrunk"])
    if bool(rules["med_if_new_customer"]):
        med = med | is_new
    rank[med] = 1
    rank[rto_rate_shrunk >= float(rules["high_rto_rate_shrunk"])] = 2

    if bool(rules["m2_cod_escalates_one_tier"]):
        rank = np.where(is_cod, np.minimum(rank + 1, 2), rank)

    return np.array(["LOW", "MED", "HIGH"], dtype=object)[rank]


def draw_cancellations(
    n_orders: int,
    u_cancel: np.ndarray,
    u_actor: np.ndarray,
    rates: dict,
) -> tuple[np.ndarray, np.ndarray]:
    """Module 14 — pre-ship cancellations. Returns ``(is_cancelled, cancel_actor)``.

    Drawn from pre-allocated uniforms so the cancellation set is identical across
    calibration iterations. If it moved with the intercept, the RTO denominator
    would move too and the bisection would be solving a shifting target.

    Returns the actor as an object array with None on non-cancelled orders, which
    is what the ``ord_cancel_coherent`` CHECK constraint expects.
    """
    actor_rates = np.array([float(rates[a.lower()]) for a in CANCEL_ACTORS])
    total = actor_rates.sum()
    if total >= 1.0:
        raise ValueError(
            f"Pre-ship cancellation rates sum to {total}, which would cancel every "
            "order. Check fulfilment.preship_cancel_rate."
        )

    is_cancelled = u_cancel < total
    actor = np.array([None] * n_orders, dtype=object)

    # Split the cancelled set across actors in proportion to their rates.
    shares = np.cumsum(actor_rates / total)
    which = np.searchsorted(shares, u_actor, side="right").clip(0, len(CANCEL_ACTORS) - 1)
    actor[is_cancelled] = np.array(CANCEL_ACTORS, dtype=object)[which[is_cancelled]]

    return is_cancelled, actor
