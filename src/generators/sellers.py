"""Module 04 — ``dim_seller``. 1,200 sellers.

Business concept
----------------
Two seller attributes carry planted relationships, and they are deliberately
*correlated with each other but not identical*:

* ``seller_rating`` — a **trust proxy**. Enters the COD logit at −0.35 (worse
  rating, more COD) and the RTO logit at −0.28. H5.
* ``seller_sla_breach_rate`` — **real fulfilment quality**. Enters the RTO logit
  at +1.20 and does *not* enter the COD logit at all. A customer cannot see it at
  checkout; it acts on them later, through late dispatch and decayed intent.

Keeping them separate is what lets the analysis distinguish "customers avoid
prepaying with sellers they distrust" from "bad sellers ship late and the parcel
comes back". A single seller-quality score would collapse both stories into one.

``seller_tier`` is **derived** from those two, never drawn. It exists as a
segmentation lens for dashboards; deriving it means it can never disagree with
the attributes it summarises.

Spec references
---------------
- Spec §3.3  — the column list
- Spec §7.2  — seller_rating_centered (−0.35), centred at 4.30
- Spec §8.2  — seller_rating_centered (−0.28), seller_sla_breach_rate (+1.20)

⚠️ ``distributions.seller`` is [A27 PROPOSED] and not yet approved.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.random import Generator

_NAME_STEMS = [
    "Vardhman", "Shreeji", "Nova", "Anantha", "Kiran", "Mehta", "Bansal", "Trilok",
    "Sagar", "Deepak", "Orbit", "Prakash", "Vaibhav", "Chandra", "Zenith", "Ambar",
]
_NAME_SUFFIX = ["Retail", "Traders", "Enterprises", "Exports", "Mart", "Distributors"]


def generate_sellers(params, rng: Generator) -> pd.DataFrame:
    """Build the seller dimension."""
    n = int(params.require("scale.n_sellers"))
    seller = params.require("distributions.seller")
    rating_spec = params.require("distributions.seller_rating")

    frame = pd.DataFrame({"seller_id": [f"SLR_{i:06d}" for i in range(n)]})
    frame["seller_name"] = [
        f"{_NAME_STEMS[i % len(_NAME_STEMS)]} {_NAME_SUFFIX[(i // 7) % len(_NAME_SUFFIX)]}"
        for i in rng.permutation(n)
    ]

    frame["seller_tenure_days"] = _lognormal(rng, seller["tenure_days"], n).astype(np.int64)
    frame["seller_rating"] = beta_scaled(rng, rating_spec, n).round(2)
    frame["seller_rating_count"] = _lognormal(rng, seller["rating_count"], n).astype(np.int64)

    # Fulfilment quality. Independent of rating by construction: a well-rated
    # seller can still ship late, and the risk model has to learn that from the
    # SLA column rather than inferring it from the rating.
    frame["seller_sla_breach_rate"] = _beta(rng, seller["sla_breach_rate"], n).round(3)
    frame["seller_cancellation_rate"] = _beta(rng, seller["cancellation_rate"], n).round(3)

    frame["fulfilment_model"] = _categorical(
        rng, seller["fulfilment_model_weights"], n
    )
    frame["seller_tier"] = _derive_tier(frame, seller["tier_thresholds"])

    return frame


def beta_scaled(rng: Generator, spec: dict, n: int) -> np.ndarray:
    """A Beta draw rescaled onto ``[lo, hi]`` — used for both rating columns.

    Ratings are bounded and left-skewed (most sellers sit near 4.3, a tail runs
    down), which a Beta captures and a truncated normal does not.
    """
    raw = rng.beta(float(spec["a"]), float(spec["b"]), size=n)
    lo, hi = float(spec["lo"]), float(spec["hi"])
    return lo + raw * (hi - lo)


def _beta(rng: Generator, spec: dict, n: int) -> np.ndarray:
    return rng.beta(float(spec["a"]), float(spec["b"]), size=n)


def _lognormal(rng: Generator, spec: dict, n: int) -> np.ndarray:
    values = rng.lognormal(float(spec["mu"]), float(spec["sigma"]), size=n)
    if "max" in spec:
        values = np.minimum(values, float(spec["max"]))
    return np.maximum(values, 0.0)


def _categorical(rng: Generator, weights: dict[str, float], n: int) -> np.ndarray:
    names = list(weights)
    probs = np.array([float(weights[k]) for k in names], dtype=np.float64)
    if not np.isclose(probs.sum(), 1.0):
        raise ValueError(f"Weights sum to {probs.sum()}, expected 1.0: {weights}")
    return np.array(names)[rng.choice(len(names), size=n, p=probs)]


def _derive_tier(frame: pd.DataFrame, thresholds: dict) -> np.ndarray:
    """GOLD / SILVER / BRONZE from rating and SLA breach. Derived, never drawn.

    A seller must clear **both** bars for a tier: a 4.8-rated seller who breaches
    SLA 20% of the time is not GOLD. Requiring both is what stops the tier from
    becoming a rating alias.
    """
    rating = frame["seller_rating"].to_numpy()
    breach = frame["seller_sla_breach_rate"].to_numpy()

    tier = np.full(len(frame), "BRONZE", dtype=object)
    silver = thresholds["SILVER"]
    gold = thresholds["GOLD"]

    is_silver = (rating >= float(silver["min_rating"])) & (
        breach <= float(silver["max_sla_breach"])
    )
    is_gold = (rating >= float(gold["min_rating"])) & (
        breach <= float(gold["max_sla_breach"])
    )
    tier[is_silver] = "SILVER"
    tier[is_gold] = "GOLD"
    return tier
