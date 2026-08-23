"""Empirical-Bayes shrinkage for low-count behavioural rates.

Business concept
----------------
A customer with one prior order that went wrong has a raw historical RTO rate of
100%. That number is useless — it says almost nothing about their future behaviour.
Shrinkage pulls such rates toward a prior in proportion to how little evidence
supports them, so a 1-in-1 failure reads as "slightly worse than average" rather
than "certain to fail".

This matters commercially, not just statistically: ``pit_rto_rate_shrunk`` carries
the largest observable coefficient in the RTO model (+2.80, spec §8.2). Feeding it a
raw rate would mean a single unlucky delivery pushes a customer over the 25.7%
break-even threshold and into a fee — which is precisely the unfair outcome the
fairness overlay in blueprint §8.4 exists to prevent.

Spec references
---------------
- Spec §3.7   — ``pit_rto_rate_shrunk``, "Empirical-Bayes shrunk toward population mean, k=8"
- Blueprint H3 — "Small-n instability for customers with 1-2 orders; needs shrinkage"
- Brief §9.7   — "Use empirical-Bayes shrinkage (k = 8) ... because raw rates at n=1
                  are useless"
"""

from __future__ import annotations

import numpy as np


class ShrinkageError(ValueError):
    """Raised when shrinkage is called with invalid inputs."""


def shrink_rate(
    successes: np.ndarray,
    trials: np.ndarray,
    prior_mean: float,
    k: float,
) -> np.ndarray:
    """Shrink an observed rate toward a prior mean.

    ``(successes + k * prior_mean) / (trials + k)``

    Read ``k`` as "how many prior observations the prior is worth". At ``k = 8``, a
    customer needs eight resolved orders before their own record carries as much
    weight as the population baseline.

    Behaviour at the boundaries — both are load-bearing:

    - **n = 0** returns exactly ``prior_mean``. A customer with no resolved history
      is treated as average, not as risk-free and not as risky. This is the value
      every new customer's ``pit_rto_rate_shrunk`` takes, so it directly determines
      how new customers are scored.
    - **n = 1, 1 failure** returns ``(1 + k*prior) / (1 + k)`` — for k=8 and a
      prior of 0.165, that is 0.258, not 1.00.

    Parameters
    ----------
    successes
        Count of the event, e.g. resolved RTOs.
    trials
        Count of opportunities, e.g. resolved orders.
    prior_mean
        The population baseline to shrink toward. Supplied from params.yaml — it must
        be a fixed prior, not the realised end-of-window rate, which would be a global
        outcome-derived quantity leaking backward into a point-in-time feature.
    k
        Shrinkage strength, in units of prior observations.

    Returns
    -------
    Shrunk rates in [0, 1], same shape as the inputs.
    """
    successes = np.asarray(successes, dtype=np.float64)
    trials = np.asarray(trials, dtype=np.float64)

    if successes.shape != trials.shape:
        raise ShrinkageError(
            f"successes shape {successes.shape} does not match trials shape {trials.shape}."
        )
    if k <= 0:
        raise ShrinkageError(f"k must be positive, got {k}. k is 'how many observations "
                             "the prior is worth', so it cannot be zero or negative.")
    if not 0.0 <= prior_mean <= 1.0:
        raise ShrinkageError(f"prior_mean must lie in [0, 1], got {prior_mean}.")
    if np.any(successes < 0) or np.any(trials < 0):
        raise ShrinkageError("Counts cannot be negative.")
    if np.any(successes > trials):
        raise ShrinkageError(
            "successes exceeds trials for at least one row — an upstream consistency "
            "constraint has been violated."
        )

    return (successes + k * prior_mean) / (trials + k)


def raw_rate(
    successes: np.ndarray,
    trials: np.ndarray,
    *,
    undefined: float = np.nan,
) -> np.ndarray:
    """Unshrunk rate, with an explicit value where the denominator is zero.

    Stored alongside the shrunk rate as ``pit_rto_rate_raw`` so an analyst can see
    both and understand what shrinkage did. Deliberately returns ``undefined``
    (NaN by default) at n=0 rather than 0.0 — a customer with no history has an
    *unknown* rate, not a zero one, and conflating those is how new customers get
    scored as safe.
    """
    successes = np.asarray(successes, dtype=np.float64)
    trials = np.asarray(trials, dtype=np.float64)
    out = np.full(successes.shape, undefined, dtype=np.float64)
    has_history = trials > 0
    out[has_history] = successes[has_history] / trials[has_history]
    return out
