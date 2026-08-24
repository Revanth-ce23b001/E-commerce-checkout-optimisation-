"""Module 10 — COD choice, STEP A: intent.

Business concept
----------------
**The two-step structure is the whole reason H11 is answerable.** This module
produces what the customer *wanted*; module 11c produces what they were *forced*
into when a prepaid payment failed. A single draw of COD-or-prepaid could not
tell preference from coercion, and the headline question — "what share of COD is
actually payment friction?" — would be unanswerable.

Never assign COD. Every payment method is a Bernoulli draw from a computed
probability with ~23 named terms, of which the payment-method coefficient is one.
The habit term ``pit_cod_share`` (+2.20) is the strongest observable, and the
inverted-U on order value (+0.28 linear, −0.12 squared) is what makes H4 testable:
COD rises with basket size, then falls again as affluence takes over.

Decision A18 in the logit
-------------------------
A NULL feature contributes **exactly 0.0** — the term is switched off, not filled
in. The level difference for historyless customers is already carried by
``pit_is_new_customer`` (+0.70), which is an approved coefficient. No new
coefficient is introduced, and no habit signal is manufactured for a customer who
has no habit.

Spec references
---------------
- Spec §7.1  — the two-step formulation
- Spec §7.2  — all 23 coefficients
- Spec §7.3  — calibration of beta_0 to the 62% OBSERVED share
- Brief §9.8 — "Never assign COD directly"
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.models.logit import CoefficientLedger, LogitAssembler

BLOCK = "cod_model"


def build_cod_intent_predictor(
    params,
    sessions: pd.DataFrame,
    state: pd.DataFrame,
    customers: pd.DataFrame,
    latents: pd.DataFrame,
    products: pd.DataFrame,
    sellers: pd.DataFrame,
    geography: pd.DataFrame,
    dates: pd.DataFrame,
    ledger: CoefficientLedger,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Assemble the COD-intent logit **without** its intercept.

    Returning the slope sum separately is what lets beta_0 be bisected: the
    ledger would reject a second distinct value for the intercept term, and
    rebuilding the whole design matrix on every iteration would make calibration
    unaffordable. The caller adds the candidate intercept as a scalar.

    Returns
    -------
    ``(slope_sum, components)`` — the component trace goes to
    ``truth_order_probability.logit_cod_components`` as JSONB.
    """
    coefficients = params.require(f"{BLOCK}.coefficients")
    centering = params.require("distributions.centering")
    n = len(sessions)

    ctx = _context(sessions, state, customers, latents, products, sellers, geography, dates)
    assembler = LogitAssembler(block=BLOCK, n_rows=n, ledger=ledger)

    # --- latents: hidden, and the engine of the confounding -----------------
    assembler.add_numeric("latent_trust", coefficients["latent_trust"], ctx["z_trust"])
    assembler.add_numeric("latent_liquidity", coefficients["latent_liquidity"], ctx["z_liquidity"])
    assembler.add_numeric("latent_intent", coefficients["latent_intent"], ctx["z_intent"])
    assembler.add_numeric(
        "latent_price_sensitivity",
        coefficients["latent_price_sensitivity"],
        ctx["z_price_sensitivity"],
    )

    # --- point-in-time history ----------------------------------------------
    assembler.add_numeric("pit_cod_share", coefficients["pit_cod_share"], ctx["pit_cod_share"])
    assembler.add_numeric(
        "log1p_prepaid_success",
        coefficients["log1p_prepaid_success"],
        np.log1p(ctx["prepaid_success"]),
    )
    assembler.add_numeric("is_new_customer", coefficients["is_new_customer"], ctx["is_new"])
    assembler.add_numeric(
        "log1p_orders_delivered",
        coefficients["log1p_orders_delivered"],
        np.log1p(ctx["orders_delivered"]),
    )
    assembler.add_numeric(
        "payment_failure_rate",
        coefficients["payment_failure_rate"],
        ctx["payment_failure_rate"],
    )
    assembler.add_numeric(
        "has_saved_instrument", coefficients["has_saved_instrument"], ctx["has_saved"]
    )

    # --- geography: two channels, access and norms --------------------------
    assembler.add_categorical("geo_tier", coefficients["geo_tier"], ctx["geo_tier"])
    assembler.add_numeric(
        "cod_cultural_index_z", coefficients["cod_cultural_index_z"], ctx["z_cod_culture"]
    )

    # --- order value: the H4 inverted-U -------------------------------------
    log_value = np.log(
        np.maximum(ctx["order_value"], 1.0) / float(centering["order_value_scale"])
    )
    assembler.add_numeric("log_order_value", coefficients["log_order_value"], log_value)
    assembler.add_numeric(
        "log_order_value_sq", coefficients["log_order_value_sq"], log_value**2
    )

    # --- trust proxies (H5) --------------------------------------------------
    assembler.add_numeric(
        "seller_rating_centered",
        coefficients["seller_rating_centered"],
        ctx["seller_rating"] - float(centering["seller_rating_center"]),
    )
    assembler.add_numeric(
        "product_rating_centered",
        coefficients["product_rating_centered"],
        ctx["product_rating"] - float(centering["product_rating_center"]),
    )
    assembler.add_numeric(
        "log1p_review_count_centered",
        coefficients["log1p_review_count_centered"],
        _centre(np.log1p(ctx["review_count"])),
    )

    # --- logistics, cart, category, time ------------------------------------
    assembler.add_numeric(
        "est_delivery_days_centered",
        coefficients["est_delivery_days_centered"],
        ctx["estimated_delivery_days"] - float(centering["est_delivery_days_center"]),
    )
    assembler.add_numeric(
        "discount_pct_centered",
        coefficients["discount_pct_centered"],
        (ctx["discount_pct"] - float(centering["discount_pct_center"]))
        / float(centering["discount_pct_unit"]),
    )
    assembler.add_numeric(
        "cart_size_ge3", coefficients["cart_size_ge3"], (ctx["cart_size"] >= 3).astype(float)
    )
    assembler.add_categorical("category", coefficients["category"], ctx["category"])
    assembler.add_numeric("is_month_end", coefficients["is_month_end"], ctx["is_month_end"])

    components = assembler.components()
    slope_sum = np.zeros(n, dtype=np.float64)
    for contribution in components.values():
        slope_sum += contribution
    return slope_sum, components


