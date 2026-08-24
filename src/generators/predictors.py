"""Static logit assembly for the COD, conversion and RTO models.

Why "static"
------------
Under decision A1 the window is simulated day by day, and each of the three
in-window intercepts is solved by bisection *around* that loop. A full solve
therefore runs the day loop on the order of a hundred times. Rebuilding a
25-term design matrix inside that loop would dominate the runtime and buy
nothing, because most terms never change: a product's rating, a seller's SLA
breach rate, the delivery promise and the latents are fixed for the whole window.

So each model is split in two:

* **static** — everything fixed per session, assembled once here, with every
  coefficient recorded in the ledger exactly once;
* **dynamic** — the point-in-time terms (and, for RTO, the terms decided inside
  the day once the payment method is known), rebuilt per day from plain numpy.

The dynamic coefficients are recorded here too, so CAL-09 still compares what the
generator *consumed* against ``params.yaml`` rather than comparing the config
file to a copy of itself.

Decision A39 — the history-rate terms are CENTRED
-------------------------------------------------
``pit_cod_share`` enters as ``(pit_cod_share − cod_prior)``, so a customer with
no history contributes a deviation of exactly **0.0** — they sit at the
population mean of the habit scale, not at its bottom.

This corrects a real defect. Decision A18 (*no imputation, NULL plus a missing
indicator*) is a ruling about the **analyst-facing tables**. Applying its
"NULL → 0" convention inside the *generator* placed a historyless customer at the
*never-used-COD* extreme, so an established customer of average habit received
``+2.20 × 0.617 = +1.358`` on the COD logit that a new customer did not — working
directly against ``is_new_customer = +0.70`` and pushing BR-01 to +6.96pp against
a ≥10pp floor.

Centring and imputing the prior are the **same model** up to a constant the
intercept absorbs. So the real question was never "impute or not" — it was where
a historyless customer sits on the habit scale, and the answer is the population
mean. **No slope moves**; the intercepts re-solve.

The centring constants are **declared**, never computed from the generated
population — the same rule LK-06 enforces for the shrinkage prior, and for the
same reason: a constant derived from realised outcomes would be a population-level
leak that no column-level check could see.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.models.logit import CoefficientLedger, LogitAssembler

COD_BLOCK = "cod_model"
CONVERSION_BLOCK = "conversion_model"
RTO_BLOCK = "rto_model"

# Terms rebuilt per day rather than assembled once.
COD_DYNAMIC = ("pit_cod_share", "log1p_prepaid_success", "is_new_customer",
               "log1p_orders_delivered", "payment_failure_rate")
CONVERSION_DYNAMIC = ("pit_is_new_customer",)


def session_context(
    sessions: pd.DataFrame,
    customers: pd.DataFrame,
    latents: pd.DataFrame,
    products: pd.DataFrame,
    sellers: pd.DataFrame,
    geography: pd.DataFrame,
    dates: pd.DataFrame,
) -> dict[str, np.ndarray]:
    """Join every fixed driver onto the session grain, once."""
    latent_by_id = latents.set_index("customer_id")
    customer_by_id = customers.set_index("customer_id")
    product_by_id = products.set_index("product_id")
    seller_by_id = sellers.set_index("seller_id")
    geo_by_id = geography.set_index("geography_id")
    date_by_id = dates.set_index("date_id")

    customer_ids = sessions["customer_id"]
    product_ids = sessions["candidate_product_id"]
    seller_ids = product_by_id["seller_id"].reindex(product_ids).to_numpy()
    geo_ids = sessions["delivery_geography_id"]

    def latent(name: str) -> np.ndarray:
        return latent_by_id[f"latent_{name}"].reindex(customer_ids).to_numpy(float)

    return {
        "z_trust": latent("trust"),
        "z_liquidity": latent("liquidity"),
        "z_intent": latent("intent"),
        "z_price_sensitivity": latent("price_sensitivity"),
        "has_saved": customer_by_id["has_saved_prepaid_instrument"]
            .reindex(customer_ids).to_numpy(float),
        "geo_tier": geo_by_id["geo_tier"].reindex(geo_ids).to_numpy(),
        "serviceability": geo_by_id["serviceability_score"].reindex(geo_ids).to_numpy(float),
        "courier_reliability": geo_by_id["courier_reliability_score"]
            .reindex(geo_ids).to_numpy(float),
        "base_delivery_days": geo_by_id["base_delivery_days"].reindex(geo_ids).to_numpy(float),
        "cod_cultural_index": geo_by_id["cod_cultural_index"].reindex(geo_ids).to_numpy(float),
        "order_value": sessions["order_value"].to_numpy(float),
        "discount_pct": sessions["discount_pct"].to_numpy(float),
        "cart_size": sessions["cart_size"].to_numpy(float),
        "estimated_delivery_days": sessions["estimated_delivery_days"].to_numpy(float),
        "address_completeness": sessions["address_completeness_score"].to_numpy(float),
        "device_type": sessions["device_type"].to_numpy(),
        "seller_rating": seller_by_id["seller_rating"].reindex(seller_ids).to_numpy(float),
        "seller_sla_breach_rate": seller_by_id["seller_sla_breach_rate"]
            .reindex(seller_ids).to_numpy(float),
        "product_rating": product_by_id["product_rating"].reindex(product_ids).to_numpy(float),
        "review_count": product_by_id["review_count"].reindex(product_ids).to_numpy(float),
        "category": product_by_id["category"].reindex(product_ids).to_numpy(),
        "is_month_end": date_by_id["is_month_end_window"]
            .reindex(sessions["date_id"]).to_numpy(float),
    }


def cod_static(params, ctx: dict, ledger: CoefficientLedger) -> np.ndarray:
    """The COD-intent logit minus its intercept and its point-in-time terms.

    The inverted-U on order value (+0.28 linear, −0.12 squared) lives here: it is
    what makes H4 testable, with COD rising through the mid basket sizes and
    falling again as affluence takes over above roughly ₹3,200.
    """
    c = params.require(f"{COD_BLOCK}.coefficients")
    centre = params.require("distributions.centering")
    n = len(ctx["z_trust"])
    a = LogitAssembler(block=COD_BLOCK, n_rows=n, ledger=ledger)

    a.add_numeric("latent_trust", c["latent_trust"], ctx["z_trust"])
    a.add_numeric("latent_liquidity", c["latent_liquidity"], ctx["z_liquidity"])
    a.add_numeric("latent_intent", c["latent_intent"], ctx["z_intent"])
    a.add_numeric("latent_price_sensitivity", c["latent_price_sensitivity"],
                  ctx["z_price_sensitivity"])
    a.add_numeric("has_saved_instrument", c["has_saved_instrument"], ctx["has_saved"])
    a.add_categorical("geo_tier", c["geo_tier"], ctx["geo_tier"])
    a.add_numeric("cod_cultural_index_z", c["cod_cultural_index_z"],
                  _z(ctx["cod_cultural_index"]))

    log_value = _log_value(ctx["order_value"], centre)
    a.add_numeric("log_order_value", c["log_order_value"], log_value)
    a.add_numeric("log_order_value_sq", c["log_order_value_sq"], log_value**2)

    a.add_numeric("seller_rating_centered", c["seller_rating_centered"],
                  ctx["seller_rating"] - float(centre["seller_rating_center"]))
    a.add_numeric("product_rating_centered", c["product_rating_centered"],
                  ctx["product_rating"] - float(centre["product_rating_center"]))
    a.add_numeric("log1p_review_count_centered", c["log1p_review_count_centered"],
                  _centre(np.log1p(ctx["review_count"])))
    a.add_numeric("est_delivery_days_centered", c["est_delivery_days_centered"],
                  ctx["estimated_delivery_days"] - float(centre["est_delivery_days_center"]))
    a.add_numeric("discount_pct_centered", c["discount_pct_centered"],
                  _discount(ctx["discount_pct"], centre))
    a.add_numeric("cart_size_ge3", c["cart_size_ge3"], (ctx["cart_size"] >= 3).astype(float))
    a.add_categorical("category", c["category"], ctx["category"])
    a.add_numeric("is_month_end", c["is_month_end"], ctx["is_month_end"])

    return _sum(a)


def conversion_static(params, ctx: dict, ledger: CoefficientLedger) -> np.ndarray:
    """The 7-slope conversion logit minus its intercept and ``pit_is_new_customer``."""
    c = params.require(f"{CONVERSION_BLOCK}.coefficients")
    centre = params.require("distributions.centering")
    fee = float(params.require("economics.shipping_fee_charged"))
    n = len(ctx["z_trust"])
    a = LogitAssembler(block=CONVERSION_BLOCK, n_rows=n, ledger=ledger)

    a.add_numeric("log_order_value", c["log_order_value"], _log_value(ctx["order_value"], centre))
    a.add_numeric("address_completeness", c["address_completeness"], ctx["address_completeness"])
    a.add_numeric("est_delivery_days_centered", c["est_delivery_days_centered"],
                  ctx["estimated_delivery_days"] - float(centre["est_delivery_days_center"]))
    # Zero in the baseline, assembled anyway so the coefficient is recorded and the
    # lever works the moment a scenario turns the fee on.
    a.add_numeric("shipping_fee_charged_gt0", c["shipping_fee_charged_gt0"],
                  np.full(n, 1.0 if fee > 0 else 0.0))
    a.add_numeric("device_web", c["device_web"], (ctx["device_type"] == "WEB").astype(float))
    a.add_numeric("cart_size_ge3", c["cart_size_ge3"], (ctx["cart_size"] >= 3).astype(float))

    return _sum(a)


def rto_static(params, ctx: dict, ledger: CoefficientLedger) -> np.ndarray:
    """Stage-1 RTO minus its intercept, its point-in-time terms and ``is_cod``.

    ``address_completeness`` at **−1.40** is the second-strongest driver here and
    the cheapest thing the business can actually fix. ``log1p_review_count`` at
    −0.05 is a **planted null**: if a later analysis "finds" a review-count effect
    on RTO it is over-fitting, and GT-04 exists to catch that.
    """
    c = params.require(f"{RTO_BLOCK}.coefficients")
    centre = params.require("distributions.centering")
    n = len(ctx["z_trust"])
    a = LogitAssembler(block=RTO_BLOCK, n_rows=n, ledger=ledger)

    a.add_numeric("latent_intent", c["latent_intent"], ctx["z_intent"])
    a.add_numeric("latent_liquidity", c["latent_liquidity"], ctx["z_liquidity"])
    a.add_numeric("latent_trust", c["latent_trust"], ctx["z_trust"])
    a.add_categorical("geo_tier", c["geo_tier"], ctx["geo_tier"])
    a.add_numeric("serviceability_z", c["serviceability_z"], _z(ctx["serviceability"]))
    a.add_numeric("address_completeness", c["address_completeness"], ctx["address_completeness"])
    a.add_numeric("seller_sla_breach_rate", c["seller_sla_breach_rate"],
                  ctx["seller_sla_breach_rate"])
    a.add_numeric("seller_rating_centered", c["seller_rating_centered"],
                  ctx["seller_rating"] - float(centre["seller_rating_center"]))
    a.add_numeric("product_rating_centered", c["product_rating_centered"],
                  ctx["product_rating"] - float(centre["product_rating_center"]))
    a.add_numeric("log1p_review_count_centered", c["log1p_review_count_centered"],
                  _centre(np.log1p(ctx["review_count"])))
    a.add_numeric("log_order_value", c["log_order_value"], _log_value(ctx["order_value"], centre))
    a.add_numeric("discount_pct_centered", c["discount_pct_centered"],
                  _discount(ctx["discount_pct"], centre))
    a.add_numeric("cart_size_ge3", c["cart_size_ge3"], (ctx["cart_size"] >= 3).astype(float))
    a.add_numeric("est_delivery_days_centered", c["est_delivery_days_centered"],
                  ctx["estimated_delivery_days"] - float(centre["est_delivery_days_center"]))
    a.add_categorical("category", c["category"], ctx["category"])

    return _sum(a)


def record_dynamic(params, block: str, terms: tuple[str, ...],
                   ledger: CoefficientLedger) -> dict[str, float]:
    """Record the per-day coefficients once and return them for reuse in the loop."""
    coefficients = params.require(f"{block}.coefficients")
    return {t: ledger.record(block, t, coefficients[t]) for t in terms}


def cod_dynamic(
    coefficients: dict[str, float],
    pit_cod_share: np.ndarray,
    prepaid_success: np.ndarray,
    is_new: np.ndarray,
    orders_delivered: np.ndarray,
    payment_failure_rate: np.ndarray,
    cod_prior: float,
    payment_failure_prior: float,
) -> np.ndarray:
    """The COD terms that move as history accumulates.

    Decision A39: ``pit_cod_share`` is centred on the declared prior, so a
    historyless customer contributes a deviation of zero rather than sitting at
    the never-used-COD end of the scale.

    ``payment_failure_rate`` is deliberately left un-centred and is flagged for a
    ruling — it is the one remaining history rate where NULL still means "zero",
    though at +1.10 × 0.175 the effect is ~0.19 on the logit rather than 1.358.
    """
    return (
        coefficients["pit_cod_share"] * np.nan_to_num(pit_cod_share - cod_prior, nan=0.0)
        + coefficients["log1p_prepaid_success"] * np.log1p(prepaid_success)
        + coefficients["is_new_customer"] * is_new.astype(np.float64)
        + coefficients["log1p_orders_delivered"] * np.log1p(orders_delivered)
        + coefficients["payment_failure_rate"]
        * np.nan_to_num(payment_failure_rate - payment_failure_prior, nan=0.0)
    )


# ---------------------------------------------------------------------------


def _sum(assembler: LogitAssembler) -> np.ndarray:
    """Sum the terms without requiring an intercept — the intercept is the variable."""
    total = np.zeros(assembler.n_rows, dtype=np.float64)
    for contribution in assembler.components().values():
        total += contribution
    return total


def _log_value(order_value: np.ndarray, centre: dict) -> np.ndarray:
    return np.log(np.maximum(order_value, 1.0) / float(centre["order_value_scale"]))


def _discount(discount_pct: np.ndarray, centre: dict) -> np.ndarray:
    return (discount_pct - float(centre["discount_pct_center"])) / float(
        centre["discount_pct_unit"]
    )


def _z(values: np.ndarray) -> np.ndarray:
    sd = values.std(ddof=0)
    return (values - values.mean()) / sd if sd > 0 else np.zeros_like(values)


def _centre(values: np.ndarray) -> np.ndarray:
    return values - values.mean()
