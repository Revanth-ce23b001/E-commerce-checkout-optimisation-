"""Module 08 — ``fct_checkout_session``. The Stage-1/2 columns.

Business concept
----------------
**Sessions are generated separately from orders, and that is the whole point.**
The north-star metric is contribution margin per checkout session *started*, so
sessions that never convert are part of the denominator and must exist as rows.
A generator that produced only orders could never measure conversion, could never
diagnose where checkout sheds traffic, and would silently turn every intervention
into a pure-RTO trade with no conversion cost — which is exactly the mistake the
project exists to avoid making.

Everything written here is knowable **before the customer picks a payment
method** — Stage 1 and Stage 2 in the availability timeline. That is the decision
moment the risk model has to work at. The funnel columns
(``address_completed``, ``abandon_step``, ``final_payment_method`` …) are left
NULL/False here and filled by modules 11a–12.

One column deserves a note. ``address_completeness_score`` is the **cheapest
intervention** in the whole opportunity model and the second-strongest driver in
the RTO logit (−1.40). It is drawn independently of tier and of every latent,
because spec §3.6 names no drivers for it — and because if address quality were a
geography proxy, "fix the addresses" would silently become a geography policy
with a fairness problem attached.

Spec references
---------------
- Spec §3.6  — the column list
- Spec §4.2  — what counts as Stage-1 / Stage-2 SAFE
- Brief §9.6 — sessions separate from orders; demand-index volume shaping

⚠️ ``distributions.session`` is [A28 PROPOSED] and not yet approved.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from numpy.random import Generator

SECONDS_PER_HOUR = 3600


def generate_sessions(
    params,
    dates: pd.DataFrame,
    customers: pd.DataFrame,
    products: pd.DataFrame,
    geography: pd.DataFrame,
    latents: pd.DataFrame,
    rng: Generator,
) -> pd.DataFrame:
    """Build the session table, sorted chronologically.

    Sorting matters: module 09 makes a strict chronological pass over these rows,
    and ``session_id`` is assigned **after** the sort so that id order and time
    order agree. That turns "is this snapshot using future information?" into a
    question anyone can eyeball, rather than one only LK-04 can answer.
    """
    session_cfg = params.require("distributions.session")
    n_sessions = _session_count(params)

    # --- volume by day, shaped by the demand index --------------------------
    per_day = _allocate_to_days(dates, n_sessions)
    date_of_session = np.repeat(dates["date_id"].to_numpy(), per_day)

    # --- who, and when within the day ---------------------------------------
    customer_idx = rng.integers(0, len(customers), size=n_sessions)
    session_ts = _timestamps(rng, date_of_session, session_cfg["hour_of_day_weights"])

    order = np.argsort(session_ts, kind="stable")
    customer_idx = customer_idx[order]
    session_ts = session_ts[order]
    date_of_session = date_of_session[order]

    frame = pd.DataFrame({
        "session_id": [f"SES_{i:08d}" for i in range(n_sessions)],
        "customer_id": customers["customer_id"].to_numpy()[customer_idx],
        "session_start_ts": session_ts,
        "date_id": date_of_session,
    })

    frame["device_type"] = _categorical(
        rng, params.require("distributions.device_type_weights"), n_sessions
    )

    # --- the cart ------------------------------------------------------------
    product_idx = rng.integers(0, len(products), size=n_sessions)
    frame["candidate_product_id"] = products["product_id"].to_numpy()[product_idx]
    list_price = products["list_price"].to_numpy()[product_idx]

    frame["cart_size"] = _categorical_int(
        rng, params.require("distributions.cart_size_weights"), n_sessions
    )
    # cart_size is the OPTIONALITY proxy — how many things they were weighing up.
    # quantity is units of the one product actually ordered (single-line orders,
    # spec §1.5). They are different quantities and are used differently.
    frame["cart_value"] = (list_price * frame["cart_size"]).round(2)
    quantity = _categorical_int(rng, session_cfg["quantity_weights"], n_sessions)

    # --- geography and the delivery promise ----------------------------------
    geo_idx = _delivery_geography(
        rng, customers, geography, customer_idx, float(session_cfg["away_geography_rate"])
    )
    frame["delivery_geography_id"] = geography["geography_id"].to_numpy()[geo_idx]

    base_days = geography["base_delivery_days"].to_numpy()[geo_idx]
    promise = base_days + rng.normal(
        0.0, float(session_cfg["est_delivery_days_noise_sd"]), size=n_sessions
    )
    frame["estimated_delivery_days"] = np.clip(np.round(promise), 1, 21).astype(np.int16)

    frame["address_completeness_score"] = _beta(
        rng, session_cfg["address_completeness"], n_sessions
    ).round(3)

    # --- prospective order economics, knowable at Stage 2 --------------------
    # Deal-seeking selection: price-sensitive customers end up on deeper
    # discounts (spec §12.2). The effect is small — this is selection into
    # promotions, not a pricing model.
    price_sens = latents["latent_price_sensitivity"].to_numpy()[customer_idx]
    base_discount = products["base_discount_pct"].to_numpy()[product_idx]
    discount = (
        base_discount
        + float(session_cfg["discount_price_sensitivity_weight"]) * price_sens
        + rng.normal(0.0, float(session_cfg["discount_noise_sd"]), size=n_sessions)
    )
    bounds = session_cfg["discount_bounds"]
    frame["discount_pct"] = np.clip(discount, float(bounds["lo"]), float(bounds["hi"])).round(4)

    frame["quantity"] = quantity.astype(np.int16)
    frame["prospective_gmv"] = (list_price * quantity).round(2)
    frame["order_value"] = (
        frame["prospective_gmv"] * (1.0 - frame["discount_pct"])
    ).round(2)

    # --- funnel columns, filled by modules 11a-12 ---------------------------
    frame["checkout_started"] = True
    frame["address_completed"] = False
    frame["payment_page_reached"] = False
    frame["intended_payment_method"] = pd.Series([None] * n_sessions, dtype=object)
    frame["final_payment_method"] = pd.Series([None] * n_sessions, dtype=object)
    frame["switched_to_cod_after_failure"] = False
    frame["payment_attempt_count"] = np.int16(0)
    frame["checkout_abandoned"] = True
    frame["abandon_step"] = pd.Series([None] * n_sessions, dtype=object)
    frame["order_id"] = pd.Series([None] * n_sessions, dtype=object)

    return frame


def _session_count(params) -> int:
    """``target_orders / checkout_conversion_target`` — the north-star denominator."""
    target_orders = int(params.require("scale.target_orders"))
    conversion = float(params.require("scale.checkout_conversion_target"))
    return int(round(target_orders / conversion))


def _allocate_to_days(dates: pd.DataFrame, n_sessions: int) -> np.ndarray:
    """Split sessions across days in proportion to ``demand_index``.

    Deterministic proportional allocation rather than a multinomial draw: the
    demand shape is a structural property of the calendar, and drawing it would
    add noise that changing any unrelated parameter would reshuffle.
    """
    weights = dates["demand_index"].to_numpy(dtype=np.float64)
    share = weights / weights.sum()
    counts = np.floor(share * n_sessions).astype(np.int64)

    remainder = n_sessions - int(counts.sum())
    if remainder:
        order = np.argsort(-(share * n_sessions - counts))
        for i in range(remainder):
            counts[order[i % len(counts)]] += 1
    return counts


def _timestamps(
    rng: Generator, date_of_session: np.ndarray, hour_weights: list
) -> np.ndarray:
    """Place each session within its day, evening-weighted."""
    probs = np.array([float(w) for w in hour_weights], dtype=np.float64)
    if len(probs) != 24:
        raise ValueError(f"hour_of_day_weights must have 24 entries, got {len(probs)}.")
    probs = probs / probs.sum()

    n = len(date_of_session)
    hours = rng.choice(24, size=n, p=probs)
    seconds_within_hour = rng.integers(0, SECONDS_PER_HOUR, size=n)

    midnight = np.array(
        [dt.datetime(d.year, d.month, d.day) for d in date_of_session],
        dtype="datetime64[s]",
    )
    offset = (hours.astype(np.int64) * SECONDS_PER_HOUR
              + seconds_within_hour.astype(np.int64))
    return midnight + offset.astype("timedelta64[s]")


def _delivery_geography(
    rng: Generator,
    customers: pd.DataFrame,
    geography: pd.DataFrame,
    customer_idx: np.ndarray,
    away_rate: float,
) -> np.ndarray:
    """Home cluster, or occasionally somewhere else (gifts, travel, work address)."""
    geo_position = {gid: i for i, gid in enumerate(geography["geography_id"])}
    home_idx = customers["home_geography_id"].map(geo_position).to_numpy()[customer_idx]

    away = rng.random(len(customer_idx)) < away_rate
    if away.any():
        home_idx = home_idx.copy()
        home_idx[away] = rng.integers(0, len(geography), size=int(away.sum()))
    return home_idx


def _categorical(rng: Generator, weights: dict, n: int) -> np.ndarray:
    names = [str(k) for k in weights]
    probs = np.array([float(weights[k]) for k in weights], dtype=np.float64)
    if not np.isclose(probs.sum(), 1.0):
        raise ValueError(f"Weights sum to {probs.sum()}, expected 1.0: {weights}")
    return np.array(names)[rng.choice(len(names), size=n, p=probs)]


def _categorical_int(rng: Generator, weights: dict, n: int) -> np.ndarray:
    values = np.array([int(k) for k in weights], dtype=np.int64)
    probs = np.array([float(v) for v in weights.values()], dtype=np.float64)
    if not np.isclose(probs.sum(), 1.0):
        raise ValueError(f"Weights sum to {probs.sum()}, expected 1.0: {weights}")
    return values[rng.choice(len(values), size=n, p=probs)]


def _beta(rng: Generator, spec: dict, n: int) -> np.ndarray:
    return rng.beta(float(spec["a"]), float(spec["b"]), size=n)
