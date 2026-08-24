"""Module 06 — ``dim_customer`` (pre-history columns) + ``truth_customer_latent``.

Business concept
----------------
This module creates the engine of the whole simulation: four hidden traits that
drive everything downstream and that no analyst will ever see.

``latent_intent`` is the single most important object in the project. It is the
low-commitment, free-optionality trait — "order it, decide later" — and it pushes
customers toward **both** COD (+0.40) **and** RTO (+0.70). Because it drives
both, a naive COD-vs-prepaid crosstab attributes its effect to COD. That is the
confounding the entire case study is built on. It is why the adjusted effect is
smaller than the naive one, why GT-03 is designed to *fail* to fully recover the
truth, and why CAL-11 gates the selection share at [0.25, 0.45].

Two construction choices worth stating plainly:

**The latents are correlated, not orthogonal.** ``corr(trust, liquidity) = +0.25``,
``corr(intent, liquidity) = −0.20``, ``corr(intent, trust) = −0.15``, and price
sensitivity runs −0.30 against liquidity. Perfectly independent latents would be
unrealistic and would make the confounding far too easy to disentangle — an
analyst could control for one proxy and recover the truth, which is precisely the
outcome §9.2 says must not happen.

**Liquidity is shifted by geography, then everything is re-standardised.** Spec
§6.1 wants liquidity correlated with tier; the shift implements that, and the
z-scoring afterwards is what makes ``z(latent_liquidity)`` in the logits mean what
the coefficient assumes it means.

Spec references
---------------
- Spec §3.5, §3.13 — the column lists
- Spec §6.1        — the four latents, their correlations, the tier shift
- Spec §7.2, §8.2  — the coefficients these feed

⚠️ ``distributions.customer`` is [A27 PROPOSED] and not yet approved.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from numpy.random import Generator

from src.models.logit import logistic

# The order of the four latents everywhere in this module. Fixed, because the
# correlation matrix is indexed positionally.
LATENT_NAMES = ("trust", "liquidity", "intent", "price_sensitivity")


def generate_customers(
    params,
    geography: pd.DataFrame,
    rng_customer: Generator,
    rng_latent: Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build ``dim_customer`` (pre-history columns only) and the latent table.

    Two substreams are used deliberately. Demographics come from ``customer`` and
    the latents from ``latent``, so that changing the age-bucket mix cannot shift
    a single customer's ``latent_intent``. That independence is what makes the
    sensitivity analysis interpretable (CLAUDE.md invariant 11).

    Returns
    -------
    ``(dim_customer, truth_customer_latent)``. ``true_cod_propensity`` is **not**
    populated here: it is the customer's mean P(COD) across their sessions, which
    do not exist until module 08. It is filled at the module-20 roll-up.
    """
    n = int(params.require("scale.n_customers"))
    customer = params.require("distributions.customer")
    latents_cfg = params.require("latents")
    window_start = _as_date(params.require("meta.window_start"))

    # --- geography assignment ------------------------------------------------
    geo_ids = geography["geography_id"].to_numpy()
    geo_tiers = geography["geo_tier"].to_numpy()
    tier_weights = params.require("distributions.geo_tier_weights")
    home_idx = _draw_home_geography(rng_customer, geo_tiers, tier_weights, n)

    frame = pd.DataFrame({
        "customer_id": [f"CUS_{i:07d}" for i in range(n)],
        "home_geography_id": geo_ids[home_idx],
    })
    home_tier = geo_tiers[home_idx]

    # --- tenure --------------------------------------------------------------
    tenure = _lognormal_capped(rng_customer, customer["tenure_days_at_window_start"], n)
    frame["tenure_days_at_window_start"] = np.maximum(tenure.astype(np.int64), 1)
    frame["signup_date"] = [
        window_start - dt.timedelta(days=int(d))
        for d in frame["tenure_days_at_window_start"]
    ]

    frame["age_bucket"] = _categorical(rng_customer, customer["age_bucket_weights"], n)
    frame["acquisition_channel"] = _categorical(
        rng_customer, customer["acquisition_channel_weights"], n
    )

    # --- the latents ---------------------------------------------------------
    latent_values = draw_latents(rng_latent, n, home_tier, latents_cfg)

    # --- saved prepaid instrument -------------------------------------------
    # A mechanical ceiling on any prepaid-shift intervention (H7 limitation): a
    # customer with no tokenised instrument cannot be nudged to prepay, however
    # persuasive the checkout is. Driven by trust and liquidity rather than drawn
    # flat, because the customers who lack an instrument are exactly the ones the
    # intervention would target.
    saved_logit = (
        np.log(float(customer["saved_instrument_base_rate"])
               / (1.0 - float(customer["saved_instrument_base_rate"])))
        + float(customer["saved_instrument_trust_weight"]) * latent_values["trust"]
        + float(customer["saved_instrument_liquidity_weight"]) * latent_values["liquidity"]
    )
    frame["has_saved_prepaid_instrument"] = (
        rng_customer.random(n) < logistic(saved_logit)
    )

    latent_frame = pd.DataFrame({
        "customer_id": frame["customer_id"],
        "latent_trust": latent_values["trust"].round(4),
        "latent_liquidity": latent_values["liquidity"].round(4),
        "latent_intent": latent_values["intent"].round(4),
        "latent_price_sensitivity": latent_values["price_sensitivity"].round(4),
        "true_cod_propensity": np.nan,      # filled at the module-20 roll-up
    })

    return frame, latent_frame


