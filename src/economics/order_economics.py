"""Module 19 — ``fct_order_economics``. Every cost line, separated.

Business concept
----------------
**Costs are outcome-conditional, and that asymmetry is the whole opportunity
model.** A delivered order and an RTO'd order of identical value have completely
different cost structures: the RTO earns nothing, pays freight twice, pays to
re-inward and re-shelve the goods, writes off part of their value, and ties up
working capital while the parcel travels back. Collapsing that into a single
"cost per order" would erase the very thing the project exists to measure.

The line that most people get wrong is ``cogs``. On an RTO, ``net_revenue`` is
zero, so ``cogs`` is zero — the goods came back, they were not sold. But shrink
and working capital are still proportional to the *value of the goods that
moved*. That is what ``cogs_value`` is for (decision A23): the counterfactual
COGS, computed as if the order had been delivered.

``counterfactual_cm_if_delivered`` is stored rather than re-derived, so the
blueprint §7 avoidability waterfall is reproducible straight from the table.

Do not tune to hit ₹416
-----------------------
Spec §12.4 is explicit: the ₹1,000 exemplar is a reconciliation target, and the
*empirical* mean across the actual right-skewed order distribution will differ.
Both are reported. The difference is informative — RTO concentrates in
lower-value categories, so the empirical mean sits below the exemplar.

Spec references
---------------
- Spec §3.12  — the column list
- Spec §12.2  — every cost parameter
- Spec §12.3  — the incurrence matrix, implemented exactly
- Spec §12.4  — the mandatory reconciliation
- Decision A23 — cogs_value, and zero-everything on pre-ship cancellations
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.random import Generator


def generate_economics(
    params,
    orders: pd.DataFrame,
    products: pd.DataFrame,
    geography: pd.DataFrame,
    rng: Generator,
) -> pd.DataFrame:
    """One row per order, with every cost line separated."""
    cfg = params.require("economics")
    n = len(orders)

    product = products.set_index("product_id").reindex(orders["product_id"])
    geo = geography.set_index("geography_id").reindex(orders["delivery_geography_id"])

    gmv = orders["gmv"].to_numpy(float)
    order_value = orders["order_value"].to_numpy(float)
    discount = gmv - order_value

    is_cod = (orders["payment_method"] == "COD").to_numpy()
    shipped = orders["is_shipped"].to_numpy(bool)
    cancelled = orders["is_cancelled_preship"].to_numpy(bool)
    rto = _as_bool(orders["rto_flag"])
    delivered = _as_bool(orders["is_delivered"])
    attempts = np.nan_to_num(orders["delivery_attempts"].to_numpy(float), nan=1.0)

    shipping_fee = float(cfg["shipping_fee_charged"])
    cod_fee = float(cfg["cod_fee_charged"])

    # Counterfactual revenue: what this order WOULD have earned if delivered.
    # Used for cogs_value and for the foregone-CM leg of the RTO cost.
    net_revenue_if_delivered = order_value + shipping_fee + np.where(is_cod, cod_fee, 0.0)
    cogs_ratio = product["cogs_ratio"].to_numpy(float)
    cogs_value = cogs_ratio * net_revenue_if_delivered

    net_revenue = np.where(delivered, net_revenue_if_delivered, 0.0)
    cogs = np.where(delivered, cogs_ratio * net_revenue, 0.0)

    # --- dispatch costs: incurred on EVERY shipped order --------------------
    weight_factor = _map(
        product["weight_band"].to_numpy(), cfg["forward_freight_weight_factor"]
    )
    forward = (
        geo["forward_freight_base"].to_numpy(float) * weight_factor
        + rng.normal(0.0, float(cfg["forward_freight_noise_sd"]), n)
    )
    packaging = (
        _map(product["weight_band"].to_numpy(), cfg["packaging"])
        + rng.normal(0.0, float(cfg["packaging_noise_sd"]), n)
    )
    forward = np.maximum(forward, 0.0) * shipped
    packaging = np.maximum(packaging, 0.0) * shipped

    # --- payment costs -------------------------------------------------------
    rail = orders["payment_rail"].to_numpy()
    pg_rate = np.array([
        float(cfg["pg_fee_rate_by_rail"].get(r, cfg["pg_fee_rate"]))
        if isinstance(r, str) else 0.0
        for r in rail
    ])
    payment_fee = pg_rate * order_value * ~is_cod
    cod_handling = (
        float(cfg["cod_handling_rate"]) * order_value + float(cfg["cod_handling_fixed"])
    ) * (is_cod & delivered)

    # --- RTO-only costs ------------------------------------------------------
    reverse_shipping = (
        float(cfg["reverse_freight_multiplier"]) * forward
        + rng.normal(0.0, float(cfg["reverse_freight_noise_sd"]), n)
    )
    reverse_shipping = np.maximum(reverse_shipping, 0.0) * rto
    reverse_handling = np.maximum(
        float(cfg["reverse_handling_base"]) * weight_factor
        + rng.normal(0.0, float(cfg["reverse_handling_noise_sd"]), n), 0.0
    ) * rto
    shrink = product["shrink_rate"].to_numpy(float) * cogs_value * rto
    cod_failed_attempt = float(cfg["cod_failed_attempt_fee"]) * (rto & is_cod)

    # Working capital: the goods' value is tied up while the parcel travels back.
    wc_cfg = cfg["wc_days_blocked"]
    days_blocked = np.exp(
        float(wc_cfg["mu"]) + float(wc_cfg["sigma"]) * rng.normal(0.0, 1.0, n)
    )
    working_capital = (
        cogs_value * float(cfg["wc_annual_rate"]) * days_blocked / 365.0
    ) * rto

    # --- asymmetric, and delivered-only -------------------------------------
    support = np.where(
        rto,
        float(cfg["support_ndr_rto"])
        + float(cfg["support_ndr_per_extra_attempt"]) * np.maximum(attempts - 1, 0),
        float(cfg["support_delivered"]) * delivered,
    )
    ops = float(cfg["ops_allocation_delivered"]) * delivered

    total_variable = (
        forward + packaging + payment_fee + cod_handling + reverse_shipping
        + reverse_handling + shrink + cod_failed_attempt + working_capital
        + support + ops
    )
    contribution_margin = net_revenue - cogs - total_variable

    counterfactual = _counterfactual_cm(
        net_revenue_if_delivered, cogs_ratio, forward, packaging, payment_fee,
        order_value, is_cod, cfg,
    )

    frame = pd.DataFrame({
        "order_id": orders["order_id"].to_numpy(),
        "gmv": gmv,
        "discount_cost": discount,
        "shipping_fee_revenue": np.where(delivered, shipping_fee, 0.0),
        "cod_fee_revenue": np.where(delivered & is_cod, cod_fee, 0.0),
        "net_revenue": net_revenue,
        "cogs": cogs,
        "cogs_value": cogs_value,
        "forward_shipping_cost": forward,
        "reverse_shipping_cost": reverse_shipping,
        "packaging_cost": packaging,
        "payment_processing_fee": payment_fee,
        "cod_handling_cost": cod_handling,
        "cod_failed_attempt_cost": cod_failed_attempt,
        "reverse_handling_cost": reverse_handling,
        "shrink_cost": shrink,
        "support_ndr_cost": support,
        "working_capital_cost": working_capital,
        "ops_allocation_cost": ops,
        "total_variable_cost": total_variable,
        "contribution_margin": contribution_margin,
        "counterfactual_cm_if_delivered": counterfactual,
        "rto_cash_loss": np.where(rto, -contribution_margin, 0.0),
        "foregone_cm": np.where(rto, counterfactual, 0.0),
    })
    frame["rto_economic_cost"] = frame["rto_cash_loss"] + frame["foregone_cm"]

    # Decision A23: a pre-ship cancellation dispatched nothing, collected nothing,
    # and refunds any prepayment. Every line is zero, not a small number.
    money = [c for c in frame.columns if c != "order_id"]
    frame.loc[cancelled, money] = 0.0

    return frame.round(2)


def _counterfactual_cm(
    net_revenue_if_delivered, cogs_ratio, forward, packaging, payment_fee,
    order_value, is_cod, cfg,
):
    """CM this order would have earned had it been delivered.

    Stored so the blueprint §7 waterfall is reproducible from the table with no
    re-derivation — and so ``foregone_cm`` is a measured counterfactual rather
    than an average applied after the fact.
    """
    cod_handling = (
        float(cfg["cod_handling_rate"]) * order_value + float(cfg["cod_handling_fixed"])
    ) * is_cod
    variable = (
        forward + packaging + payment_fee + cod_handling
        + float(cfg["support_delivered"]) + float(cfg["ops_allocation_delivered"])
    )
    return net_revenue_if_delivered - cogs_ratio * net_revenue_if_delivered - variable


def reconcile_exemplar(params, gmv: float = 1000.0) -> dict[str, float]:
    """The spec §12.4 reconciliation at a single ₹1,000 **GMV** order.

    Decision A34: ₹1,000 is GMV. Phase 1 §6.5 is unambiguous —
    GMV ₹1,000 − 8% discount = net revenue ₹920 — and 24M × ₹1,000 = ₹2,400 Cr is
    the figure the whole ₹165 Cr model rests on.

    Computed analytically at blended cost parameters, so EC-03…EC-06 test the cost
    *structure* rather than a sample of it.
    """
    cfg = params.require("economics")
    order_value = gmv * (1.0 - float(cfg["platform_discount_pct"]))
    net_revenue = order_value
    cogs = float(cfg["cogs_ratio_mean"]) * net_revenue

    forward = _blended_forward(params)
    packaging = _blended(cfg["packaging"], params.require("distributions.weight_band_weights"))
    support_delivered = float(cfg["support_delivered"])
    ops = float(cfg["ops_allocation_delivered"])

    prepaid_cm = net_revenue - cogs - (
        forward + packaging + float(cfg["pg_fee_rate"]) * order_value
        + support_delivered + ops
    )
    cod_handling = float(cfg["cod_handling_rate"]) * order_value + float(cfg["cod_handling_fixed"])
    cod_cm = net_revenue - cogs - (forward + packaging + cod_handling + support_delivered + ops)

    cogs_value = cogs
    shrink = _blended(
        cfg["shrink_rate_by_category"], params.require("distributions.category_weights")
    ) * cogs_value
    wc_cfg = cfg["wc_days_blocked"]
    days = np.exp(float(wc_cfg["mu"]) + float(wc_cfg["sigma"]) ** 2 / 2)
    working_capital = cogs_value * float(cfg["wc_annual_rate"]) * days / 365.0
    attempts = int(params.require("fulfilment.max_delivery_attempts"))

    rto_cash = (
        forward + packaging
        + float(cfg["reverse_freight_multiplier"]) * forward
        + float(cfg["reverse_handling_base"])
        + shrink + working_capital + float(cfg["cod_failed_attempt_fee"])
        + float(cfg["support_ndr_rto"])
        + float(cfg["support_ndr_per_extra_attempt"]) * (attempts - 1)
    )
    return {
        "prepaid_delivered_cm": prepaid_cm,
        "cod_delivered_cm": cod_cm,
        "cod_rto_cash_loss": -rto_cash,
        "cod_rto_economic_cost": -(rto_cash + cod_cm),
        # EV of a COD order is zero at p* -- so the threshold is set by the CASH
        # loss, which is what blueprint 6.6's 25.7% is derived from.
        "breakeven_rto_prob": cod_cm / (cod_cm + rto_cash),
    }


def _blended_forward(params) -> float:
    cfg = params.require("economics")
    tier = _blended(cfg["forward_freight_base"], params.require("distributions.geo_tier_weights"))
    weight = _blended(
        cfg["forward_freight_weight_factor"], params.require("distributions.weight_band_weights")
    )
    return tier * weight


def _blended(values: dict, weights: dict) -> float:
    return float(sum(float(values[k]) * float(weights[k]) for k in weights))


def _map(keys: np.ndarray, mapping: dict) -> np.ndarray:
    missing = set(np.unique(keys).tolist()) - set(mapping)
    if missing:
        raise ValueError(f"No economics value for {sorted(missing)}.")
    return np.array([float(mapping[k]) for k in keys])


def _as_bool(series: pd.Series) -> np.ndarray:
    """Outcome columns are NULL on censored and cancelled orders (decision A10)."""
    return series.fillna(False).to_numpy(bool)
