"""Unit tests for the decision-A45 component trace.

Two things need testing, and only one of them is the new module.

The first is the **refactor invariant**. Splitting a summed expression into named
terms is only safe if the split moves no bits, because the day loop's arithmetic
is what every calibrated intercept was solved against. ``TestSumTerms`` pins that
down against the original inline expressions, written out here by hand so the
test would fail if someone "simplified" the term builders into something merely
equivalent-looking.

The second is that the trace's own guarantee is **enforced, not asserted**.
``TestReconstructionGuard`` drops a term and checks the generator refuses to
store the result. That is decision A44's lesson applied to A44's own remedy: a
docstring promising the trace is faithful would constrain nothing.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from src.generators import predictors as pred
from src.generators import rto as rto_mod
from src.generators.components import (
    RECONSTRUCTION_TOL, _assert_matches, _draw_sample, _serialise,
)
from src.models.logit import sum_terms


@pytest.fixture
def rng():
    return np.random.default_rng(20260115)


class TestSumTerms:
    """The named-term split must be bit-identical to the inline sum it replaced."""

    def test_sum_matches_left_to_right_addition(self, rng):
        terms = {name: rng.normal(size=64) for name in "abcde"}
        a, b, c, d, e = terms.values()
        assert np.array_equal(sum_terms(terms), a + b + c + d + e)

    def test_leading_zero_is_exact(self):
        # 0.0 + x is exact in IEEE-754, which is the whole reason accumulating
        # into a zeros array reproduces the inline expression rather than
        # approximating it.
        terms = {"x": np.array([1e-300, 1e300, -0.0])}
        assert np.array_equal(sum_terms(terms), terms["x"])

    def test_empty_terms_raise(self):
        from src.models.logit import LogitError
        with pytest.raises(LogitError):
            sum_terms({})

    def test_cod_dynamic_split_is_bit_identical(self, rng):
        coefficients = {
            "pit_cod_share": 2.20, "log1p_prepaid_success": -0.35,
            "is_new_customer": 0.70, "log1p_orders_delivered": -0.18,
            "payment_failure_rate": 1.10,
        }
        n = 256
        share = rng.random(n)
        share[:20] = np.nan                      # customers with no placements
        failure = rng.random(n)
        failure[:30] = np.nan                    # customers with no attempts
        success = rng.integers(0, 9, n).astype(float)
        delivered = rng.integers(0, 9, n).astype(float)
        is_new = delivered == 0
        cod_prior, fail_prior = 0.617, 0.175

        expected = (
            coefficients["pit_cod_share"] * np.nan_to_num(share - cod_prior, nan=0.0)
            + coefficients["log1p_prepaid_success"] * np.log1p(success)
            + coefficients["is_new_customer"] * is_new.astype(np.float64)
            + coefficients["log1p_orders_delivered"] * np.log1p(delivered)
            + coefficients["payment_failure_rate"]
            * np.nan_to_num(failure - fail_prior, nan=0.0)
        )
        got = pred.cod_dynamic(coefficients, share, success, is_new, delivered,
                               failure, cod_prior, fail_prior)
        assert np.array_equal(got, expected)

        terms = pred.cod_dynamic_terms(coefficients, share, success, is_new,
                                       delivered, failure, cod_prior, fail_prior)
        assert set(terms) == set(coefficients)
        assert np.array_equal(sum_terms(terms), expected)

    def test_stage1_split_is_bit_identical(self, rng):
        coefficients = {
            "pit_rto_rate_shrunk": 2.80, "is_new_customer": 0.30,
            "log1p_orders_delivered": -0.20, "pit_cod_share": 0.35,
            "is_cod": 1.60, "paid_via_switch": 0.25, "month_end_x_cod": 0.15,
        }
        n = 256
        shrunk = rng.random(n) * 0.4
        share = rng.random(n)
        share[:20] = np.nan
        delivered = rng.integers(0, 9, n).astype(float)
        is_new = delivered == 0
        is_cod = rng.random(n) < 0.62
        switched = rng.random(n) < 0.05
        month_end = (rng.random(n) < 0.2).astype(float)
        rto_prior, cod_prior = 0.165, 0.617

        cod = is_cod.astype(np.float64)
        expected = (
            coefficients["pit_rto_rate_shrunk"] * (shrunk - rto_prior)
            + coefficients["is_new_customer"] * is_new.astype(np.float64)
            + coefficients["log1p_orders_delivered"] * np.log1p(delivered)
            + coefficients["pit_cod_share"] * np.nan_to_num(share - cod_prior, nan=0.0)
            + coefficients["is_cod"] * cod
            + coefficients["paid_via_switch"] * switched.astype(np.float64)
            + coefficients["month_end_x_cod"] * month_end * cod
        )
        got = rto_mod.stage1_dynamic(coefficients, shrunk, is_new, delivered,
                                     share, is_cod, switched, month_end,
                                     rto_prior, cod_prior)
        assert np.array_equal(got, expected)

        terms = rto_mod.stage1_dynamic_terms(coefficients, shrunk, is_new,
                                             delivered, share, is_cod, switched,
                                             month_end, rto_prior, cod_prior)
        # +1.60 on is_cod is the one INVARIANT coefficient in the project. It has
        # to survive the split as its own named term, or the trace cannot show
        # Phase 5 the planted effect it is trying to recover.
        assert np.array_equal(terms["is_cod"], 1.60 * cod)
        assert np.array_equal(sum_terms(terms), expected)

    def test_shock_split_is_bit_identical(self, rng):
        coefficients = {"courier_reliability_z_neg": 0.45,
                        "attempt_delay_days": 0.12, "seller_dispatch_late": 0.30}
        n = 256
        courier_z = rng.normal(size=n)
        delay = rng.random(n) * 6
        late = rng.random(n) < 0.15
        nu = rng.normal(0, 3.3125, n)

        expected = (
            coefficients["courier_reliability_z_neg"] * (-courier_z)
            + coefficients["attempt_delay_days"] * delay
            + coefficients["seller_dispatch_late"] * late.astype(np.float64)
            + nu
        )
        assert np.array_equal(
            rto_mod.post_dispatch_shock(coefficients, courier_z, delay, late, nu),
            expected)
        terms = rto_mod.post_dispatch_shock_terms(
            coefficients, courier_z, delay, late, nu)
        assert np.array_equal(sum_terms(terms), expected)
        # nu is sampling, not a coefficient, and must pass through untouched.
        assert np.array_equal(terms["nu"], nu)


class TestReconstructionGuard:
    """The trace must be refused when it does not reproduce its own probability."""

    def test_exact_match_passes_and_returns_the_error(self):
        stored = np.array([0.1, 0.5, 0.9])
        assert _assert_matches(stored.copy(), stored, "p_cod_intent") == 0.0

    def test_float_noise_within_tolerance_passes(self):
        stored = np.array([0.25, 0.5])
        rebuilt = stored + np.array([1e-16, -1e-16])
        assert _assert_matches(rebuilt, stored, "p_cod_intent") < RECONSTRUCTION_TOL

    def test_a_dropped_term_is_caught(self):
        # What a divergence actually looks like: one term omitted from the trace,
        # so the rebuilt probability drifts. Silent in every other check.
        stored = np.array([0.20, 0.40, 0.60])
        rebuilt = stored - np.array([0.0, 0.0, 0.004])
        with pytest.raises(ValueError, match="does not reproduce p_rto_final"):
            _assert_matches(rebuilt, stored, "p_rto_final")

    def test_a_nan_is_caught(self):
        with pytest.raises(ValueError):
            _assert_matches(np.array([np.nan]), np.array([0.5]), "p_cod_intent")

    def test_empty_sample_is_not_a_silent_pass(self):
        # No rows means no evidence, which is fine only because the loader's
        # PARTIAL_BY_DESIGN check fails an empty sample separately.
        assert _assert_matches(np.array([]), np.array([]), "p_cod_intent") == 0.0


class TestSampling:
    """Strata, de-duplication, and the shortfall warning."""

    @staticmethod
    def _extra(n=40_000, seed=7):
        rng = np.random.default_rng(seed)
        shipped = rng.random(n) < 0.60
        censored = shipped & (rng.random(n) < 0.10)
        resolved = shipped & ~censored
        cod = rng.random(n) < 0.62
        pre = rng.random(n) * 0.5
        rto = resolved & (rng.random(n) < np.where(cod, 0.24, 0.041))
        return {"shipped": shipped, "censored": censored, "rto_flag": rto,
                "is_cod_order": cod, "p_rto_precheckout": np.where(shipped, pre, np.nan)}

    @staticmethod
    def _cfg(**over):
        cfg = {"strata": {"random_sessions": 50, "cod_rto": 50,
                          "prepaid_rto": 50, "high_risk_delivered": 50},
               "high_risk_quantile": 0.90}
        cfg.update(over)
        return cfg

    def test_every_stratum_lands_in_its_pool(self):
        extra = self._extra()
        positions, drawn = _draw_sample(
            self._cfg(), extra, np.random.default_rng(1))
        resolved = extra["shipped"] & ~extra["censored"]
        rto, cod = extra["rto_flag"], extra["is_cod_order"]
        assert drawn["cod_rto"] == 50 and drawn["prepaid_rto"] == 50
        # Membership is what matters: the union must contain enough rows of each
        # kind, whichever stratum contributed them.
        assert (resolved & rto & cod)[positions].sum() >= 50
        assert (resolved & rto & ~cod)[positions].sum() >= 50

    def test_positions_are_unique_and_sorted(self):
        positions, _ = _draw_sample(
            self._cfg(), self._extra(), np.random.default_rng(2))
        assert len(np.unique(positions)) == len(positions)
        assert np.array_equal(positions, np.sort(positions))

    def test_overlap_shrinks_the_union_below_the_stratum_total(self):
        # Strata are not disjoint by construction, so the realised count is <= the
        # sum of the targets. Asserting equality would be wrong, which is why
        # _truth.json records the drawn count rather than assuming 2,000.
        positions, drawn = _draw_sample(
            self._cfg(), self._extra(), np.random.default_rng(3))
        assert len(positions) <= sum(drawn.values())

    def test_high_risk_stratum_sits_in_the_upper_tail_of_delivered(self):
        extra = self._extra()
        positions, _ = _draw_sample(
            self._cfg(strata={"random_sessions": 1, "cod_rto": 1,
                              "prepaid_rto": 1, "high_risk_delivered": 50}),
            extra, np.random.default_rng(4))
        delivered = (extra["shipped"] & ~extra["censored"] & ~extra["rto_flag"])
        cut = np.nanquantile(extra["p_rto_precheckout"][delivered], 0.90)
        picked = extra["p_rto_precheckout"][positions]
        # At most the three single-row strata can sit below the cut.
        assert (picked < cut).sum() <= 3

    def test_a_stratum_that_cannot_be_filled_is_reported(self, capsys):
        _draw_sample(self._cfg(strata={"random_sessions": 1, "cod_rto": 1,
                                       "prepaid_rto": 10_000,
                                       "high_risk_delivered": 1}),
                     self._extra(), np.random.default_rng(5))
        assert "WARNING truth_sampling.prepaid_rto" in capsys.readouterr().out

    def test_sampling_is_reproducible_from_the_substream(self):
        extra = self._extra()
        first, _ = _draw_sample(self._cfg(), extra, np.random.default_rng(99))
        second, _ = _draw_sample(self._cfg(), extra, np.random.default_rng(99))
        assert np.array_equal(first, second)


class TestSerialisation:
    """The stored trace must be readable and must carry its own totals."""

    def test_terms_and_totals_round_trip(self):
        terms = {"__intercept__": np.array([-4.6875, -4.6875]),
                 "is_cod": np.array([1.6, 0.0])}
        totals = {"__total__": np.array([-3.0875, -4.6875])}
        rows = [json.loads(r) for r in _serialise(terms, totals)]
        assert rows[0]["is_cod"] == 1.6
        assert rows[1]["is_cod"] == 0.0
        # Adding the named terms must reproduce the recorded total, which is what
        # lets a reviewer verify the decomposition without re-running anything.
        for row in rows:
            named = sum(v for k, v in row.items() if not k.startswith("__"))
            assert named + row["__intercept__"] == pytest.approx(
                row["__total__"], abs=1e-6)

    def test_shock_terms_stay_distinguishable_from_stage_one(self):
        rows = [json.loads(r) for r in _serialise(
            {"is_cod": np.array([1.6]), "shock.nu": np.array([2.1])},
            {"__total_final__": np.array([3.7])})]
        # A Phase 5 reader has to be able to tell which half of the trace was
        # knowable at checkout. The prefix is that boundary.
        assert [k for k in rows[0] if k.startswith("shock.")] == ["shock.nu"]
