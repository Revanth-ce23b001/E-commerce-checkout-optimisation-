"""Unit tests for the M2 / policy code added under decision A47.

Same rule as ``test_risk_m1.py``: this tests the CODE, not the generated data.
What is worth testing here is everything that would fail *silently* —

* a per-tier threshold that does not actually equalise, so FA-01 passes for the
  wrong reason;
* a margin-cost sign error, which would report the price of fairness as a saving;
* a rules baseline reconstruction that drifts from the planted definition;
* a challenger ship rule that rounds its way past the 3pp margin;
* an FA-01 that reads a stale published result and calls it a pass.

The last one is the important one. Every other failure here shows up as a wrong
number a reader might question. A stale FA-01 shows up as a green light.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.risk import baseline, challenger, interventions, policy


# The fixture deliberately makes the tiers NON-OVERLAPPING in score and makes
# risk MONOTONE in score. Both matter:
#
#   non-overlapping  -> a global threshold restricts whole tiers, which is the
#                       A47 failure in miniature and makes the two rules
#                       maximally different;
#   monotone risk    -> contribution margin is a decreasing function of score,
#                       so "global top-k maximises captured value" is a theorem
#                       about top-k selection rather than an artefact, and a
#                       sign error in margin_cost cannot hide behind noise.
#
# An earlier draft of this fixture had risk running INVERSE to score, which made
# restricting the top-scored orders lose money and turned two real assertions
# into false failures. Recorded because it is the trap in any policy test:
# the fixture has to encode the direction the policy assumes.
RTO_SCORE_CUT = 0.5


def frame(n_per_tier: int = 50) -> pd.DataFrame:
    """Four tiers occupying disjoint score bands, risk rising with score."""
    rows = []
    band = 1.0 / len(policy.TIERS)
    for offset, tier in enumerate(policy.TIERS):
        for i in range(n_per_tier):
            score = offset * band + (i / n_per_tier) * band
            rows.append({
                "geo_tier": tier,
                "score": score,
                "rto_flag": score >= RTO_SCORE_CUT,
                "payment_method": "COD" if i % 2 else "PREPAID",
                "pit_has_clean_record": False,
                "pit_has_history": True,
            })
    return pd.DataFrame(rows)


def margins(f: pd.DataFrame) -> np.ndarray:
    """Realised contribution margin: negative on an RTO, positive on a delivery."""
    return np.where(f["rto_flag"], -300.0, 110.0)


# --- the two thresholding rules --------------------------------------------


def test_global_threshold_concentrates_on_the_worst_tier():
    """The failure A47 was raised about, reproduced in miniature."""
    f = frame()
    flags = policy.global_flags(f["score"].to_numpy(), 0.25)
    rate = pd.Series(flags).groupby(f["geo_tier"]).mean()
    assert rate["TIER3"] == 1.0
    assert rate["METRO"] == 0.0


def test_per_tier_threshold_equalises_exactly():
    """Every tier restricted at the target rate. This is the whole mechanism."""
    f = frame()
    flags = policy.per_tier_flags(f["score"].to_numpy(), f["geo_tier"], 0.20)
    rate = pd.Series(flags).groupby(f["geo_tier"]).mean()
    assert rate.nunique() == 1
    assert rate.iloc[0] == pytest.approx(0.20, abs=0.01)


def test_per_tier_threshold_picks_the_riskiest_within_each_tier():
    """Equalising rates must not mean picking at random inside a tier."""
    f = frame()
    flags = policy.per_tier_flags(f["score"].to_numpy(), f["geo_tier"], 0.20)
    for tier in policy.TIERS:
        block = f[f["geo_tier"] == tier]
        chosen = block[flags[block.index]]
        rejected = block[~flags[block.index]]
        assert chosen["score"].min() > rejected["score"].max()


def test_per_tier_flags_is_insensitive_to_row_order():
    """The index bookkeeping inside per_tier_flags is where an off-by-one would
    hide, and it would hide as a *slightly* wrong rate, not a crash."""
    f = frame()
    shuffled = f.sample(frac=1.0, random_state=7)
    a = policy.per_tier_flags(f["score"].to_numpy(), f["geo_tier"], 0.20)
    b = policy.per_tier_flags(shuffled["score"].to_numpy(), shuffled["geo_tier"], 0.20)
    assert (pd.Series(a, index=f.index).sort_index().to_numpy()
            == pd.Series(b, index=shuffled.index).sort_index().to_numpy()).all()


# --- the §8.4 protections --------------------------------------------------


def test_protections_veto_a_flagged_order():
    f = frame()
    f.loc[f["geo_tier"] == "TIER3", "pit_has_clean_record"] = True
    with_veto = policy.restrict(f, f["score"].to_numpy(), 0.25, "global")
    without = policy.restrict(f, f["score"].to_numpy(), 0.25, "global",
                              apply_protections=False)
    assert with_veto.sum() < without.sum()
    assert not with_veto[(f["geo_tier"] == "TIER3").to_numpy()].any()


def test_zero_history_customers_are_never_restricted():
    f = frame()
    f["pit_has_history"] = False
    assert not policy.restrict(f, f["score"].to_numpy(), 0.50, "per-tier").any()


# --- FA-01's measurement ---------------------------------------------------


def test_ratio_audit_reports_a_zero_best_tier_rather_than_dividing_by_it():
    """A best tier of zero is the STRONGEST form of the failure, not a
    divide-by-zero to paper over. It must never render as a pass."""
    f = frame()
    audit = policy.ratio_audit(f, f["score"].to_numpy(), "global", volumes=(0.10,))
    row = audit.iloc[0]
    assert row["verdict"] == "FAIL"
    assert isinstance(row["ratio"], str)


def test_ratio_audit_watches_the_worst_pair_not_a_named_pair():
    """§8.4 names Tier-3 and Metro. Encoding those names would let a policy that
    concentrated on TIER2 pass a check watching the wrong pair."""
    # Built so the four tiers land at four DISTINCT restriction rates, with the
    # extremes on TIER2 and TIER1 rather than on the pair §8.4 happens to name.
    # A Tier-3-over-Metro test would read 0.4 / 0.2 = 2.0x here and PASS, while
    # the policy is actually running at 8x.
    target = {"METRO": 0.2, "TIER1": 0.1, "TIER2": 0.8, "TIER3": 0.4}
    n = 50
    rows = []
    for tier, rate in target.items():
        for i in range(n):
            rows.append({"geo_tier": tier, "score": 1.0 if i < rate * n else 0.0,
                         "rto_flag": False, "payment_method": "COD",
                         "pit_has_clean_record": False, "pit_has_history": True})
    f = pd.DataFrame(rows)
    volume = sum(target.values()) / len(target)

    audit = policy.ratio_audit(f, f["score"].to_numpy(), "global", volumes=(volume,))
    row = audit.iloc[0]
    assert row["worst_tier"] == "TIER2"
    assert row["best_tier"] == "TIER1"
    assert row["ratio"] == pytest.approx(8.0)
    assert row["verdict"] == "FAIL"
    # What a Tier-3-vs-Metro test would have reported, for contrast.
    assert row["TIER3"] / row["METRO"] == pytest.approx(2.0)


# --- condition 2: the price of fairness ------------------------------------


def test_margin_cost_signs_a_restricted_rto_as_a_gain():
    """An RTO carries a negative contribution margin, so removing it must be a
    POSITIVE delta. Getting this backwards would report the price of the
    fairness constraint as a saving, which is the one error nobody would query."""
    f = frame()
    cm = margins(f)
    cost = policy.margin_cost(f, f["score"].to_numpy(), cm, 1.0, volumes=(0.25,))
    assert cost.iloc[0]["global_delta_cm"] > 0


def test_margin_cost_matches_the_volumes_it_compares():
    f = frame()
    cm = margins(f)
    cost = policy.margin_cost(f, f["score"].to_numpy(), cm, 1.0, volumes=(0.20,))
    assert cost.iloc[0]["selected"] == pytest.approx(len(f) * 0.20, abs=len(policy.TIERS))


def test_global_targeting_is_never_worse_than_per_tier_at_equal_volume():
    """The global rule maximises captured value by construction, so the price of
    fairness cannot be negative. A negative price would mean per-tier
    thresholding is free, which would make the whole escalation pointless."""
    f = frame()
    cm = margins(f)
    cost = policy.margin_cost(f, f["score"].to_numpy(), cm, 1.0)
    assert (cost["price_of_fairness"] >= 0).all()


def test_conversion_sensitivity_improves_both_arms():
    """Switching beats abandoning for the platform, so a higher switch share must
    lift both arms -- and it must lift them by the same per-order amount, which
    is why the price barely moves."""
    f = frame()
    cm = margins(f)
    prepaid = {"prepaid_rto_rate": 0.05, "cm_delivered": 120.0, "cm_rto": -305.0}
    out = policy.conversion_sensitivity(f, f["score"].to_numpy(), cm, prepaid,
                                        1.0, 0.25, shares=(0.0, 0.5))
    assert out.iloc[1]["global_delta_cm"] > out.iloc[0]["global_delta_cm"]
    assert out.iloc[1]["per-tier_delta_cm"] > out.iloc[0]["per-tier_delta_cm"]


# --- condition 3: absolute exposure ----------------------------------------


def test_tier_exposure_counts_restricted_orders_below_pstar():
    """The number the ratio hides: restricting an order scored below p* is a
    deliberate loss, and per-tier thresholding creates them in the safe tiers."""
    f = frame()
    exposure = policy.tier_exposure(f, f["score"].to_numpy(), 0.25, "per-tier",
                                    pstar=0.9)
    assert exposure.set_index("geo_tier").loc["METRO", "share_below_pstar"] == 1.0


# --- condition 1: which levers are rationed --------------------------------


def test_only_restrictive_interventions_are_per_tier():
    table = policy.intervention_table()
    restrictive = table[table["kind"] == "RESTRICTIVE"]
    offers = table[table["kind"] == "offer"]
    assert set(restrictive["threshold basis"]) == {"per-tier"}
    assert set(offers["threshold basis"]) == {"global"}
    # B COD fee, D partial payment, E smart recommendation (A48), G COD gating.
    assert set(restrictive["id"]) == {"B", "D", "E", "G"}
    assert set(interventions.restrictive_ids()) == set(restrictive["id"])


def test_intervention_e_is_restrictive_and_carries_the_one_tap_floor():
    """A48. E de-emphasises, and for a 62%-COD population salience IS the option.
    The one-tap floor lives in code as well as in the PRD, because a constraint
    that lives only in a document is a constraint that gets lost at a handover."""
    row = policy.intervention_table().set_index("id").loc["E"]
    assert row["kind"] == "RESTRICTIVE"
    assert row["threshold basis"] == "per-tier"
    assert "ONE TAP" in interventions.ONE_TAP_CONSTRAINT


# --- the three-rule baseline -----------------------------------------------


def test_m2_baseline_escalates_cod_one_tier_and_caps_at_high():
    m2 = baseline.M2RulesBaseline()
    out = m2.add_column(pd.DataFrame({
        "pit_risk_tier_rule_based": ["LOW", "MED", "HIGH", "LOW", "HIGH"],
        "payment_method": ["COD", "COD", "COD", "PREPAID", "PREPAID"],
    }))
    assert list(out[m2.COLUMN]) == ["MED", "HIGH", "HIGH", "LOW", "HIGH"]


def test_m2_baseline_can_be_configured_not_to_escalate():
    """`m2_cod_escalates_one_tier` is a params flag. If it is ever turned off the
    baseline must become the two-rule one, not silently keep escalating."""
    m2 = baseline.M2RulesBaseline(cod_escalates_one_tier=False)
    out = m2.add_column(pd.DataFrame({
        "pit_risk_tier_rule_based": ["LOW", "MED"],
        "payment_method": ["COD", "COD"],
    }))
    assert list(out[m2.COLUMN]) == ["LOW", "MED"]


def test_m2_baseline_verification_catches_a_mismatch():
    m2 = baseline.M2RulesBaseline()
    rebuilt = pd.DataFrame({"session_id": ["s1", "s2"], m2.COLUMN: ["MED", "HIGH"]})
    planted = pd.DataFrame({"session_id": ["s1", "s2"], m2.COLUMN: ["MED", "MED"]})
    result = m2.verify_against_planted(rebuilt, planted)
    assert not result["exact"]
    assert result["agreement"] == 0.5


# --- the challenger ship rule ----------------------------------------------


def test_challenger_ship_rule_is_a_floor_not_a_target():
    """A 2.99pp margin does not ship. §9.3's rule is >= 3pp and the whole point
    is that it is not negotiated after the fact."""
    assert challenger.SHIP_MARGIN_AUC == 0.03
    assert (0.7684 + 0.0299) - 0.7684 < challenger.SHIP_MARGIN_AUC


# --- FA-01's staleness guard -----------------------------------------------


def _publish(tmp_path, monkeypatch, payload):
    from src.validation import tests_fa
    path = tmp_path / "fairness_checks.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(tests_fa, "CHECKS", path)
    return tests_fa


def _orders() -> dict:
    return {"fct_order": pd.DataFrame({"order_id": ["o1", "o2"], "rto_flag": [0, 1]})}


def _rows(ratios) -> list:
    return [{"volume_flagged": v, "ratio": r, "worst_tier": "TIER3",
             "best_tier": "METRO"}
            for v, r in zip(policy.FA01_VOLUMES, ratios)]


def test_fa01_passes_on_a_matching_publication(tmp_path, monkeypatch):
    from src.validation.dataset_hash import order_hash
    tables = _orders()
    fa = _publish(tmp_path, monkeypatch, {
        "fct_order_sha256": order_hash(tables["fct_order"]),
        "checks": {"FA-01": {"passed": True, "by_volume": _rows([1.2, 1.3, 1.4, 1.3])}},
    })
    assert fa._fa_01(tables).status.value == "PASS"


def test_fa01_skips_rather_than_passes_on_a_stale_publication(tmp_path, monkeypatch):
    """The failure mode that matters. A fairness result from a different dataset
    is not evidence about this one, and accepting it would fabricate a green
    light on the one test that exists to prevent a regression."""
    tables = _orders()
    fa = _publish(tmp_path, monkeypatch, {
        "fct_order_sha256": "not-the-hash-of-this-dataset",
        "checks": {"FA-01": {"passed": True, "by_volume": _rows([1.2, 1.3, 1.4, 1.3])}},
    })
    assert fa._fa_01(tables).status.value == "SKIP"


def test_fa01_skips_when_a_required_volume_is_missing(tmp_path, monkeypatch):
    """Three of four volumes satisfies `passed` while leaving the fourth
    untested -- a check that looks green because it did less work."""
    from src.validation.dataset_hash import order_hash
    tables = _orders()
    fa = _publish(tmp_path, monkeypatch, {
        "fct_order_sha256": order_hash(tables["fct_order"]),
        "checks": {"FA-01": {"passed": True, "by_volume": _rows([1.2, 1.3, 1.4])[:3]}},
    })
    assert fa._fa_01(tables).status.value == "SKIP"


def test_fa01_rederives_the_verdict_from_the_ratios(tmp_path, monkeypatch):
    """The `passed` flag was written by the same script that chose the policy.
    The ratios are the evidence, so a flag that disagrees with them loses."""
    from src.validation.dataset_hash import order_hash
    tables = _orders()
    fa = _publish(tmp_path, monkeypatch, {
        "fct_order_sha256": order_hash(tables["fct_order"]),
        "checks": {"FA-01": {"passed": True, "by_volume": _rows([1.2, 1.3, 9.9, 1.3])}},
    })
    assert fa._fa_01(tables).status.value == "FAIL"


def test_fa01_fails_when_a_tier_is_never_restricted(tmp_path, monkeypatch):
    from src.validation.dataset_hash import order_hash
    tables = _orders()
    fa = _publish(tmp_path, monkeypatch, {
        "fct_order_sha256": order_hash(tables["fct_order"]),
        "checks": {"FA-01": {"passed": False, "by_volume": _rows(
            [1.2, "no orders restricted in the best tier", 1.4, 1.3])}},
    })
    assert fa._fa_01(tables).status.value == "FAIL"