def draw_cod_intent(
    slope_sum: np.ndarray,
    intercept: float,
    noise: np.ndarray,
    uniforms: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Draw COD intent. Returns ``(is_cod_intent, p_cod_intent)``.

    ``noise`` is the pre-drawn epsilon ~ N(0, 0.35) and ``uniforms`` the
    pre-allocated common random numbers. Both are fixed across bisection
    iterations, which is what makes the realised share monotone in the intercept.
    """
    from src.models.logit import logistic

    probability = logistic(slope_sum + intercept + noise)
    return uniforms < probability, probability


# ---------------------------------------------------------------------------
# feature assembly
# ---------------------------------------------------------------------------


def _context(
    sessions: pd.DataFrame,
    state: pd.DataFrame,
    customers: pd.DataFrame,
    latents: pd.DataFrame,
    products: pd.DataFrame,
    sellers: pd.DataFrame,
    geography: pd.DataFrame,
    dates: pd.DataFrame,
) -> dict[str, np.ndarray]:
    """Join every driver onto the session grain."""
    latent_by_customer = latents.set_index("customer_id")
    customer_by_id = customers.set_index("customer_id")
    product_by_id = products.set_index("product_id")
    seller_by_id = sellers.set_index("seller_id")
    geo_by_id = geography.set_index("geography_id")
    date_by_id = dates.set_index("date_id")

    customer_ids = sessions["customer_id"]
    product_ids = sessions["candidate_product_id"]
    seller_ids = product_by_id["seller_id"].reindex(product_ids).to_numpy()
    geo_ids = sessions["delivery_geography_id"]

    return {
        "z_trust": latent_by_customer["latent_trust"].reindex(customer_ids).to_numpy(float),
        "z_liquidity":
            latent_by_customer["latent_liquidity"].reindex(customer_ids).to_numpy(float),
        "z_intent": latent_by_customer["latent_intent"].reindex(customer_ids).to_numpy(float),
        "z_price_sensitivity":
            latent_by_customer["latent_price_sensitivity"].reindex(customer_ids).to_numpy(float),

        # Decision A18: NULL -> 0.0 contribution. The term is switched OFF.
        "pit_cod_share": _null_to_zero(state["pit_cod_share"]),
        "payment_failure_rate": _null_to_zero(state["pit_payment_failure_rate"]),
        "prepaid_success": state["pit_prepaid_success_count"].to_numpy(float),
        "orders_delivered": state["pit_orders_delivered"].to_numpy(float),
        "is_new": state["pit_is_new_customer"].to_numpy(float),

        "has_saved":
            customer_by_id["has_saved_prepaid_instrument"]
            .reindex(customer_ids).to_numpy(float),

        "geo_tier": geo_by_id["geo_tier"].reindex(geo_ids).to_numpy(),
        "z_cod_culture": _zscore(
            geo_by_id["cod_cultural_index"].reindex(geo_ids).to_numpy(float)
        ),

        "order_value": sessions["order_value"].to_numpy(float),
        "discount_pct": sessions["discount_pct"].to_numpy(float),
        "cart_size": sessions["cart_size"].to_numpy(float),
        "estimated_delivery_days": sessions["estimated_delivery_days"].to_numpy(float),

        "seller_rating": seller_by_id["seller_rating"].reindex(seller_ids).to_numpy(float),
        "product_rating": product_by_id["product_rating"].reindex(product_ids).to_numpy(float),
        "review_count": product_by_id["review_count"].reindex(product_ids).to_numpy(float),
        "category": product_by_id["category"].reindex(product_ids).to_numpy(),

        "is_month_end":
            date_by_id["is_month_end_window"].reindex(sessions["date_id"]).to_numpy(float),
    }


def _null_to_zero(series: pd.Series) -> np.ndarray:
    """Decision A18: a NULL feature contributes zero, it is not imputed."""
    return series.astype(float).fillna(0.0).to_numpy()


def _zscore(values: np.ndarray) -> np.ndarray:
    sd = values.std(ddof=0)
    return (values - values.mean()) / sd if sd > 0 else np.zeros_like(values)


def _centre(values: np.ndarray) -> np.ndarray:
    return values - values.mean()
