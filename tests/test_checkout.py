"""Unit tests for generation modules 08-17, through the decision-A1 day loop.

The tests that matter most are ``TestPointInTime`` (the leakage firewall),
``TestFunnel`` (abandon_step as a real diagnosis) and ``TestRtoOutcomes`` (the
denominator and the censoring contract). Everything downstream of those is
arithmetic; those three are where a silent error would be invisible and fatal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config.loader import load_params
from src.config.seeds import spawn_substreams
from src.generators import materialise
from src.generators.conversion import project_checkout_events, split_hurdles
from src.generators.customers import generate_customers
from src.generators.dates import generate_dates
from src.generators.geography import generate_geography
from src.generators.history import generate_history
from src.generators.orders import draw_cancellations, m2_risk_tier
from src.generators.products import generate_products
from src.generators.sellers import generate_sellers
from src.generators.sessions import generate_sessions
from src.generators.simulate import prepare, simulate_window
from src.models.logit import CoefficientLedger
from src.validation.tests_cal import cal_09_no_slope_changed

# Intercepts near the solved values, so the fixture is representative without
# paying for a full joint solve in every test session.
ALPHA, BETA, GAMMA = 0.25, -0.25, -4.125


@pytest.fixture(scope="module")
def built():
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
    rngs = {k: rng.get(k) for k in ("cod", "payment", "conversion", "rto", "delivery")}
    setup = prepare(params, sessions, customers, latents, products, sellers,
                    geography, dates, rngs, ledger)
    metrics = simulate_window(setup, ALPHA, BETA, GAMMA, collect=True)
    extra = metrics.extra

    resolved = materialise.resolve_sessions(params, sessions, extra)
    resolved["signup_date"] = customers.set_index("customer_id")["signup_date"]\
        .reindex(resolved["customer_id"]).to_numpy()
    state = materialise.build_state(params, resolved, extra, dates)
    orders = materialise.build_orders(params, resolved, extra, dates, products)

    return {
        "params": params, "setup": setup, "metrics": metrics, "extra": extra,
        "sessions": sessions, "resolved": resolved, "state": state,
        "orders": orders, "customers": customers, "geography": geography,
        "products": products, "ledger": ledger, "dates": dates,
    }


class TestSessions:
    def test_session_count_is_the_input_knob(self, built):
        """Decision A31: read from scale.n_sessions, not derived from conversion."""
        assert len(built["sessions"]) == int(built["params"].require("scale.n_sessions"))

    def test_sessions_are_chronological(self, built):
        assert built["sessions"]["session_start_ts"].is_monotonic_increasing

    def test_stage2_columns_are_in_range(self, built):
        s = built["sessions"]
        assert s["address_completeness_score"].between(0, 1).all()
        assert s["discount_pct"].between(0, 1).all()
        assert (s["estimated_delivery_days"] >= 1).all()
        assert s["quantity"].between(1, 3).all()

    def test_address_completeness_is_not_a_geography_proxy(self, built):
        """Decision A28(a) / limitation L1 — a STATED property, so it is tested."""
        merged = built["sessions"].merge(
            built["geography"][["geography_id", "geo_tier"]],
            left_on="delivery_geography_id", right_on="geography_id",
        )
        spread = merged.groupby("geo_tier")["address_completeness_score"].mean()
        assert spread.max() - spread.min() < 0.02, spread.to_dict()

    def test_daily_volume_follows_the_demand_index(self, built):
        """Decision A28(b): uniform ACROSS CUSTOMERS, never across days.

        BR-10 needs month-end to be a real concentration of traffic and DQ-14
        needs orders to pile up late in the window. Flat daily volume breaks both.
        """
        per_day = built["sessions"].groupby("date_id").size()
        demand = built["dates"].set_index("date_id")["demand_index"].reindex(per_day.index)
        assert float(np.corrcoef(per_day.to_numpy(float), demand.to_numpy(float))[0, 1]) > 0.9


class TestPointInTime:
    def test_counts_are_internally_consistent(self, built):
        s = built["state"]
        assert (s["pit_orders_resolved"] <= s["pit_orders_placed"]).all()
        assert (s["pit_orders_delivered"] + s["pit_rto_count"]
                <= s["pit_orders_resolved"]).all()
        assert (s["pit_cod_orders"] <= s["pit_orders_placed"]).all()

    def test_no_imputation_for_historyless_customers(self, built):
        """Decision A18. pit_cod_share carries +2.20; imputing would manufacture
        a habit signal for a customer who has none."""
        s = built["state"]
        no_history = s["pit_orders_placed"] == 0
        assert s.loc[no_history, "pit_cod_share"].isna().all()
        assert s.loc[~no_history, "pit_cod_share"].notna().all()
        assert (s["pit_has_history"] == (s["pit_orders_placed"] > 0)).all()

    def test_shrunk_rate_never_null_and_uses_the_declared_prior(self, built):
        """The A18 exception: EB shrinkage at n=0 RETURNS the prior by construction."""
        s = built["state"]
        assert s["pit_rto_rate_shrunk"].notna().all()
        prior = float(built["params"].require("priors.rto_prior"))
        none = s["pit_orders_resolved"] == 0
        assert np.allclose(s.loc[none, "pit_rto_rate_shrunk"], prior, atol=1e-3)

    def test_history_accumulates_inside_the_window(self, built):
        """If it did not, the A1 day loop would be pointless and H3/BR-02/BR-03
        would have nothing to detect."""
        s = built["state"].copy()
        s["day"] = built["setup"]["index"]["day_index"]
        early = s[s["day"] < 15]["pit_orders_placed"].mean()
        late = s[s["day"] >= 75]["pit_orders_placed"].mean()
        assert late > early, (early, late)

    def test_risk_tier_carries_no_payment_method(self, built):
        """Decision A21: this is the M1 baseline."""
        assert set(built["state"]["pit_risk_tier_rule_based"].unique()) <= {
            "LOW", "MED", "HIGH"
        }


class TestFunnel:
    def test_no_session_pays_and_then_abandons_at_address(self, built):
        """Decision A26 makes this unreachable, not merely unviolated."""
        r = built["resolved"]
        assert not (r["payment_page_reached"] & ~r["address_completed"]).any()

    def test_abandoned_and_converted_are_exclusive_and_complete(self, built):
        r = built["resolved"]
        abandoned = r["checkout_abandoned"].to_numpy()
        assert not (abandoned & ~r["final_payment_method"].isna().to_numpy()).any()
        assert (abandoned == ~r["abandon_step"].isna().to_numpy()).all()

    def test_every_abandonment_has_a_cause(self, built):
        r = built["resolved"]
        steps = set(r.loc[r["checkout_abandoned"], "abandon_step"].dropna().unique())
        assert steps <= {"ADDRESS", "PAYMENT_PAGE", "FEE_REVEAL", "PAYMENT_FAILURE"}
        assert "PAYMENT_FAILURE" in steps, "no failure-driven abandonment — H11 is dead"

    def test_fee_reveal_absent_in_the_baseline(self, built):
        """0 by design: shipping_fee_charged is 0, so the fee term is identically zero."""
        assert float(built["params"].require("economics.shipping_fee_charged")) == 0
        assert (built["resolved"]["abandon_step"] == "FEE_REVEAL").sum() == 0

    def test_switch_to_cod_requires_prepaid_intent(self, built):
        r = built["resolved"]
        switched = r[r["switched_to_cod_after_failure"]]
        assert (switched["intended_payment_method"] == "PREPAID").all()
        assert (switched["final_payment_method"] == "COD").all()

    def test_payment_rail_is_null_exactly_for_cod(self, built):
        """DQ-13, enforced as a CHECK constraint in the DDL."""
        r = built["resolved"][~built["resolved"]["checkout_abandoned"]]
        assert (r["payment_rail"].isna() == (r["final_payment_method"] == "COD")).all()


class TestOrders:
    def test_one_order_per_converted_session(self, built):
        orders, resolved = built["orders"], built["resolved"]
        assert len(orders) == int((~resolved["checkout_abandoned"]).sum())
        assert orders["session_id"].is_unique
        assert orders["order_id"].is_unique

    def test_gmv_identity_holds(self, built):
        o = built["orders"]
        assert np.allclose(o["order_value"], o["gmv"] * (1 - o["discount_pct"]), atol=0.02)

    def test_cancelled_orders_never_ship_and_never_rto(self, built):
        """DQ-09. is_shipped is the RTO denominator (CLAUDE.md invariant 8)."""
        o = built["orders"]
        c = o[o["is_cancelled_preship"]]
        assert not c["is_shipped"].any()
        assert not (c["rto_flag"] == True).any()  # noqa: E712
        assert c["cancel_actor"].notna().all()

    def test_cancellation_actor_is_null_when_not_cancelled(self, built):
        o = built["orders"]
        assert o.loc[~o["is_cancelled_preship"], "cancel_actor"].isna().all()

    def test_m2_tier_escalates_on_cod(self, built):
        """Decision A21: payment method is the third rule, and belongs to M2 only."""
        rules = built["params"].require("distributions.risk_tier_rules")
        shrunk = np.array([0.05, 0.05, 0.30, 0.30])
        new = np.array([False, False, False, False])
        cod = np.array([False, True, False, True])
        tiers = m2_risk_tier(shrunk, new, cod, rules)
        assert tiers[0] == "LOW" and tiers[1] == "MED"
        assert tiers[2] == "HIGH" and tiers[3] == "HIGH"

    def test_cancellation_rate_matches_the_declared_total(self, built):
        rates = built["params"].require("fulfilment.preship_cancel_rate")
        total = sum(float(v) for v in rates.values())
        # Independent blocks: whether an order cancels and who cancelled it are
        # separate draws. Any deterministic relationship between the two — even
        # an inverse one — collapses the actor mix onto a single value.
        rng = np.random.default_rng(11)
        is_cancelled, actor = draw_cancellations(
            50_000, rng.random(50_000), rng.random(50_000), rates
        )
        assert abs(is_cancelled.mean() - total) < 0.005
        assert set(actor[is_cancelled]) == {"CUSTOMER", "SELLER", "SYSTEM"}

    def test_all_three_cancel_actors_appear_in_the_data(self, built):
        """The generator uses separate uniform blocks for cancel and actor, so
        the actor mix must not collapse."""
        actors = built["orders"].loc[
            built["orders"]["is_cancelled_preship"], "cancel_actor"
        ]
        assert set(actors.dropna().unique()) == {"CUSTOMER", "SELLER", "SYSTEM"}


class TestRtoOutcomes:
    def test_rto_implies_shipped_and_not_delivered(self, built):
        """DQ-08."""
        o = built["orders"]
        rto = o[o["rto_flag"] == True]  # noqa: E712
        assert rto["is_shipped"].all()
        assert not (rto["is_delivered"] == True).any()  # noqa: E712

    def test_censored_orders_carry_no_outcome(self, built):
        """Decision A10 — and the DDL enforces the same thing as a CHECK."""
        o = built["orders"]
        c = o[o["is_censored"]]
        assert len(c) > 0, "no censoring at all — DQ-14 could not be demonstrated"
        for column in ("rto_flag", "is_delivered", "outcome_resolved_date"):
            assert c[column].isna().all(), column

    def test_shipped_uncensored_orders_all_have_an_outcome(self, built):
        """What makes 'shipped AND NOT censored' a COMPLETE RTO denominator."""
        o = built["orders"]
        observable = o[o["is_shipped"] & ~o["is_censored"]]
        assert observable["rto_flag"].notna().all()
        assert observable["is_delivered"].notna().all()

    def test_delivery_delay_days_is_null_on_every_rto(self, built):
        """Decision A8: this is the DIAGNOSTIC column. The parcel never arrived,
        so there is no actual delivery date to subtract from."""
        o = built["orders"]
        assert o.loc[o["rto_flag"] == True, "delivery_delay_days"].isna().all()  # noqa: E712

    def test_resolution_days_respect_the_specified_window(self, built):
        o = built["orders"]
        bounds = built["params"].require("fulfilment.outcome_resolution_days")
        actual = o["actual_delivery_days"].dropna()
        assert actual.between(int(bounds["lo"]), int(bounds["hi"])).all()

    def test_cod_orders_rto_more_than_prepaid(self, built):
        """The planted +1.60. Not an assignment — the gap is emergent."""
        m = built["metrics"]
        assert m.rto_rate_cod > m.rto_rate_prepaid

    def test_rto_is_drawn_not_assigned(self, built):
        """CLAUDE.md rule 9. Some COD orders must be delivered and some prepaid
        orders must fail, or a rule has replaced a probability."""
        o = built["orders"][built["orders"]["is_shipped"] & ~built["orders"]["is_censored"]]
        cod = o[o["payment_method"] == "COD"]
        prepaid = o[o["payment_method"] == "PREPAID"]
        assert 0 < cod["rto_flag"].mean() < 1
        assert 0 < prepaid["rto_flag"].mean() < 1


class TestDeterminism:
    def test_the_loop_consumes_no_randomness(self, built):
        """The property bisection depends on: same intercepts -> same result."""
        a = simulate_window(built["setup"], ALPHA, BETA, GAMMA)
        b = simulate_window(built["setup"], ALPHA, BETA, GAMMA)
        assert (a.conversion_rate, a.cod_share, a.rto_rate_blended) == (
            b.conversion_rate, b.cod_share, b.rto_rate_blended
        )

    def test_rto_rate_is_monotone_in_gamma(self, built):
        """If it were not, the bisection would be solving a non-monotone target."""
        rates = [simulate_window(built["setup"], ALPHA, BETA, g).rto_rate_blended
                 for g in (-5.0, -4.5, -4.0, -3.5)]
        assert rates == sorted(rates), rates

    def test_placements_are_exact_not_day_batched(self, built):
        """Rank groups within a day. If ranks collapsed to one batch, a customer's
        second session of the day would not see their first order."""
        assert built["setup"]["index"]["max_rank"] >= 2
        assert 0 < built["setup"]["index"]["multi_session_share"] < 0.10


class TestGuards:
    def test_cal_09_covers_the_stage2_deltas(self, built):
        """post_dispatch_shock lives outside the coefficients block, so without
        this it would be the one place a slope could move unnoticed."""
        result = cal_09_no_slope_changed(
            built["ledger"], built["params"], require_complete_coverage=False
        )
        assert result.status.value == "PASS", result.detail
        assert "shock.attempt_delay_days" in built["ledger"].slopes("rto_model")

    def test_hurdle_split_is_exact(self):
        p = np.array([0.2, 0.5, 0.68, 0.95])
        a, b = split_hurdles(p, 0.35)
        assert np.allclose(a * b, p)

    def test_checkout_events_are_a_deterministic_projection(self, built):
        """Decision A12: no new randomness, so re-running must be identical."""
        first = project_checkout_events(built["resolved"])
        second = project_checkout_events(built["resolved"])
        pd.testing.assert_frame_equal(first, second)
        assert (first.groupby("session_id")["event_seq"].min() == 1).all()
