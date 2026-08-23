"""Unit tests for empirical-Bayes shrinkage.

Brief §15 requires a unit test for 'shrinkage at n=0' specifically, because that is
the value every new customer's ``pit_rto_rate_shrunk`` takes, and it therefore
determines how customers with no track record are scored.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.utils.shrinkage import ShrinkageError, raw_rate, shrink_rate

# Test fixtures, not business assumptions.
PRIOR = 0.165
K = 8.0


def test_at_zero_trials_returns_exactly_the_prior():
    """A customer with no resolved history is treated as average — not safe, not risky."""
    out = shrink_rate(np.zeros(3), np.zeros(3), prior_mean=PRIOR, k=K)
    np.testing.assert_allclose(out, [PRIOR] * 3)


def test_one_failure_in_one_order_is_not_read_as_certain_failure():
    """The whole point: a raw rate of 100% at n=1 is useless."""
    out = shrink_rate(np.array([1.0]), np.array([1.0]), prior_mean=PRIOR, k=K)
    assert out[0] == pytest.approx((1 + K * PRIOR) / (1 + K))
    assert out[0] == pytest.approx(0.2578, abs=1e-4)
    assert out[0] < 0.30, "A single failure pushed the customer near a fee threshold."


def test_shrinkage_weakens_as_evidence_accumulates():
    """With enough history a customer's own record dominates the prior."""
    trials = np.array([1.0, 4.0, 20.0, 200.0])
    out = shrink_rate(trials.copy(), trials, prior_mean=PRIOR, k=K)  # 100% raw each time
    assert np.all(np.diff(out) > 0)
    assert out[-1] > 0.95


def test_result_always_lies_between_the_raw_rate_and_the_prior():
    rng = np.random.default_rng(7)
    trials = rng.integers(1, 40, size=500).astype(float)
    successes = np.floor(trials * rng.random(500))
    shrunk = shrink_rate(successes, trials, prior_mean=PRIOR, k=K)
    raw = successes / trials
    assert np.all(shrunk >= np.minimum(raw, PRIOR) - 1e-12)
    assert np.all(shrunk <= np.maximum(raw, PRIOR) + 1e-12)


def test_output_is_always_a_valid_probability():
    rng = np.random.default_rng(11)
    trials = rng.integers(0, 50, size=1000).astype(float)
    successes = np.floor(trials * rng.random(1000))
    out = shrink_rate(successes, trials, prior_mean=PRIOR, k=K)
    assert np.all((out >= 0.0) & (out <= 1.0))


def test_larger_k_shrinks_harder():
    out = [shrink_rate(np.array([1.0]), np.array([2.0]), PRIOR, k)[0] for k in (1.0, 8.0, 50.0)]
    assert out[0] > out[1] > out[2] > PRIOR


@pytest.mark.parametrize("bad_k", [0.0, -1.0])
def test_non_positive_k_rejected(bad_k):
    with pytest.raises(ShrinkageError, match="k must be positive"):
        shrink_rate(np.array([1.0]), np.array([2.0]), PRIOR, bad_k)


@pytest.mark.parametrize("bad_prior", [-0.01, 1.01])
def test_prior_outside_unit_interval_rejected(bad_prior):
    with pytest.raises(ShrinkageError, match=r"\[0, 1\]"):
        shrink_rate(np.array([1.0]), np.array([2.0]), bad_prior, K)


def test_successes_exceeding_trials_is_rejected_as_an_upstream_bug():
    with pytest.raises(ShrinkageError, match="exceeds trials"):
        shrink_rate(np.array([5.0]), np.array([2.0]), PRIOR, K)


def test_negative_counts_rejected():
    with pytest.raises(ShrinkageError, match="negative"):
        shrink_rate(np.array([-1.0]), np.array([2.0]), PRIOR, K)


def test_shape_mismatch_rejected():
    with pytest.raises(ShrinkageError, match="does not match"):
        shrink_rate(np.zeros(3), np.zeros(4), PRIOR, K)


# --- raw rate ---------------------------------------------------------------


def test_raw_rate_is_undefined_not_zero_at_no_history():
    """A customer with no history has an *unknown* rate, not a zero one.

    Conflating those is how new customers get scored as safe.
    """
    out = raw_rate(np.array([0.0, 1.0]), np.array([0.0, 4.0]))
    assert np.isnan(out[0])
    assert out[1] == pytest.approx(0.25)


def test_raw_rate_undefined_value_is_configurable():
    out = raw_rate(np.array([0.0]), np.array([0.0]), undefined=-1.0)
    assert out[0] == -1.0
