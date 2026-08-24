"""Module 18 — ``rto_reason``, ``rto_reason_class``, ``ndr_code``.

Business concept
----------------
Hard-coding a reason distribution would make the §7 avoidability waterfall
**circular** — we would be assuming the 65% we later "discover". So each reason's
probability is a softmax over driver-weighted scores, and the ADDRESSABLE /
STRUCTURAL split *emerges* and has to be validated.

Decision A3 — the log transform
--------------------------------
§13.2's ``base_weights`` sum to 1.00: they are *probabilities*. Feeding
probabilities into a softmax as *logits* flattens 22%/14%/…/3% into a 9.3%–11.3%
band and produces a 51.5% addressable share instead of 65%. So the score is
``log(base_weight[r]) + Σ driver_weight × driver``, which reproduces the stated
weights **exactly** at zero drivers while leaving the drivers free to move the
split. It is a correction to the *implementation*, not to the intended
distribution.

Decision A4 — frozen weights
-----------------------------
The driver matrix is frozen and hashed; CAL-10 fails if it changes. It will
**not** be tuned to make CAL-08 pass. There is no class-level renormalisation:
the softmax normalises across the ten reasons within an order, and the split is
measured from the realised draws.

Two hard gates, enforced rather than hoped for (spec §11.2):
``INSUFFICIENT_CASH_AT_DELIVERY`` is impossible on a prepaid order (DQ-11), and
``NEVER_ORDERED_LOW_INTENT`` is suppressed once a customer has ≥5 delivered orders.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.random import Generator

SUPPRESSED = -1e9  # a gate, applied on the score scale before the softmax


def generate_rto_reasons(
    params,
    orders: pd.DataFrame,
    drivers: dict[str, np.ndarray],
    rng: Generator,
) -> pd.DataFrame:
    """Assign a reason to every RTO order. Returns reason, class and NDR code."""
    cfg = params.require("rto_reasons")
    base = cfg["base_weights"]
    reasons = list(base)
    weights = cfg["driver_weights"]

    rto = orders["rto_flag"].fillna(False).to_numpy(bool)
    n = int(rto.sum())
    if n == 0:
        raise ValueError("No RTO orders — module 18 has nothing to explain.")

    # Decision A3: log the base weights so they ARE the zero-driver probabilities.
    scores = np.tile(np.log([float(base[r]) for r in reasons]), (n, 1))

    context = {k: v[rto] for k, v in drivers.items()}
    for j, reason in enumerate(reasons):
        for term, coefficient in weights.get(reason, {}).items():
            if float(coefficient) == 0.0:
                continue  # ruled to zero under A4; declared, not deleted
            if term not in context:
                raise ValueError(
                    f"rto_reasons.driver_weights[{reason}][{term}] has no driver. "
                    "Every declared weight must multiply something."
                )
            scores[:, j] += float(coefficient) * context[term]

    _apply_gates(params, scores, reasons, context)

    probabilities = _softmax(scores)
    draw = rng.random(n)
    chosen = (probabilities.cumsum(axis=1) < draw[:, None]).sum(axis=1).clip(0, len(reasons) - 1)
    reason_array = np.array(reasons, dtype=object)[chosen]

    class_map = cfg["class_map"]
    ndr_map = cfg["ndr_code_map"]
    return pd.DataFrame({
        "order_id": orders.loc[rto, "order_id"].to_numpy(),
        "rto_reason": reason_array,
        "rto_reason_class": np.array([class_map[r] for r in reason_array], dtype=object),
        "ndr_code": np.array([ndr_map[r] for r in reason_array], dtype=object),
    })


def _apply_gates(params, scores: np.ndarray, reasons: list, context: dict) -> None:
    """Spec §11.2. A gate is a hard impossibility, not a small probability."""
    gates = params.require("rto_reasons.gates")

    for reason, rule in gates.items():
        j = reasons.index(reason)
        if "requires_payment_method" in rule:
            # DQ-11: a prepaid customer cannot fail to have cash at the door.
            scores[~context["is_cod"].astype(bool), j] = SUPPRESSED
        if "suppress_if_pit_orders_delivered_gte" in rule:
            threshold = float(rule["suppress_if_pit_orders_delivered_gte"])
            scores[context["pit_orders_delivered"] >= threshold, j] = SUPPRESSED


def _softmax(scores: np.ndarray) -> np.ndarray:
    """Row-wise softmax, shifted for numerical stability.

    Normalises WITHIN an order, across the ten reasons. No class-level
    renormalisation — that is what keeps the 65/35 split measured (decision A4).
    """
    shifted = scores - scores.max(axis=1, keepdims=True)
    exponentiated = np.exp(shifted)
    return exponentiated / exponentiated.sum(axis=1, keepdims=True)


def build_reason_drivers(
    orders: pd.DataFrame,
    state: pd.DataFrame,
    latents: pd.DataFrame,
    geography: pd.DataFrame,
    sessions: pd.DataFrame,
    products: pd.DataFrame,
    attempt_delay: np.ndarray,
    is_month_end: np.ndarray,
) -> dict[str, np.ndarray]:
    """Assemble every driver the frozen weight matrix names, on the order grain."""
    latent_by_id = latents.set_index("customer_id")
    state_by_session = state.set_index("session_id")
    geo_by_id = geography.set_index("geography_id")
    session_by_id = sessions.set_index("session_id")

    customer = orders["customer_id"]
    session = orders["session_id"]
    geo = orders["delivery_geography_id"]

    return {
        "latent_intent_z": latent_by_id["latent_intent"].reindex(customer).to_numpy(float),
        "latent_liquidity_z": latent_by_id["latent_liquidity"].reindex(customer).to_numpy(float),
        "address_completeness":
            session_by_id["address_completeness_score"].reindex(session).to_numpy(float),
        "serviceability_z": _z(geo_by_id["serviceability_score"].reindex(geo).to_numpy(float)),
        "courier_reliability_z":
            _z(geo_by_id["courier_reliability_score"].reindex(geo).to_numpy(float)),
        "attempt_delay_days_z": _z(attempt_delay),
        "geo_tier_tier3": (geo_by_id["geo_tier"].reindex(geo).to_numpy() == "TIER3").astype(float),
        "pit_is_new_customer":
            state_by_session["pit_is_new_customer"].reindex(session).to_numpy(float),
        "pit_orders_delivered":
            state_by_session["pit_orders_delivered"].reindex(session).to_numpy(float),
        "discount_pct_centered":
            (orders["discount_pct"].to_numpy(float) - 0.08) / 0.10,
        "est_delivery_days_centered":
            orders["estimated_delivery_days"].to_numpy(float) - 4.0,
        "cart_size_ge3":
            (session_by_id["cart_size"].reindex(session).to_numpy(float) >= 3).astype(float),
        "log_order_value": np.log(np.maximum(orders["order_value"].to_numpy(float), 1.0) / 1000.0),
        "is_month_end": is_month_end,
        "is_cod": (orders["payment_method"] == "COD").to_numpy().astype(float),
    }


def _z(values: np.ndarray) -> np.ndarray:
    values = np.nan_to_num(np.asarray(values, dtype=float), nan=0.0)
    sd = values.std(ddof=0)
    return (values - values.mean()) / sd if sd > 0 else np.zeros_like(values)
