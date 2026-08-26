"""Unit tests for the GT-01 recovery harness.

GT-01 currently FAILS on its magnitude clause, and a failing test is exactly the
situation where the harness itself has to be beyond doubt. If the harness were
wrong, the failure would be evidence about the harness rather than about the
dataset, and the ruling A49 asks for would be based on nothing.

Two defects were found and fixed while building it, and both are pinned below:

* **Identification.** The first draft emitted a full dummy set for BOTH
  ``geo_tier`` and ``category`` with no intercept. Each block sums to 1, so the
  two are collinear with each other; statsmodels returned NaN standard errors
  and level estimates near -1.2 against planted values near 0. That read as
  catastrophic recovery failure and as sign flips on geography. It was an
  identification bug in the test.
* **Scope.** ``IN_SCOPE_FOR_SIGNS`` was a case-sensitive substring match on
  ``("Strong", "Moderate")``, which silently excluded ``"Weak-moderate"`` while a
  comment two lines above claimed it was included.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis import gt_recovery as G

CENTERING = {"seller_rating_center": 4.30, "product_rating_center": 4.20,
             "est_delivery_days_center": 4, "discount_pct_center": 0.08,
             "discount_pct_unit": 0.10, "order_value_scale": 1000}


def frame(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    return pd.DataFrame({
        "is_cod": rng.integers(0, 2, n).astype(float),
        "serviceability_score": rng.normal(0.7, 0.1, n),
        "address_completeness_score": rng.uniform(0.4, 1.0, n),
        "seller_sla_breach_rate": rng.uniform(0.0, 0.2, n),
        "seller_rating": rng.uniform(3.5, 5.0, n),
        "product_rating": rng.uniform(3.0, 5.0, n),
        "review_count": rng.integers(0, 500, n),
        "order_value": rng.lognormal(6.5, 0.6, n),
        "discount_pct": rng.uniform(0.0, 0.4, n),
        "cart_size": rng.integers(1, 6, n),
        "estimated_delivery_days": rng.integers(2, 9, n),
        "pit_rto_rate_shrunk": rng.uniform(0.0, 0.4, n),
        "pit_is_new_customer": rng.integers(0, 2, n).astype(float),
        "pit_orders_delivered": rng.integers(0, 20, n),
        "pit_cod_share": rng.uniform(0.0, 1.0, n),
        "paid_via_switch": rng.integers(0, 2, n).astype(float),
        "is_month_end_window": rng.integers(0, 2, n).astype(float),
        "geo_tier": rng.choice(["METRO", "TIER1", "TIER2", "TIER3"], n),
        "category": rng.choice(["GROCERY_FMCG", "FASHION", "ELECTRONICS"], n),
        "rto_flag": rng.integers(0, 2, n).astype(float),
    })


# --- identification --------------------------------------------------------


def test_categorical_blocks_are_contrasts_not_full_dummy_sets():
    """Both blocks drop their reference level. Emitting full sets for both makes
    them collinear with each other and the fit returns NaN standard errors."""
    X = G.recovery_matrix(frame(), CENTERING)
    assert "geo_tier[METRO]" not in X.columns
    assert "category[GROCERY_FMCG]" not in X.columns
    assert "geo_tier[TIER3]" in X.columns
    assert "category[FASHION]" in X.columns


def test_recovery_matrix_is_full_rank():
    """The property the identification bug violated. Checked directly rather
    than via 'the fit produced numbers', because the broken version also
    produced numbers -- they were just meaningless."""
    X = G.recovery_matrix(frame(), CENTERING)
    design = np.column_stack([np.ones(len(X)), X.to_numpy(float)])
    assert np.linalg.matrix_rank(design) == design.shape[1]


def test_missing_reference_level_is_an_error_not_a_silent_full_set():
    f = frame()
    f["geo_tier"] = "TIER3"
    with pytest.raises(AssertionError, match="reference level"):
        G.recovery_matrix(f, CENTERING)


# --- planted contrasts -----------------------------------------------------


def test_planted_levels_become_contrasts_against_the_reference():
    """A regression cannot recover absolute dummy levels -- only differences.
    Comparing a fitted contrast to a planted LEVEL would fail every categorical
    term for a reason that has nothing to do with the data."""
    out = G.planted_contrasts({
        "geo_tier[METRO]": -0.35, "geo_tier[TIER3]": 0.45,
        "is_cod": 1.60,
    })
    assert out["geo_tier[TIER3]"] == pytest.approx(0.80)
    assert "geo_tier[METRO]" not in out          # the reference has no contrast
    assert out["is_cod"] == 1.60                  # non-categoricals pass through


def test_planted_contrasts_requires_the_reference_to_be_planted():
    with pytest.raises(AssertionError, match="reference level"):
        G.planted_contrasts({"geo_tier[TIER3]": 0.45})


# --- the untestable set ----------------------------------------------------


def test_latents_and_shock_terms_are_excluded_from_the_denominator():
    """A coefficient on a variable the model is forbidden to see is not
    recoverable by any analysis. Excluding them is forced; the exclusion is
    named rather than pattern-matched so it cannot quietly widen."""
    testable = G.testable_terms({
        "is_cod": 1.6, "latent_intent": 0.7, "latent_trust": -0.3,
        "shock.attempt_delay_days": 0.22, "shock.noise_sd": 3.3,
        "pit_cod_share": 0.35,
    })
    assert set(testable) == {"is_cod", "pit_cod_share"}


# --- the attenuation diagnostic -------------------------------------------


def test_attenuation_matches_the_closed_form_and_shrinks_with_noise():
    """A37 raised noise_sd 0.85 -> 3.3125. The predicted attenuation is what
    turns GT-01's failure from 'the data is wrong' into 'the threshold belongs
    to a different noise level', so the formula has to be right."""
    assert G.expected_attenuation(0.0) == pytest.approx(1.0)
    assert G.expected_attenuation(0.85) == pytest.approx(0.9055, abs=0.005)
    assert G.expected_attenuation(3.3125) == pytest.approx(0.480, abs=0.005)
    assert G.expected_attenuation(3.3125) < G.expected_attenuation(0.85)


