"""Module 05 — ``dim_product``. 8,000 products, each belonging to a seller.

Business concept
----------------
``list_price`` is where the ₹1,000 headline is actually produced. Decision A5
settled that **₹1,000 is mean GMV per order, not mean order_value** — so the
category-mixture lognormal here is calibrated on GMV, and mean ``order_value``
emerges around ₹920 once the ~8% discount comes off.

Spec §12.1 explains why the **mean** is pinned rather than the median: every
Phase 1 figure — ₹416 per RTO, ₹165 Cr exposure, p* = 25.7% — is computed at a
₹1,000 order. Pinning the median instead would push the mean to ~₹1,450, inflate
mean RTO cost to ~₹560, and quietly turn the headline into ₹221 Cr. That is
exactly the silent assumption drift this project exists to expose.

The mixture is right-skewed by construction: Electronics at ₹2,900 mean against
Mobile Accessories at ₹550. That skew is why RTO cost varies so much across the
order population, and why §12.4 insists the *empirical* mean RTO cost be reported
next to the ₹416 exemplar rather than instead of it.

``review_count`` deserves a note: it carries a **planted null** (−0.05 in the RTO
logit, noise-level by design). If a later analysis "discovers" a review-count
effect on RTO, it is over-fitting, and GT-04 is there to catch it.

Spec references
---------------
- Spec §3.4  — the column list
- Spec §12.1 — category-mixture lognormal, mean pinned
- Spec §12.2 — shrink_rate by category
- Decision A5 — the GMV / order_value distinction

⚠️ ``distributions.product`` is [A27 PROPOSED] and not yet approved.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.random import Generator

from src.generators.sellers import beta_scaled

_SUB_CATEGORIES = {
    "FASHION": ["Womens_Ethnic", "Mens_Casual", "Footwear", "Kids_Wear", "Accessories"],
    "BEAUTY": ["Skincare", "Haircare", "Fragrance", "Makeup"],
    "HOME_KITCHEN": ["Cookware", "Storage", "Decor", "Bedding", "Small_Appliance"],
    "MOBILE_ACC": ["Cases", "Chargers", "Audio", "Screen_Guards"],
    "ELECTRONICS": ["Audio_Devices", "Wearables", "Cameras", "Peripherals"],
    "GROCERY_FMCG": ["Staples", "Snacks", "Beverages", "Household"],
}


def generate_products(params, sellers: pd.DataFrame, rng: Generator) -> pd.DataFrame:
    """Build the product dimension.

    Blocking dependency: **sellers**. A product cannot exist without one, and the
    seller's quality attributes are what make seller-level SLA analysis possible
    at ~83 orders per seller (spec §15).
    """
    n = int(params.require("scale.n_products"))
    category_weights = params.require("distributions.category_weights")
    category_mean_gmv = params.require("distributions.category_mean_gmv")
    category_sigma = params.require("distributions.category_gmv_sigma")
    truncation = params.require("distributions.gmv_truncation")
    shrink_by_category = params.require("economics.shrink_rate_by_category")
    product = params.require("distributions.product")
    rating_spec = params.require("distributions.product_rating")
    review_spec = params.require("distributions.review_count")
    cogs_mean = float(params.require("economics.cogs_ratio_mean"))
    cogs_sd = float(params.require("economics.cogs_ratio_sd"))
    cogs_bounds = params.require("economics.cogs_ratio_bounds")

    categories = _draw_categories(rng, category_weights, n)

    frame = pd.DataFrame({
        "product_id": [f"PRD_{i:06d}" for i in range(n)],
        "seller_id": sellers["seller_id"].to_numpy()[
            rng.integers(0, len(sellers), size=n)
        ],
        "category": categories,
    })
    frame["sub_category"] = [
        _SUB_CATEGORIES[c][rng.integers(0, len(_SUB_CATEGORIES[c]))] for c in categories
    ]

    # `category_mean_gmv` is the target mean **GMV per order** (decision A5), and
    # gmv = list_price x quantity (spec §3.10). So the list-price mean must be the
    # GMV target divided by E[quantity], or mean GMV overshoots by 12.5% and EC-01
    # fails by ~₹125 against a ±₹25 tolerance.
    #
    # This is implementing A5 correctly, not changing it. See decision A29 for the
    # related spec inconsistency: order_value is marked Stage-2 SAFE but depends on
    # quantity, which §3.10 marks Stage 3.
    expected_quantity = _expected_quantity(params)
    price_means = {c: float(v) / expected_quantity for c, v in category_mean_gmv.items()}

    frame["list_price"] = _category_lognormal_price(
        rng, categories, price_means, category_sigma, truncation
    ).round(2)

    frame["product_rating"] = beta_scaled(rng, rating_spec, n).round(2)
    frame["review_count"] = _lognormal_capped(rng, review_spec, n).astype(np.int64)

    frame["base_discount_pct"] = rng.beta(
        float(product["base_discount_pct"]["a"]),
        float(product["base_discount_pct"]["b"]),
        size=n,
    ).round(3)

    frame["cogs_ratio"] = np.clip(
        rng.normal(cogs_mean, cogs_sd, size=n),
        float(cogs_bounds["lo"]), float(cogs_bounds["hi"]),
    ).round(3)

    frame["weight_band"] = _weight_band(rng, categories, product["weight_band_by_category"])
    frame["shrink_rate"] = _map(categories, shrink_by_category).astype(np.float64).round(3)
    frame["is_returnable"] = _returnable(rng, categories, product["is_returnable_by_category"])

    return frame


def _expected_quantity(params) -> float:
    """E[quantity] from ``distributions.session.quantity_weights``."""
    weights = params.require("distributions.session.quantity_weights")
    total = sum(float(w) for w in weights.values())
    if not np.isclose(total, 1.0):
        raise ValueError(f"quantity_weights sum to {total}, expected 1.0")
    return float(sum(int(q) * float(w) for q, w in weights.items()))


def _draw_categories(rng: Generator, weights: dict[str, float], n: int) -> np.ndarray:
    names = list(weights)
    probs = np.array([float(weights[k]) for k in names], dtype=np.float64)
    if not np.isclose(probs.sum(), 1.0):
        raise ValueError(f"category_weights sum to {probs.sum()}, expected 1.0")
    return np.array(names)[rng.choice(len(names), size=n, p=probs)]


def _category_lognormal_price(
    rng: Generator,
    categories: np.ndarray,
    means: dict[str, float],
    sigmas: dict[str, float],
    truncation: dict,
) -> np.ndarray:
    """Lognormal per category, parameterised so the **mean** is the stated value.

    For a lognormal, ``mean = exp(mu + sigma^2 / 2)``. Setting ``mu = log(mean)``
    would pin the *median* and overshoot the mean by ``exp(sigma^2/2)`` — at
    Electronics' sigma of 0.75 that is a 32% overshoot, which would blow EC-01
    on its own. So mu is solved backwards from the target mean.

    Truncation then pulls the realised mean slightly below target. That residual
    is left for the calibration report to show rather than corrected here: the
    order-value mean is an *emergent* property of the category mix, and EC-01 is
    a genuine test of it (spec §12.1).
    """
    lo, hi = float(truncation["lo"]), float(truncation["hi"])
    prices = np.empty(len(categories), dtype=np.float64)

    for category in np.unique(categories):
        mask = categories == category
        sigma = float(sigmas[category])
        target_mean = float(means[category])
        mu = np.log(target_mean) - 0.5 * sigma**2
        prices[mask] = rng.lognormal(mu, sigma, size=int(mask.sum()))

    return np.clip(prices, lo, hi)


def _lognormal_capped(rng: Generator, spec: dict, n: int) -> np.ndarray:
    values = rng.lognormal(float(spec["mu"]), float(spec["sigma"]), size=n)
    return np.minimum(values, float(spec["max"]))


def _map(categories: np.ndarray, mapping: dict) -> np.ndarray:
    missing = set(np.unique(categories).tolist()) - set(mapping)
    if missing:
        raise ValueError(f"No value for category/categories {sorted(missing)}.")
    return np.array([mapping[c] for c in categories])


def _weight_band(
    rng: Generator, categories: np.ndarray, by_category: dict
) -> np.ndarray:
    """Weight band drawn per category — it drives freight and packaging cost.

    Category-conditional because a Mobile Accessory is never HEAVY and a Home &
    Kitchen item often is, and freight is a real line in the CM waterfall.
    """
    bands = np.empty(len(categories), dtype=object)
    for category in np.unique(categories):
        mask = categories == category
        weights = by_category[category]
        names = list(weights)
        probs = np.array([float(weights[k]) for k in names], dtype=np.float64)
        if not np.isclose(probs.sum(), 1.0):
            raise ValueError(
                f"weight_band_by_category[{category}] sums to {probs.sum()}, expected 1.0"
            )
        bands[mask] = np.array(names)[rng.choice(len(names), size=int(mask.sum()), p=probs)]
    return bands


def _returnable(rng: Generator, categories: np.ndarray, by_category: dict) -> np.ndarray:
    rates = _map(categories, by_category).astype(np.float64)
    return rng.random(len(categories)) < rates
