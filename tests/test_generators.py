"""Unit tests for generator modules 02-07.

These test the GENERATOR CODE, not the generated dataset. Dataset-level checks
live in ``src/validation/`` and run against the full artefact (brief §5).

The tests that matter most here are the ones in ``TestHistoryConstraints`` and
``TestConfounding``. Brief §9.5 says of the pre-window consistency constraints:
"Write a unit test for each constraint." Everything downstream — H3, BR-02,
BR-03, the whole naive-vs-adjusted finding — is built on that history being both
coherent and latent-driven.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.config.loader import load_params
from src.config.seeds import spawn_substreams
from src.generators.customers import build_correlation_matrix, generate_customers
from src.generators.dates import generate_dates
from src.generators.geography import generate_geography
from src.generators.history import assert_history_constraints, generate_history
from src.generators.products import generate_products
from src.generators.sellers import generate_sellers
from src.models.calibrate import CalibrationError, scaled_tolerance, solve_intercept
from src.models.logit import CoefficientLedger, logistic

PARAMS_PATH = "config/params.yaml"
SCHEMA_PATH = "config/params.schema.json"
DEV_SCENARIO = "config/scenarios/dev_small.yaml"


@pytest.fixture(scope="module")
def params():
    return load_params(PARAMS_PATH, SCHEMA_PATH, scenario_path=DEV_SCENARIO)


@pytest.fixture(scope="module")
def rng(params):
    return spawn_substreams(
        int(params.require("seed.master")), params.require("seed.substreams")
    )


@pytest.fixture(scope="module")
def built(params, rng):
    """Modules 02-07 at dev scale, built once and shared."""
    dates = generate_dates(params, rng.get("date"))
    geography = generate_geography(params, rng.get("geography"))
    sellers = generate_sellers(params, rng.get("seller"))
    products = generate_products(params, sellers, rng.get("product"))
    customers, latents = generate_customers(
        params, geography, rng.get("customer"), rng.get("latent")
    )
    ledger = CoefficientLedger()
    history = generate_history(
        params, customers, latents, geography, rng.get("history"), ledger
    )
    merged = customers.merge(history.history, on="customer_id").merge(
        latents, on="customer_id"
    )
    return {
        "dates": dates, "geography": geography, "sellers": sellers,
        "products": products, "customers": customers, "latents": latents,
        "history": history, "merged": merged, "ledger": ledger,
    }


# ---------------------------------------------------------------------------
# 02 — dates
# ---------------------------------------------------------------------------


class TestDates:
    def test_window_length_and_index(self, built, params):
        dates = built["dates"]
        assert len(dates) == int(params.require("meta.window_days"))
        assert dates["day_index"].tolist() == list(range(len(dates)))

    def test_month_end_flag_matches_spec_definition(self, built):
        """is_month_end_window drives the +0.30 month-end x COD RTO interaction.

        An off-by-one here would silently weaken BR-10.
        """
        dates = built["dates"]
        assert (dates["is_month_end_window"] == (dates["day_of_month"] >= 26)).all()
        assert (dates["is_salary_week"] == (dates["day_of_month"] <= 7)).all()

    def test_demand_index_is_always_positive(self, built):
        assert (built["dates"]["demand_index"] > 0).all()

    def test_weekend_flag_agrees_with_weekday(self, built):
        dates = built["dates"]
        assert (dates["is_weekend"] == (dates["day_of_week"] >= 6)).all()


# ---------------------------------------------------------------------------
# 03 — geography
# ---------------------------------------------------------------------------


class TestGeography:
    def test_every_tier_has_fairness_audit_power(self, built):
        """Spec §15: >=100 clusters per tier, or the §8.4 audit is unreportable."""
        counts = built["geography"]["geo_tier"].value_counts()
        assert counts.min() >= 100, counts.to_dict()

    def test_scores_are_within_bounds(self, built):
        geo = built["geography"]
        for column in ("serviceability_score", "courier_reliability_score",
                       "cod_cultural_index"):
            assert geo[column].between(0, 1).all(), column

    def test_serviceability_falls_with_tier(self, built):
        """The ACCESS channel is monotone in tier — courier networks really are thinner."""
        means = built["geography"].groupby("geo_tier")["serviceability_score"].mean()
        assert means["METRO"] > means["TIER1"] > means["TIER2"] > means["TIER3"]

    def test_cod_culture_is_not_a_tier_proxy(self, built):
        """The NORMS channel must NOT track the access channel.

        If both were monotone in tier, geography would collapse into a single
        latent and the analysis could conclude "Tier-3 = low trust" for free —
        exactly the lazy read blueprint §1.5 warns against.
        """
        means = built["geography"].groupby("geo_tier")["cod_cultural_index"].mean()
        assert means["TIER1"] > means["METRO"]
        assert means["TIER2"] > means["TIER3"]


# ---------------------------------------------------------------------------
# 04 / 05 — sellers and products
# ---------------------------------------------------------------------------


class TestSellersAndProducts:
    def test_ratings_are_in_range(self, built):
        assert built["sellers"]["seller_rating"].between(1.0, 5.0).all()
        assert built["products"]["product_rating"].between(1.0, 5.0).all()

    def test_seller_tier_requires_both_bars(self, built, params):
        """A high rating alone must not buy GOLD — otherwise tier is a rating alias."""
        thresholds = params.require("distributions.seller.tier_thresholds")
        gold = built["sellers"][built["sellers"]["seller_tier"] == "GOLD"]
        assert (gold["seller_rating"] >= thresholds["GOLD"]["min_rating"]).all()
        assert (gold["seller_sla_breach_rate"]
                <= thresholds["GOLD"]["max_sla_breach"]).all()

    def test_every_product_has_a_real_seller(self, built):
        assert built["products"]["seller_id"].isin(built["sellers"]["seller_id"]).all()

    def test_list_price_mean_is_pinned_not_the_median(self, built, params):
        """The lognormal mu correction, which EC-01 depends on.

        Setting mu = log(mean) pins the MEDIAN and overshoots the mean by
        exp(sigma^2/2) — 32% at Electronics' sigma. Solving mu backwards is what
        keeps mean GMV near ₹1,000 instead of ~₹1,300.
        """
        products = built["products"]
        weights = params.require("distributions.category_weights")
        means = params.require("distributions.category_mean_gmv")
        expected = sum(float(weights[c]) * float(means[c]) for c in weights)

        actual = sum(
            float(weights[c]) * products.loc[products["category"] == c, "list_price"].mean()
            for c in weights
        )
        # Generous band: this is per-product list price at dev scale, not the
        # order-weighted GMV that EC-01 tests. It catches the mu bug (a ~30%
        # error) without pretending to be a calibration test.
        assert actual == pytest.approx(expected, rel=0.15), (actual, expected)

    def test_category_shrink_rate_is_mapped_not_drawn(self, built, params):
        declared = params.require("economics.shrink_rate_by_category")
        for category, rate in declared.items():
            subset = built["products"][built["products"]["category"] == category]
            if len(subset):
                assert np.allclose(subset["shrink_rate"], float(rate)), category


# ---------------------------------------------------------------------------
# 06 — latents
# ---------------------------------------------------------------------------


class TestLatents:
    def test_correlation_matrix_is_positive_definite(self, params):
        corr = build_correlation_matrix(params.require("latents"))
        assert np.linalg.eigvalsh(corr).min() > 0

    def test_correlation_matrix_rejects_inconsistent_inputs(self):
        """An impossible correlation set must fail loudly, not degrade silently."""
        broken = {
            "correlations": {
                "trust_liquidity": 0.99,
                "intent_liquidity": -0.99,
                "intent_trust": 0.99,
            },
            "price_sensitivity_liquidity_corr": -0.3,
        }
        with pytest.raises(ValueError, match="positive definite"):
            build_correlation_matrix(broken)

    def test_latents_are_z_scored(self, built):
        """Every latent coefficient in §7.2/§8.2 is stated per standard deviation.

        Storing un-standardised values would silently rescale all of them.
        """
        latents = built["latents"]
        for column in ("latent_trust", "latent_liquidity", "latent_intent",
                       "latent_price_sensitivity"):
            assert latents[column].mean() == pytest.approx(0.0, abs=0.01), column
            assert latents[column].std(ddof=0) == pytest.approx(1.0, abs=0.01), column

    def test_latent_correlations_match_the_specification(self, built, params):
        declared = params.require("latents.correlations")
        latents = built["latents"]

        def r(a, b):
            return float(np.corrcoef(latents[f"latent_{a}"], latents[f"latent_{b}"])[0, 1])

        # Liquidity is shifted by geo tier after drawing, so its correlations move
        # a little. Trust-vs-intent is untouched and should land close.
        assert r("trust", "intent") == pytest.approx(
            float(declared["intent_trust"]), abs=0.05
        )
        assert np.sign(r("trust", "liquidity")) == np.sign(float(declared["trust_liquidity"]))
        assert np.sign(r("intent", "liquidity")) == np.sign(float(declared["intent_liquidity"]))

    def test_liquidity_tracks_geography(self, built):
        """Spec §6.1: liquidity correlates with tier. Metro should sit highest."""
        merged = built["merged"].merge(
            built["geography"][["geography_id", "geo_tier"]],
            left_on="home_geography_id", right_on="geography_id",
        )
        means = merged.groupby("geo_tier")["latent_liquidity"].mean()
        assert means["METRO"] > means["TIER3"]


# ---------------------------------------------------------------------------
# 07 — history: brief §9.5 says one test per constraint
# ---------------------------------------------------------------------------


class TestHistoryConstraints:
    def test_delivered_plus_rto_within_orders(self, built):
        h = built["history"].history
        assert (h["pre_window_delivered"] + h["pre_window_rto_count"]
                <= h["pre_window_orders"]).all()

    def test_cod_orders_within_orders(self, built):
        h = built["history"].history
        assert (h["pre_window_cod_orders"] <= h["pre_window_orders"]).all()

    def test_prepaid_success_within_prepaid_orders(self, built):
        h = built["history"].history
        assert (h["pre_window_prepaid_success"]
                <= h["pre_window_orders"] - h["pre_window_cod_orders"]).all()

    def test_orders_capped_by_tenure(self, built, params):
        """A 30-day-old account with 14 prior orders is an obvious tell."""
        gap = int(params.require("distributions.pre_window.min_days_between_orders"))
        merged = built["merged"]
        assert (merged["pre_window_orders"]
                <= merged["tenure_days_at_window_start"] // gap).all()

    def test_zero_inflation_produces_historyless_customers(self, built):
        """These customers exercise the decision-A18 NULL path. Without them it is dead code."""
        h = built["history"].history
        assert (h["pre_window_orders"] == 0).sum() > 0

    def test_all_counts_non_negative(self, built):
        h = built["history"].history
        assert (h.drop(columns=["customer_id"]) >= 0).all().all()

    def test_constraint_checker_catches_a_violation(self):
        """The checker must actually fire — a green test on a broken invariant is worse
        than no test."""
        import pandas as pd

        broken = pd.DataFrame({
            "customer_id": ["CUS_0000000", "CUS_0000001"],
            "pre_window_orders": [2, 0],
            "pre_window_delivered": [2, 0],
            "pre_window_rto_count": [1, 0],     # 2 + 1 > 2
            "pre_window_cod_orders": [1, 0],
            "pre_window_prepaid_success": [1, 0],
            "pre_window_payment_failures": [0, 0],
        })
        with pytest.raises(ValueError, match="delivered \\+ rto <= orders"):
            assert_history_constraints(broken)


class TestConfounding:
    """The whole project rests on these signs. Decision A11."""

    @pytest.mark.parametrize(
        "latent,column,expected_sign",
        [
            ("latent_trust", "cod", -1),
            ("latent_liquidity", "cod", -1),
            ("latent_intent", "cod", +1),
            ("latent_intent", "rto", +1),
            ("latent_liquidity", "rto", -1),
            ("latent_trust", "rto", -1),
        ],
    )
    def test_latent_drives_history_in_the_planted_direction(
        self, built, latent, column, expected_sign
    ):
        merged = built["merged"]
        sub = merged[merged["pre_window_orders"] > 0]
        numerator = (
            "pre_window_cod_orders" if column == "cod" else "pre_window_rto_count"
        )
        rate = sub[numerator] / sub["pre_window_orders"]
        r = float(np.corrcoef(sub[latent], rate)[0, 1])
        assert np.sign(r) == expected_sign, f"{latent} vs {column}: r={r:+.4f}"

    def test_prior_cod_and_prior_rto_are_linked(self, built):
        """is_cod enters the pre-window RTO logit at the same +1.60 as in-window.

        This is what makes BR-02 and BR-03 detectable later.
        """
        merged = built["merged"]
        sub = merged[merged["pre_window_orders"] > 0]
        cod_rate = sub["pre_window_cod_orders"] / sub["pre_window_orders"]
        rto_rate = sub["pre_window_rto_count"] / sub["pre_window_orders"]
        assert float(np.corrcoef(cod_rate, rto_rate)[0, 1]) > 0

    def test_reused_slopes_are_recorded_against_the_parent_block(self, built, params):
        """Decision A11 re-uses approved slopes; the ledger must see one value each."""
        ledger = built["ledger"]
        declared = params.require("rto_model.coefficients")
        assert ledger.slopes("rto_model")["is_cod"] == float(declared["is_cod"])
        assert ledger.slopes("pre_window_rto_model") == {}


class TestPreWindowCalibration:
    def test_both_intercepts_converged(self, built):
        assert built["history"].cod_calibration.converged
        assert built["history"].rto_calibration.converged

    def test_realised_shares_land_on_target(self, built, params):
        result = built["history"]
        cod_target = params.require("calibration_targets.pre_window_cod_share")
        rto_target = params.require("calibration_targets.pre_window_rto_rate")
        assert abs(result.realised_cod_share - float(cod_target["target"])) <= float(
            cod_target["tol"]
        )
        assert abs(result.realised_rto_rate - float(rto_target["target"])) <= float(
            rto_target["tol"]
        )


# ---------------------------------------------------------------------------
# calibration solver
# ---------------------------------------------------------------------------


class TestCalibrate:
    def test_solves_a_monotone_objective(self):
        result = solve_intercept(
            lambda x: float(logistic(np.array([x]))[0]),
            block="test", target=0.62, tolerance=0.001, bracket=(-2.0, 2.0),
        )
        assert result.converged
        assert result.achieved == pytest.approx(0.62, abs=0.001)

    def test_widens_a_too_narrow_bracket(self):
        """A narrow bracket is a config annoyance, not a finding about the assumptions.

        It must not masquerade as one — the 'unreachable' error is reserved for a
        genuinely impossible target.
        """
        result = solve_intercept(
            lambda x: float(logistic(np.array([x]))[0]),
            block="test", target=0.98, tolerance=0.002, bracket=(-0.5, 0.5),
        )
        assert result.converged

    def test_rejects_a_non_monotone_objective(self):
        """The symptom of resampling randomness between iterations."""
        with pytest.raises(CalibrationError, match="DECREASES"):
            solve_intercept(
                lambda x: float(logistic(np.array([-x]))[0]),
                block="test", target=0.5, tolerance=0.01, bracket=(-2.0, 2.0),
            )

    def test_unreachable_target_says_escalate_not_tune(self):
        with pytest.raises(CalibrationError, match="do NOT move"):
            solve_intercept(
                lambda x: 0.3, block="test", target=0.9,
                tolerance=0.001, bracket=(-1.0, 1.0),
            )

    def test_tolerance_widens_at_small_n(self):
        """Decision A13: ±0.004 is finer than one order at dev scale."""
        assert scaled_tolerance(100_000, 0.004, 1.5) == pytest.approx(0.00474, abs=1e-4)
        assert scaled_tolerance(5_000, 0.004, 1.5) == pytest.approx(0.02121, abs=1e-4)
        assert scaled_tolerance(10_000_000, 0.004, 1.5) == 0.004  # floor holds