# --- the sign clause -------------------------------------------------------


def test_sign_scope_sets_are_explicit_and_disjoint():
    """The case-sensitivity bug: `str.contains("Strong|Moderate")` never matched
    lowercase "Weak-moderate", so the code contradicted its own comment. Both
    sets are now enumerated."""
    assert "Weak-moderate" not in G.IN_SCOPE_FOR_SIGNS
    assert "Weak-moderate" in G.WATCHED_FOR_SIGNS
    assert not (G.IN_SCOPE_FOR_SIGNS & G.WATCHED_FOR_SIGNS)
    assert "Moderate-strong" in G.IN_SCOPE_FOR_SIGNS


def test_every_spec_strength_label_is_classified():
    """A label that is in neither set would silently escape the sign clause."""
    known = G.IN_SCOPE_FOR_SIGNS | G.WATCHED_FOR_SIGNS
    for _ref, strength in G.SPEC_STRENGTH.values():
        assert strength in known or strength.startswith("Weak"), strength


# --- end to end ------------------------------------------------------------


def test_gt_01_runs_and_reports_both_clauses():
    truth = {
        "coefficient_ledger": {"rto_model": {
            "is_cod": 1.6, "pit_cod_share": 0.35, "latent_intent": 0.7,
            "shock.noise_sd": 3.3125,
            "geo_tier[METRO]": -0.35, "geo_tier[TIER1]": -0.1,
            "geo_tier[TIER2]": 0.2, "geo_tier[TIER3]": 0.45,
        }},
        "frozen": {"post_dispatch_noise_sd": 3.3125,
                   "post_dispatch_noise_sd_spec_value": 0.85},
    }
    out = G.gt_01(frame(2000), truth, CENTERING)
    assert 0.0 <= out["share_inside_ci"] <= 1.0
    assert out["n_untestable"] == 2                      # latent_intent, shock.*
    assert not out["table"]["ci_low"].isna().any()       # the identification bug
    assert out["expected_attenuation"] == pytest.approx(0.480, abs=0.005)


def test_gt_01_requires_the_superseded_noise_level_on_the_record():
    """The diagnosis compares sigma = 3.3125 against the sigma = 0.85 the spec
    was written for. If the truth file stops recording what A37 superseded, the
    comparison must fail loudly rather than fall back to a remembered constant."""
    truth = {"coefficient_ledger": {"rto_model": {"is_cod": 1.6}},
             "frozen": {"post_dispatch_noise_sd": 3.3125}}
    with pytest.raises(AssertionError, match="post_dispatch_noise_sd_spec_value"):
        G.gt_01(frame(200), truth, CENTERING)
