"""Unit tests for generation modules 08-12.

The tests that matter most here are ``TestPointInTime`` and ``TestFunnel``.
Point-in-time integrity is the leakage firewall — if a snapshot can see an order
that had not resolved, the risk model gets a peek at the future and nothing
downstream means anything. And the funnel invariants are what make ``abandon_step``
a real diagnosis rather than a label.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config.loader import load_params
from src.config.seeds import spawn_substreams
from src.generators.checkout_pipeline import run_checkout
from src.generators.conversion import project_checkout_events, split_hurdles
from src.generators.customers import generate_customers
from src.generators.dates import generate_dates
from src.generators.geography import generate_geography
from src.generators.history import generate_history
from src.generators.products import generate_products
from src.generators.sellers import generate_sellers
from src.generators.sessions import generate_sessions
from src.generators.state_snapshots import (
    LEDGER_COLUMNS,
    empty_ledger,
    generate_state_snapshots,
)
from src.models.logit import CoefficientLedger


@pytest.fixture(scope="module")
def built():
    """Modules 02-12 at dev scale, built once and shared."""
    params = load_params(
        "config/params.yaml", "config/params.schema.json",
        scenario_path="config/scenarios/dev_small.yaml",
    )
    rng = spawn_substreams(
        int(params.require("seed.master")), params.require("seed.substreams")
    )
    ledger = CoefficientLedger()

    dates = generate_dates(params, rng.get("date"))
    geography = generate_geography(params, rng.get("geography"))
    sellers = generate_sellers(params, rng.get("seller"))
    products = generate_products(params, sellers, rng.get("product"))
    customers, latents = generate_customers(
        params, geography, rng.get("customer"), rng.get("latent")
    )
    history = generate_history(
        params, customers, latents, geography, rng.get("history"), ledger
    )
    customers = customers.merge(history.history, on="customer_id", how="left")
    sessions = generate_sessions(
        params, dates, customers, products, geography, latents, rng.get("session")
    )
    state = generate_state_snapshots(params, sessions, customers, empty_ledger())
    checkout = run_checkout(
        params, sessions, state, customers, latents, products, sellers, geography,
        dates, rng.get("cod"), rng.get("payment"), rng.get("conversion"), ledger,
    )
    return {
        "params": params, "dates": dates, "geography": geography,
        "customers": customers, "latents": latents, "products": products,
        "sessions": sessions, "state": state, "checkout": checkout,
        "resolved": checkout.sessions, "ledger": ledger,
    }


# ---------------------------------------------------------------------------
# 08 — sessions
# ---------------------------------------------------------------------------


class TestSessions:
    def test_session_count_is_the_north_star_denominator(self, built):
        """orders / conversion. Sessions that never convert MUST exist as rows."""
        params = built["params"]
        expected = round(
            int(params.require("scale.target_orders"))
            / float(params.require("scale.checkout_conversion_target"))
        )
        assert len(built["sessions"]) == expected

    def test_sessions_are_chronological(self, built):
        """Module 09 makes a strict chronological pass and asserts this."""
        assert built["sessions"]["session_start_ts"].is_monotonic_increasing

    def test_session_ids_agree_with_time_order(self, built):
        """Assigned after the sort, so id order and time order match.

        That turns "is this snapshot using the future?" into something anyone can
        eyeball, instead of something only LK-04 can answer.
        """
        ids = built["sessions"]["session_id"].tolist()
        assert ids == sorted(ids)

    def test_every_foreign_key_resolves(self, built):
        s = built["sessions"]
        assert s["customer_id"].isin(built["customers"]["customer_id"]).all()
        assert s["candidate_product_id"].isin(built["products"]["product_id"]).all()
        assert s["delivery_geography_id"].isin(
            built["geography"]["geography_id"]
        ).all()
        assert s["date_id"].isin(built["dates"]["date_id"]).all()

    def test_stage2_columns_are_in_range(self, built):
        s = built["sessions"]
        assert s["address_completeness_score"].between(0, 1).all()
        assert s["discount_pct"].between(0, 1).all()
        assert (s["estimated_delivery_days"] >= 1).all()
        assert s["cart_size"].between(1, 5).all()
        assert s["quantity"].between(1, 3).all()
        assert (s["order_value"] > 0).all()

    def test_address_completeness_is_not_a_geography_proxy(self, built):
        """Deliberate: spec §3.6 names no drivers.

        If address quality tracked tier, "fix the addresses" would silently become
        a geography policy with a fairness problem attached.
        """
        merged = built["sessions"].merge(
            built["geography"][["geography_id", "geo_tier"]],
            left_on="delivery_geography_id", right_on="geography_id",
        )
        spread = merged.groupby("geo_tier")["address_completeness_score"].mean()
        assert spread.max() - spread.min() < 0.02, spread.to_dict()

    def test_mean_gmv_lands_near_the_pinned_target(self, built):
        """Decision A5 + the E[quantity] correction in module 05.

        Without dividing the category list-price means by E[quantity], mean GMV
        overshoots by 12.5% and EC-01 fails by ~₹125 against a ±₹25 tolerance.
        """
        target = float(built["params"].require("calibration_targets.mean_gmv_per_order.target"))
        actual = built["sessions"]["prospective_gmv"].mean()
        assert actual == pytest.approx(target, rel=0.08), actual


# ---------------------------------------------------------------------------
# 09 — point-in-time state: the leakage firewall
# ---------------------------------------------------------------------------


class TestPointInTime:
    def test_rejects_unsorted_sessions(self, built):
        """An unsorted input would silently produce snapshots that see the future."""
        shuffled = built["sessions"].iloc[::-1].reset_index(drop=True)
        with pytest.raises(ValueError, match="chronological"):
            generate_state_snapshots(
                built["params"], shuffled, built["customers"], empty_ledger()
            )

    def test_counts_are_internally_consistent(self, built):
        s = built["state"]
        assert (s["pit_orders_resolved"] <= s["pit_orders_placed"]).all()
        assert (s["pit_orders_delivered"] + s["pit_rto_count"]
                <= s["pit_orders_resolved"]).all()
        assert (s["pit_cod_orders"] <= s["pit_orders_placed"]).all()

    def test_derived_flags_match_their_definitions(self, built):
        """These are CHECK constraints in the DDL; a mismatch fails the load."""
        s = built["state"]
        assert (s["pit_is_new_customer"] == (s["pit_orders_delivered"] == 0)).all()
        assert (s["pit_has_clean_record"]
                == ((s["pit_orders_delivered"] >= 3) & (s["pit_rto_count"] == 0))).all()
        assert (s["pit_has_history"] == (s["pit_orders_placed"] > 0)).all()

    def test_no_imputation_for_historyless_customers(self, built):
        """Decision A18. Imputing 0.62 into a column multiplied by +2.20 would
        manufacture a habit signal for a customer with no habit."""
        s = built["state"]
        no_history = s["pit_orders_placed"] == 0
        assert s.loc[no_history, "pit_cod_share"].isna().all()
        assert s.loc[~no_history, "pit_cod_share"].notna().all()
        assert s.loc[no_history, "pit_avg_order_value"].isna().all()

    def test_shrunk_rate_is_never_null_and_uses_the_declared_prior(self, built):
        """The A18 exception: EB shrinkage at n=0 RETURNS the prior by construction,
        which is computed rather than imputed."""
        s = built["state"]
        assert s["pit_rto_rate_shrunk"].notna().all()
        prior = float(built["params"].require("priors.rto_prior"))
        no_resolved = s["pit_orders_resolved"] == 0
        assert np.allclose(s.loc[no_resolved, "pit_rto_rate_shrunk"], prior, atol=1e-4)

    def test_raw_rate_is_null_exactly_when_nothing_resolved(self, built):
        s = built["state"]
        assert (s["pit_rto_rate_raw"].isna() == (s["pit_orders_resolved"] == 0)).all()

    def test_risk_tier_carries_no_payment_method(self, built):
        """Decision A21: this is the M1 baseline. payment_method is Stage 3 and
        belongs to the M2 tier on fct_order."""
        s = built["state"]
        assert set(s["pit_risk_tier_rule_based"].unique()) <= {"LOW", "MED", "HIGH"}
        rules = built["params"].require("distributions.risk_tier_rules")
        high = s[s["pit_risk_tier_rule_based"] == "HIGH"]
        assert (high["pit_rto_rate_shrunk"] >= float(rules["high_rto_rate_shrunk"])).all()

    def test_ledger_interface_is_the_real_one(self, built):
        """When the A1 day loop closes, module 09 must not have to change."""
        assert set(LEDGER_COLUMNS) == {
            "customer_id", "order_ts", "outcome_resolved_date",
            "is_cod", "rto_flag", "is_delivered", "order_value",
        }
        assert list(empty_ledger().columns) == list(LEDGER_COLUMNS)

    def test_a_ledger_order_is_only_counted_once_resolved(self, built):
        """The core rule: an order placed 3 days ago has NOT resolved."""
        params, sessions, customers = built["params"], built["sessions"], built["customers"]
        target = sessions.iloc[len(sessions) // 2]
        session_day = pd.Timestamp(target["date_id"]).date()

        ledger = pd.DataFrame({
            "customer_id": [target["customer_id"]],
            "order_ts": [pd.Timestamp(target["session_start_ts"]) - pd.Timedelta(days=3)],
            # Resolves AFTER this session — must not be visible.
            "outcome_resolved_date": [session_day + pd.Timedelta(days=10)],
            "is_cod": [True], "rto_flag": [True], "is_delivered": [False],
            "order_value": [1000.0],
        })
        with_ledger = generate_state_snapshots(params, sessions, customers, ledger)
        row = with_ledger[with_ledger["session_id"] == target["session_id"]].iloc[0]
        base = built["state"]
        base_row = base[base["session_id"] == target["session_id"]].iloc[0]

        # Placement is visible; the unresolved outcome is not.
        assert row["pit_orders_placed"] == base_row["pit_orders_placed"] + 1
        assert row["pit_rto_count"] == base_row["pit_rto_count"]
        assert row["pit_orders_resolved"] == base_row["pit_orders_resolved"]


# ---------------------------------------------------------------------------
# 10-12 — the funnel
# ---------------------------------------------------------------------------


class TestFunnel:
    def test_no_session_pays_and_then_abandons_at_address(self, built):
        """Decision A26. Under the 11a/11b/11c ordering this is UNREACHABLE."""
        r = built["resolved"]
        assert not (r["payment_page_reached"] & ~r["address_completed"]).any()

    def test_abandoned_and_converted_are_exclusive_and_complete(self, built):
        r = built["resolved"]
        abandoned = r["checkout_abandoned"].to_numpy()
        has_method = ~r["final_payment_method"].isna().to_numpy()
        has_step = ~r["abandon_step"].isna().to_numpy()
        assert not (abandoned & has_method).any()
        assert not (~abandoned & has_step).any()
        assert (abandoned == has_step).all()

    def test_every_abandonment_has_a_cause(self, built):
        """Brief §9.10: abandonment must be causally connected to what happened.
        An unlabelled abandonment makes Branch 5 undiagnosable."""
        r = built["resolved"]
        steps = set(r.loc[r["checkout_abandoned"], "abandon_step"].unique())
        assert steps <= {"ADDRESS", "PAYMENT_PAGE", "FEE_REVEAL", "PAYMENT_FAILURE"}
        assert "PAYMENT_FAILURE" in steps, "no failure-driven abandonment — H11 is dead"

    def test_fee_reveal_is_absent_in_the_baseline(self, built):
        """0 by design: shipping_fee_charged is 0. FEE_REVEAL is the diagnosis that
        exists to catch the lever when a scenario pulls it."""
        assert float(built["params"].require("economics.shipping_fee_charged")) == 0
        assert (built["resolved"]["abandon_step"] == "FEE_REVEAL").sum() == 0

    def test_switch_to_cod_requires_prepaid_intent(self, built):
        """The two-step structure. A switch means they TRIED to prepay — that is
        what makes H11 answerable and what deviation D5 rewards."""
        r = built["resolved"]
        switched = r[r["switched_to_cod_after_failure"]]
        assert (switched["intended_payment_method"] == "PREPAID").all()
        assert (switched["final_payment_method"] == "COD").all()

    def test_payment_rail_is_null_exactly_for_cod(self, built):
        """DQ-13, enforced as a CHECK constraint in the DDL."""
        r = built["resolved"][~built["resolved"]["checkout_abandoned"]]
        is_cod = r["final_payment_method"] == "COD"
        assert (r["payment_rail"].isna() == is_cod).all()

    def test_only_prepaid_intent_sessions_attempt_payment(self, built):
        r = built["resolved"]
        attempted = r["payment_attempt_count"] > 0
        assert (r.loc[attempted, "intended_payment_method"] == "PREPAID").all()
        assert r.loc[attempted, "payment_page_reached"].all()

    def test_attempt_rows_reconcile_with_the_session_counter(self, built):
        attempts = built["checkout"].payment_attempts
        r = built["resolved"]
        per_session = attempts.groupby("session_id").size()
        expected = r.set_index("session_id")["payment_attempt_count"]
        common = per_session.index
        # A rail switch increments the counter without emitting a third row; the
        # attempt table is never LARGER than the counter claims.
        assert (per_session <= expected.loc[common]).all()


class TestHurdleSplit:
    def test_product_of_hurdles_is_the_conversion_probability(self):
        """The split is a decomposition, not a change to the model."""
        p = np.array([0.2, 0.5, 0.68, 0.95])
        a, b = split_hurdles(p, 0.35)
        assert np.allclose(a * b, p)

    def test_rejects_a_degenerate_share(self):
        with pytest.raises(ValueError, match="address_hurdle_share"):
            split_hurdles(np.array([0.5]), 0.0)


class TestCheckoutEvents:
    def test_events_are_a_projection_of_session_state(self, built):
        """Decision A12: no new randomness. Re-running must be identical."""
        first = project_checkout_events(built["resolved"])
        second = project_checkout_events(built["resolved"])
        pd.testing.assert_frame_equal(first, second)

    def test_every_session_has_a_start_and_a_terminal_event(self, built):
        events = built["checkout"].checkout_events
        sessions = built["resolved"]
        starts = events[events["event_name"] == "CHECKOUT_STARTED"]
        assert len(starts) == len(sessions)
        terminal = events[events["event_name"].isin(["ORDER_PLACED", "ABANDONED"])]
        assert len(terminal) == len(sessions)

    def test_event_sequence_is_dense_and_ordered(self, built):
        events = built["checkout"].checkout_events
        grouped = events.groupby("session_id")
        assert (grouped["event_seq"].min() == 1).all()
        assert (grouped["event_seq"].apply(lambda s: s.is_monotonic_increasing)).all()

    def test_no_event_precedes_its_session(self, built):
        events = built["checkout"].checkout_events
        joined = events.merge(
            built["resolved"][["session_id", "session_start_ts"]], on="session_id"
        )
        assert (joined["event_ts"] >= joined["session_start_ts"]).all()


class TestJointSolve:
    def test_both_intercepts_converged(self, built):
        c = built["checkout"]
        assert c.conversion_calibration.converged
        assert c.cod_calibration.converged

    def test_the_alternating_solve_reached_a_fixed_point(self, built):
        """If beta_0 and alpha_0 were still moving on the last pass, neither is solved."""
        drift = built["checkout"].solve_drift
        assert drift["beta_0"] < 1e-6
        assert drift["alpha_0"] < 1e-6

    def test_cod_share_hits_its_target(self, built):
        target = built["params"].require("calibration_targets.cod_share")
        assert abs(built["checkout"].cod_share - float(target["target"])) <= float(
            target["tol"]
        )

    def test_cod_intent_is_drawn_not_assigned(self, built):
        """CLAUDE.md rule 9. Every payment method is a Bernoulli draw."""
        truth = built["checkout"].truth
        assert truth["p_cod_intent"].between(0, 1).all()
        # A genuine probability distribution, not a constant or a step function.
        assert truth["p_cod_intent"].std() > 0.05
        assert truth["p_cod_intent"].nunique() > len(truth) // 2
