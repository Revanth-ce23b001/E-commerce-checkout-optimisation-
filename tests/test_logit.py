"""Unit tests for the logit assembler, the coefficient ledger, and CAL-09.

Coefficient values used here are arbitrary test fixtures, not business assumptions.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.models.logit import (
    INTERCEPT_TERM,
    CoefficientLedger,
    LogitAssembler,
    LogitError,
    average_marginal_effect,
    logistic,
    logit,
    marginal_effect_at_baseline,
)
from src.validation.result import Severity, Status
from src.validation.tests_cal import cal_09_no_slope_changed


class FakeParams:
    """Minimal stand-in for the loaded Params object."""

    def __init__(self, raw: dict) -> None:
        self.raw = raw

    def get(self, dotted_path: str, default=...):
        node = self.raw
        for part in dotted_path.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is not ...:
                    return default
                raise KeyError(dotted_path)
            node = node[part]
        return node


# --- logistic / logit -------------------------------------------------------


def test_logistic_matches_known_values():
    np.testing.assert_allclose(logistic(np.array([0.0])), [0.5])
    np.testing.assert_allclose(logistic(np.array([-3.15])), [0.0410913], atol=1e-6)
    np.testing.assert_allclose(logistic(np.array([-1.55])), [0.1750863], atol=1e-6)


def test_logistic_is_stable_in_both_tails():
    """The naive 1/(1+exp(-x)) overflows here; this must not."""
    extreme = np.array([-800.0, -50.0, 0.0, 50.0, 800.0])
    with np.errstate(over="raise", invalid="raise"):
        out = logistic(extreme)
    assert np.all(np.isfinite(out))
    assert out[0] == pytest.approx(0.0)
    assert out[-1] == pytest.approx(1.0)
    assert np.all(np.diff(out) >= 0)


def test_logit_is_the_inverse_of_logistic():
    x = np.array([-4.0, -1.5, 0.0, 0.75, 3.0])
    np.testing.assert_allclose(logit(logistic(x)), x, atol=1e-9)


def test_logit_rejects_degenerate_probabilities():
    with pytest.raises(LogitError):
        logit(np.array([0.0]))
    with pytest.raises(LogitError):
        logit(np.array([1.0]))


# --- marginal effects (decision A6) ----------------------------------------


def test_marginal_effect_at_baseline_reproduces_the_spec_derivation():
    """Spec §8.3: logistic(-3.15 + 1.60) - logistic(-3.15) = 17.5% - 4.1%."""
    effect = marginal_effect_at_baseline(intercept=-3.15, coefficient=1.60)
    assert effect == pytest.approx(13.398, abs=0.01)


def test_marginal_effect_moves_with_the_intercept():
    """Why A6 matters: the same coefficient gives a different effect at a different intercept."""
    at_315 = marginal_effect_at_baseline(-3.15, 1.60)
    at_325 = marginal_effect_at_baseline(-3.25, 1.60)
    at_300 = marginal_effect_at_baseline(-3.00, 1.60)
    assert at_325 == pytest.approx(12.38, abs=0.02)
    assert at_300 == pytest.approx(15.04, abs=0.02)
    assert at_325 < at_315 < at_300


def test_average_marginal_effect_differs_from_the_baseline_effect():
    """The logistic curve is steeper in the middle, so the population average differs."""
    rng = np.random.default_rng(0)
    lp = -3.15 + rng.normal(0.0, 1.0, size=100_000)
    ame = average_marginal_effect(lp, coefficient=1.60)
    at_baseline = marginal_effect_at_baseline(-3.15, 1.60)
    assert ame != pytest.approx(at_baseline, abs=0.1), (
        "AME and at-baseline effect coincided; the test population has no dispersion."
    )


# --- assembler --------------------------------------------------------------


def _assembler(n=5, ledger=None):
    return LogitAssembler("test_model", n_rows=n, ledger=ledger or CoefficientLedger())


def test_assembler_sums_terms_and_traces_each_component():
    a = _assembler(n=3)
    a.add_intercept(-3.0)
    a.add_numeric("is_cod", coef=1.6, values=np.array([1.0, 0.0, 1.0]))
    a.add_numeric("address", coef=-1.4, values=np.array([0.5, 0.5, 1.0]))

    np.testing.assert_allclose(a.linear_predictor(), [-3.0 + 1.6 - 0.7, -3.0 - 0.7, -3.0 + 1.6 - 1.4])
    trace = a.components()
    assert set(trace) == {INTERCEPT_TERM, "is_cod", "address"}
    np.testing.assert_allclose(trace["is_cod"], [1.6, 0.0, 1.6])


def test_component_rows_serialise_for_jsonb():
    a = _assembler(n=2)
    a.add_intercept(-3.0).add_numeric("is_cod", 1.6, np.array([1.0, 0.0]))
    rows = a.component_rows()
    assert len(rows) == 2
    assert rows[0]["is_cod"] == pytest.approx(1.6)
    assert all(isinstance(v, float) for v in rows[0].values())


def test_categorical_maps_each_level():
    a = _assembler(n=4)
    a.add_intercept(0.0)
    a.add_categorical(
        "geo_tier",
        coef_map={"METRO": -0.35, "TIER1": -0.10, "TIER2": 0.20, "TIER3": 0.45},
        levels=np.array(["METRO", "TIER3", "TIER2", "TIER1"]),
    )
    np.testing.assert_allclose(a.components()["geo_tier"], [-0.35, 0.45, 0.20, -0.10])


def test_categorical_rejects_an_unmapped_level():
    """An unmapped level would silently contribute zero, looking like a reference category."""
    a = _assembler(n=2)
    a.add_intercept(0.0)
    with pytest.raises(LogitError, match="no coefficient in params.yaml"):
        a.add_categorical("geo_tier", {"METRO": -0.35}, np.array(["METRO", "TIER3"]))


def test_interaction_multiplies_both_drivers():
    a = _assembler(n=4)
    a.add_intercept(0.0)
    a.add_interaction("month_end_x_cod", 0.30,
                      left=np.array([1.0, 1.0, 0.0, 0.0]),
                      right=np.array([1.0, 0.0, 1.0, 0.0]))
    np.testing.assert_allclose(a.components()["month_end_x_cod"], [0.30, 0.0, 0.0, 0.0])


def test_missing_intercept_is_an_error():
    a = _assembler(n=2)
    a.add_numeric("is_cod", 1.6, np.array([1.0, 0.0]))
    with pytest.raises(LogitError, match="no intercept"):
        a.linear_predictor()


def test_null_intercept_reports_that_the_calibrator_has_not_run():
    with pytest.raises(LogitError, match="calibrator must run"):
        _assembler(n=2).add_intercept(None)


def test_adding_the_same_term_twice_is_an_error():
    a = _assembler(n=2)
    a.add_intercept(0.0).add_numeric("is_cod", 1.6, np.array([1.0, 0.0]))
    with pytest.raises(LogitError, match="added twice"):
        a.add_numeric("is_cod", 1.6, np.array([1.0, 0.0]))


def test_non_finite_driver_is_caught_with_a_pointer_to_imputation():
    a = _assembler(n=2)
    a.add_intercept(0.0)
    with pytest.raises(LogitError, match="explicit imputation rule"):
        a.add_numeric("pit_cod_share", 2.2, np.array([0.5, np.nan]))


def test_wrong_row_count_is_an_error():
    a = _assembler(n=5)
    a.add_intercept(0.0)
    with pytest.raises(LogitError, match="expected shape"):
        a.add_numeric("is_cod", 1.6, np.array([1.0, 0.0]))


# --- ledger and CAL-09 ------------------------------------------------------


def test_ledger_records_used_coefficients_and_excludes_the_intercept():
    ledger = CoefficientLedger()
    a = LogitAssembler("rto_model", n_rows=2, ledger=ledger)
    a.add_intercept(-3.25).add_numeric("is_cod", 1.60, np.array([1.0, 0.0]))
    assert ledger.slopes("rto_model") == {"is_cod": 1.60}
    assert INTERCEPT_TERM in ledger.as_dict()["rto_model"]


def test_ledger_rejects_a_coefficient_used_with_two_values():
    ledger = CoefficientLedger()
    ledger.record("rto_model", "is_cod", 1.60)
    with pytest.raises(LogitError, match="two different values"):
        ledger.record("rto_model", "is_cod", 1.55)


def test_cal_09_passes_when_the_generator_uses_the_declared_coefficients():
    ledger = CoefficientLedger()
    a = LogitAssembler("rto_model", n_rows=2, ledger=ledger)
    a.add_intercept(-3.25)
    a.add_numeric("is_cod", 1.60, np.array([1.0, 0.0]))
    a.add_categorical("geo_tier", {"METRO": -0.35, "TIER3": 0.45}, np.array(["METRO", "TIER3"]))

    params = FakeParams({"rto_model": {"coefficients": {
        "is_cod": 1.60, "geo_tier": {"METRO": -0.35, "TIER3": 0.45}}}})
    result = cal_09_no_slope_changed(ledger, params, ("rto_model",))
    assert result.status is Status.PASS
    assert result.severity is Severity.HARD


def test_cal_09_catches_a_slope_the_generator_silently_changed():
    """The failure mode CAL-09 exists for: tuning a slope to make a target pass."""
    ledger = CoefficientLedger()
    LogitAssembler("rto_model", n_rows=2, ledger=ledger) \
        .add_intercept(-3.25).add_numeric("is_cod", 1.85, np.array([1.0, 0.0]))

    params = FakeParams({"rto_model": {"coefficients": {"is_cod": 1.60}}})
    result = cal_09_no_slope_changed(ledger, params, ("rto_model",))
    assert result.status is Status.FAIL
    assert result.blocking
    assert "1.6" in result.detail and "1.85" in result.detail


def test_cal_09_catches_a_business_literal_that_never_reached_params_yaml():
    ledger = CoefficientLedger()
    LogitAssembler("rto_model", n_rows=2, ledger=ledger) \
        .add_intercept(-3.25).add_numeric("secret_fudge", 0.4, np.array([1.0, 0.0]))

    params = FakeParams({"rto_model": {"coefficients": {}}})
    result = cal_09_no_slope_changed(ledger, params, ("rto_model",))
    assert result.status is Status.FAIL
    assert "leaked into src/" in result.detail


def test_cal_09_catches_a_declared_relationship_that_was_never_applied():
    """A coefficient in params.yaml that the generator ignored is a silently absent effect."""
    ledger = CoefficientLedger()
    LogitAssembler("rto_model", n_rows=2, ledger=ledger).add_intercept(-3.25)

    params = FakeParams({"rto_model": {"coefficients": {"address_completeness": -1.40}}})
    result = cal_09_no_slope_changed(ledger, params, ("rto_model",))
    assert result.status is Status.FAIL
    assert "never used" in result.detail


def test_cal_09_allows_the_intercept_to_differ_from_the_file():
    """The intercept is one of the values the calibrator is permitted to solve."""
    ledger = CoefficientLedger()
    LogitAssembler("rto_model", n_rows=2, ledger=ledger) \
        .add_intercept(-3.247).add_numeric("is_cod", 1.60, np.array([1.0, 0.0]))

    params = FakeParams({"rto_model": {
        "intercept_solved": -9.99,  # deliberately different
        "coefficients": {"is_cod": 1.60}}})
    assert cal_09_no_slope_changed(ledger, params, ("rto_model",)).status is Status.PASS
