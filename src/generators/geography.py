"""Module 03 — ``dim_geography``. 500 pincode clusters across four tiers.

Business concept
----------------
Geography is the most fairness-sensitive dimension in the project, and the table
is built to keep it honest.

``geo_tier`` drives both COD choice (+0.55 for Tier-3) and RTO (+0.45), which
means any risk model will learn to charge Tier-3 customers more. Blueprint §8.4
requires that outcome to be auditable rather than accidental, so tier's influence
runs through **two separate, explicitly-modelled channels**:

* ``serviceability_score`` — can the courier actually find the address? Real
  operational capability, and it falls with tier.
* ``cod_cultural_index`` — is paying cash normal here? A *norms* channel that is
  deliberately **not** tier-ordered the same way. Tier-1 scores highest, Metro
  lowest.

That second channel exists so geography can drive COD **without** being a pure
trust proxy. If the only path from geography to COD ran through distrust, the
analysis would trivially conclude "Tier-3 = low trust" — precisely the lazy
conclusion blueprint §1.5 warns against. Because the two channels disagree about
Metro-vs-Tier-1, an analyst has to work to separate access from norms, and may
not fully succeed. That difficulty is realistic and intentional.

Spec references
---------------
- Spec §3.2  — the column list and the cod_cultural_index note
- Spec §8.2  — serviceability_z (−0.25), geo_tier (−0.35 … +0.45)
- Spec §12.2 — forward_freight_base by tier

⚠️ ``distributions.geography`` is [A27 PROPOSED] and not yet approved.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.random import Generator

# Synthetic label pools. Cosmetic only — no business meaning, never a feature.
_CITY_STEMS = [
    "Nashik", "Indore", "Surat", "Kochi", "Guwahati", "Raipur", "Jodhpur", "Vellore",
    "Bhilai", "Ranchi", "Dhanbad", "Ajmer", "Kollam", "Warangal", "Bilaspur", "Karnal",
    "Erode", "Bathinda", "Latur", "Rewa", "Hosur", "Nadiad", "Palakkad", "Sambalpur",
]
_STATE_STEMS = [
    "Maharashtra", "Madhya Pradesh", "Gujarat", "Kerala", "Assam", "Chhattisgarh",
    "Rajasthan", "Tamil Nadu", "Jharkhand", "Punjab", "Odisha", "Telangana",
    "Karnataka", "Haryana", "Uttar Pradesh", "West Bengal",
]
_COURIERS = ["Bluewing", "Raptor Logistics", "Nandi Express", "Trident Ship", "Kestrel"]


def generate_geography(params, rng: Generator) -> pd.DataFrame:
    """Build the geography dimension.

    Cluster count per tier follows ``distributions.geo_tier_weights``, which is
    also the population weighting customers are drawn against. Spec §15 requires
    ≥100 clusters per tier so tier-level estimates are stable enough for a
    credible fairness audit — that is checked here rather than hoped for.
    """
    n = int(params.require("scale.n_geographies"))
    tier_weights = params.require("distributions.geo_tier_weights")
    geo = params.require("distributions.geography")
    freight_base = params.require("economics.forward_freight_base")

    tiers = _allocate_tiers(n, tier_weights)

    frame = pd.DataFrame({
        "geography_id": [f"GEO_{i:04d}" for i in range(n)],
        "geo_tier": tiers,
    })

    frame["pincode_prefix"] = rng.integers(110, 860, size=n).astype(str)
    frame["city_name"] = [
        f"{_CITY_STEMS[i % len(_CITY_STEMS)]}-{i // len(_CITY_STEMS) + 1}"
        for i in rng.permutation(n)
    ]
    frame["state_name"] = [_STATE_STEMS[i % len(_STATE_STEMS)] for i in rng.permutation(n)]

    frame["serviceability_score"] = _tiered_score(tiers, geo["serviceability"], rng)
    frame["courier_reliability_score"] = _tiered_score(tiers, geo["courier_reliability"], rng)
    # The norms channel. Note the tier ordering is NOT monotone — that is the point.
    frame["cod_cultural_index"] = _tiered_score(tiers, geo["cod_cultural_index"], rng)

    frame["base_delivery_days"] = _tiered_value(
        tiers, geo["base_delivery_days"], rng, lo=1.0, hi=12.0
    ).round(1)
    frame["forward_freight_base"] = (
        _map_tier(tiers, freight_base).astype(np.float64).round(2)
    )

    _assert_fairness_audit_power(frame)
    return frame


def _allocate_tiers(n: int, weights: dict[str, float]) -> np.ndarray:
    """Deterministic proportional allocation, not a multinomial draw.

    The tier mix is a *structural* choice about the marketplace, not a random
    outcome. Drawing it would make the sensitivity analysis noisier for no gain:
    changing n_geographies would reshuffle which tier each cluster belongs to.
    """
    names = list(weights)
    raw = np.array([float(weights[k]) for k in names], dtype=np.float64)
    if not np.isclose(raw.sum(), 1.0):
        raise ValueError(f"geo_tier_weights sum to {raw.sum()}, expected 1.0")

    counts = np.floor(raw * n).astype(int)
    # Hand out the rounding remainder to the largest fractional parts.
    remainder = n - counts.sum()
    if remainder:
        order = np.argsort(-(raw * n - counts))
        for i in range(remainder):
            counts[order[i % len(names)]] += 1

    return np.repeat(names, counts)


def _map_tier(tiers: np.ndarray, mapping: dict) -> np.ndarray:
    missing = set(np.unique(tiers).tolist()) - set(mapping)
    if missing:
        raise ValueError(f"No value for geo tier(s) {sorted(missing)} in {sorted(mapping)}.")
    return np.array([mapping[t] for t in tiers])


def _tiered_score(tiers: np.ndarray, spec: dict, rng: Generator) -> np.ndarray:
    """A 0–1 score whose mean depends on tier. Truncated, then rounded to 3dp."""
    means = {k: v for k, v in spec.items() if k != "sd"}
    values = _map_tier(tiers, means).astype(np.float64)
    values = values + rng.normal(0.0, float(spec["sd"]), size=len(tiers))
    return np.clip(values, 0.01, 0.999).round(3)


def _tiered_value(
    tiers: np.ndarray, spec: dict, rng: Generator, *, lo: float, hi: float
) -> np.ndarray:
    means = {k: v for k, v in spec.items() if k != "sd"}
    values = _map_tier(tiers, means).astype(np.float64)
    values = values + rng.normal(0.0, float(spec["sd"]), size=len(tiers))
    return np.clip(values, lo, hi)


def _assert_fairness_audit_power(frame: pd.DataFrame) -> None:
    """Spec §15: ≥100 clusters per tier, so tier-level estimates are stable.

    A thin tier would make the blueprint §8.4 fairness audit unreportable — the
    confidence intervals would swamp any disparity worth acting on.
    """
    counts = frame["geo_tier"].value_counts()
    thin = counts[counts < 100]
    if not thin.empty:
        raise ValueError(
            f"Tier(s) {thin.to_dict()} have fewer than 100 geography clusters. "
            "Spec §15 requires >=100 per tier for a credible fairness audit. "
            "Raise scale.n_geographies or rebalance geo_tier_weights."
        )
