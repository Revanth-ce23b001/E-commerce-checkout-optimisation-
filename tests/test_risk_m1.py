"""Unit tests for the Phase 4 risk-model code.

These test the CODE, not the generated data — the dataset-level assertions live in
``src/validation/``. What is worth testing here is everything that would fail
silently: a firewall that passes when it should fail, a design matrix that leaks
test statistics into its own fit, an AUC that is subtly wrong, and a fairness
audit that divides by zero and calls it a pass.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.risk import baseline, dataset, evaluate, fairness, features

PARAMS = {
    "leakage_guard": {
        "safe_feature_whitelist": ["session_id", "geo_tier", "order_value", "rto_flag"],
        "hard_blocked": ["attempt_delay_days", "delivery_delay_days", "clv_estimate"],
    }
}


def frame(**extra) -> pd.DataFrame:
    base = {"session_id": ["s1"], "geo_tier": ["METRO"], "order_value": [500.0],
            "rto_flag": [False]}
    base.update(extra)
    return pd.DataFrame(base)


# --- the firewall ----------------------------------------------------------


def test_firewall_passes_a_clean_frame():
    dataset.assert_firewall(frame(), PARAMS)


def test_firewall_rejects_a_hard_blocked_column():
    with pytest.raises(AssertionError, match="LK-02"):
        dataset.assert_firewall(frame(clv_estimate=[1200.0]), PARAMS)


def test_firewall_rejects_a_column_outside_the_whitelist():
    """A new column is guilty until whitelisted. LK-01 is a subset assertion, not
    a blocklist, precisely so that an unreviewed addition cannot slip through."""
    with pytest.raises(AssertionError, match="LK-01"):
        dataset.assert_firewall(frame(some_new_feature=[1.0]), PARAMS)


def test_stage_4_bar_is_asserted_by_name_even_if_someone_whitelists_it():
    """Phase 3 closeout §3.2. The redundancy is the point: if attempt_delay_days
    is ever added to the whitelist by mistake, the named check still fires."""
    params = {
        "leakage_guard": {
            "safe_feature_whitelist": PARAMS["leakage_guard"]["safe_feature_whitelist"]
            + ["attempt_delay_days"],
            "hard_blocked": ["clv_estimate"],
        }
    }
    with pytest.raises(AssertionError, match="Stage-4 bar"):
        dataset.assert_firewall(frame(attempt_delay_days=[3.0]), params)


# --- the design matrix -----------------------------------------------------


def synthetic(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "session_id": [f"s{i}" for i in range(n)],
        "customer_id": [f"c{i % 50}" for i in range(n)],
        "session_start_ts": pd.date_range("2026-01-01", periods=n, freq="h"),
        "geo_tier": rng.choice(["METRO", "TIER1", "TIER2", "TIER3"], n),
        "category": rng.choice(["GROCERY_FMCG", "FASHION"], n),
        "device_type": rng.choice(["ANDROID", "IOS", "WEB"], n),
        "age_bucket": rng.choice(["25-34", "35-44"], n),
        "acquisition_channel": rng.choice(["ORGANIC", "REFERRAL"], n),
        "payment_method": rng.choice(["COD", "PREPAID"], n),
        "paid_via_switch": rng.random(n) < 0.1,
        "payment_attempt_count": rng.integers(1, 3, n),
        "order_value": rng.lognormal(6.5, 0.6, n),
        "pit_avg_order_value": np.where(rng.random(n) < 0.3, np.nan, rng.random(n) * 900),
        "pit_has_clean_record": rng.random(n) < 0.25,
        "pit_has_history": rng.random(n) < 0.85,
        "rto_flag": rng.random(n) < 0.19,
    })


def test_m1_projects_away_every_stage_3_column():
    """The single defining difference between M1 and M2."""
    built = features.DesignMatrix("M1").fit_transform(synthetic())
    for column in dataset.M2_ONLY:
        assert not any(column in name for name in built.columns), column


def test_m2_keeps_the_stage_3_columns():
    built = features.DesignMatrix("M2").fit_transform(synthetic())
    assert any("payment_method[COD]" == name for name in built.columns)
    assert "paid_via_switch" in built.columns


def test_transform_uses_train_statistics_not_its_own():
    """Learning the scaler on test is a small leak that is free to make and
    invisible afterwards, so it is asserted rather than trusted."""
    matrix = features.DesignMatrix("M1")
    train = synthetic(300, seed=1)
    matrix.fit_transform(train)
    shifted = synthetic(300, seed=2)
    shifted["order_value"] = shifted["order_value"] * 10
    out = matrix.transform(shifted)
    assert abs(out["log1p_order_value"].mean()) > 0.5, (
        "a matrix rescaled on its own statistics would centre this at zero")


def test_a_missing_reference_level_is_an_error_not_a_full_dummy_set():
    """The real bug this caught: device_type's reference was declared as
    ANDROID_APP, which does not exist, so all three levels were emitted and the
    set was collinear with the intercept at VIF 2.4e7."""
    data = synthetic()
    data["geo_tier"] = "TIER3"
    with pytest.raises(AssertionError, match="reference level"):
        features.DesignMatrix("M1").fit_transform(data)


def test_perfectly_collinear_columns_are_dropped_and_recorded():
    data = synthetic()
    matrix = features.DesignMatrix("M1")
    built = matrix.fit_transform(data)
    assert len(built.columns) == len(set(built.columns))
    for name, _ in matrix.redundant_:
        assert name not in built.columns


# --- the split -------------------------------------------------------------


def test_split_is_chronological_with_no_overlap():
    split = dataset.build_split(synthetic(1000))
    assert split.train["session_start_ts"].max() < split.test["session_start_ts"].min()


def test_clean_window_cuts_at_the_first_censored_date():
    data = synthetic(240)
    censoring = pd.Series(
        [0.0] * 200 + [0.4] * 40,
        index=pd.to_datetime(data["session_start_ts"]).dt.normalize())
    kept, dropped = dataset.clean_window(data, censoring)
    assert dropped > 0
    assert kept["session_start_ts"].max() < censoring.index[200]


# --- the metrics -----------------------------------------------------------


def test_auc_of_a_perfect_ranking_is_one_and_a_reversed_one_is_zero():
    y = np.array([0, 0, 1, 1], float)
    assert evaluate.auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == pytest.approx(1.0)
    assert evaluate.auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == pytest.approx(0.0)


def test_auc_of_constant_predictions_is_a_half_not_undefined():
    """Ties must be averaged. A tie-blind implementation returns 1.0 here, which
    would make a constant model look perfect."""
    y = np.array([0, 1, 0, 1], float)
    assert evaluate.auc(y, np.full(4, 0.19)) == pytest.approx(0.5)


def test_ece_is_zero_for_a_perfectly_calibrated_prediction():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.05, 0.95, 40000)
    y = (rng.random(40000) < p).astype(float)
    assert evaluate.ece(y, p) < 0.01


def test_ece_catches_a_uniformly_inflated_prediction():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.05, 0.55, 40000)
    y = (rng.random(40000) < p).astype(float)
    assert evaluate.ece(y, np.clip(p + 0.15, 0, 1)) == pytest.approx(0.15, abs=0.02)


# --- the baseline ----------------------------------------------------------


def test_rules_baseline_assigns_each_tier_its_own_train_rate():
    train = pd.DataFrame({
        "pit_risk_tier_rule_based": ["LOW"] * 10 + ["HIGH"] * 10,
        "rto_flag": [False] * 10 + [True] * 8 + [False] * 2,
    })
    fitted = baseline.RulesBaseline().fit(train)
    assert fitted.rates_["LOW"] == pytest.approx(0.0)
    assert fitted.rates_["HIGH"] == pytest.approx(0.8)


def test_rules_baseline_falls_back_to_the_base_rate_for_an_unseen_tier():
    train = pd.DataFrame({"pit_risk_tier_rule_based": ["LOW", "HIGH"],
                          "rto_flag": [False, True]})
    fitted = baseline.RulesBaseline().fit(train)
    unseen = pd.DataFrame({"pit_risk_tier_rule_based": ["MED"]})
    assert fitted.predict_proba(unseen)[0] == pytest.approx(0.5)


# --- the fairness audit ----------------------------------------------------


def test_zero_metro_restrictions_escalates_rather_than_dividing_by_zero():
    """A Metro rate of zero is the strongest form of the failure, not a missing
    value. An implementation that returns NaN here would silently pass."""
    audited = fairness.geo_audit(
        pd.DataFrame({"geo_tier": ["METRO"] * 50 + ["TIER3"] * 50}),
        np.concatenate([np.zeros(50), np.ones(50)]), volumes=(0.1,))
    assert audited.loc[0, "verdict"] == "ESCALATE"
    assert audited.loc[0, "tier3_over_metro"] == "no Metro flagged"


def test_an_even_spread_across_tiers_passes():
    tiers = ["METRO", "TIER1", "TIER2", "TIER3"] * 250
    rng = np.random.default_rng(11)
    audited = fairness.geo_audit(pd.DataFrame({"geo_tier": tiers}),
                                 rng.random(1000), volumes=(0.2,))
    assert audited.loc[0, "verdict"] == "PASS"


def test_overlay_caps_clean_records_and_never_restricts_zero_history():
    frame_ = pd.DataFrame({
        "pit_has_clean_record": [True, False, False],
        "pit_has_history": [True, True, False],
        "rto_flag": [False, True, True],
    })
    out = fairness.apply_overlay(frame_, pd.Series(["HIGH", "HIGH", "HIGH"]))
    assert out.loc[0, "tier_final"] == "MED"   # clean record, capped
    assert out.loc[1, "tier_final"] == "HIGH"  # no protection applies
    assert out.loc[2, "tier_final"] == "MED"   # zero history, never restricted


def test_overlay_never_escalates_a_tier():
    frame_ = pd.DataFrame({"pit_has_clean_record": [True], "pit_has_history": [True],
                           "rto_flag": [False]})
    out = fairness.apply_overlay(frame_, pd.Series(["LOW"]))
    assert out.loc[0, "tier_final"] == "LOW"