def draw_latents(
    rng: Generator, n: int, home_tier: np.ndarray, latents_cfg: dict
) -> dict[str, np.ndarray]:
    """Draw the four correlated latents, shift liquidity by tier, z-score.

    Returned as z-scores because every coefficient in §7.2 and §8.2 that reads a
    latent is stated per standard deviation. Storing the raw shifted values would
    silently rescale every latent coefficient in the project.
    """
    corr = build_correlation_matrix(latents_cfg)
    raw = rng.multivariate_normal(np.zeros(len(LATENT_NAMES)), corr, size=n)

    values = {name: raw[:, i] for i, name in enumerate(LATENT_NAMES)}

    # Spec §6.1: liquidity correlates with geo tier. Implemented as a mean shift
    # per tier rather than a correlation coefficient, so it composes cleanly with
    # the latent-to-latent correlation structure above.
    shift = latents_cfg["liquidity_geo_tier_shift"]
    missing = set(np.unique(home_tier).tolist()) - set(shift)
    if missing:
        raise ValueError(f"latents.liquidity_geo_tier_shift has no entry for {sorted(missing)}.")
    values["liquidity"] = values["liquidity"] + np.array(
        [float(shift[t]) for t in home_tier]
    )

    return {name: _zscore(v) for name, v in values.items()}


def build_correlation_matrix(latents_cfg: dict) -> np.ndarray:
    """The 4x4 latent correlation matrix, checked for positive definiteness.

    Unspecified pairs are zero: ``corr(trust, price_sensitivity)`` and
    ``corr(intent, price_sensitivity)`` are not stated in §6.1, and inventing a
    value for them would be an unflagged assumption. Zero is the honest default
    and is recorded as such.

    A non-positive-definite matrix would make ``multivariate_normal`` silently
    return degenerate draws, so it is rejected loudly instead.
    """
    c = latents_cfg["correlations"]
    trust_liq = float(c["trust_liquidity"])
    intent_liq = float(c["intent_liquidity"])
    intent_trust = float(c["intent_trust"])
    price_liq = float(latents_cfg["price_sensitivity_liquidity_corr"])

    corr = np.array([
        # trust        liquidity   intent        price_sensitivity
        [1.0,          trust_liq,  intent_trust, 0.0],
        [trust_liq,    1.0,        intent_liq,   price_liq],
        [intent_trust, intent_liq, 1.0,          0.0],
        [0.0,          price_liq,  0.0,          1.0],
    ], dtype=np.float64)

    eigenvalues = np.linalg.eigvalsh(corr)
    if eigenvalues.min() <= 0:
        raise ValueError(
            f"The latent correlation matrix is not positive definite "
            f"(smallest eigenvalue {eigenvalues.min():.6f}). The stated pairwise "
            "correlations in latents.correlations are mutually inconsistent — that "
            "is a finding about the assumption set, not something to patch here."
        )
    return corr


def _zscore(values: np.ndarray) -> np.ndarray:
    sd = values.std(ddof=0)
    if sd == 0:
        raise ValueError("A latent has zero variance — every customer would be identical.")
    return (values - values.mean()) / sd


def _draw_home_geography(
    rng: Generator, geo_tiers: np.ndarray, tier_weights: dict, n: int
) -> np.ndarray:
    """Assign each customer a home cluster, weighted so the POPULATION matches
    ``geo_tier_weights`` rather than the cluster count.

    Clusters are allocated proportionally too, so these coincide today — but they
    are different things, and coupling them would mean that changing the cluster
    count silently changed the customer tier mix.
    """
    probs = np.zeros(len(geo_tiers), dtype=np.float64)
    for tier, weight in tier_weights.items():
        mask = geo_tiers == tier
        count = int(mask.sum())
        if count == 0:
            raise ValueError(f"No geography clusters for tier {tier!r}.")
        probs[mask] = float(weight) / count
    probs = probs / probs.sum()
    return rng.choice(len(geo_tiers), size=n, p=probs)


def _categorical(rng: Generator, weights: dict, n: int) -> np.ndarray:
    names = [str(k) for k in weights]
    probs = np.array([float(weights[k]) for k in weights], dtype=np.float64)
    if not np.isclose(probs.sum(), 1.0):
        raise ValueError(f"Weights sum to {probs.sum()}, expected 1.0: {weights}")
    return np.array(names)[rng.choice(len(names), size=n, p=probs)]


def _lognormal_capped(rng: Generator, spec: dict, n: int) -> np.ndarray:
    values = rng.lognormal(float(spec["mu"]), float(spec["sigma"]), size=n)
    return np.minimum(values, float(spec["max"]))


def _as_date(value) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))
