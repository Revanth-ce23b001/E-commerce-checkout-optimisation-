"""Unit tests for the two validation gates added by the A7 and A19 rulings.

These test the TEST CODE. A validation test that cannot fail is worse than no
test at all: it produces a green report that means nothing, and it trains
whoever reads the report to stop looking. So each gate is checked in both
directions — it passes what it should pass, and it fails what it should fail.
"""

from __future__ import annotations

import pytest

from src.config.loader import load_params
from src.validation.result import Severity, Status
from src.validation.tests_cal import (
    MODEL_BLOCKS,
    cal_09_no_slope_changed,
    cal_10_reason_weights_frozen,
    cal_11_selection_share,
)
from src.validation.tests_lk import lk_06_shrinkage_prior_is_declared
from src.models.logit import CoefficientLedger


@pytest.fixture(scope="module")
def params():
    return load_params("config/params.yaml", "config/params.schema.json")


class TestCal11SelectionShare:
    """CAL-11 is the real gate on this dataset (decision A7)."""

    def test_passes_at_the_headline_one_third(self, params):
        # The claim the project exists to make: a naive read overstates the
        # causal effect by roughly a third. 19.9 -> 13.4 is 32.7%.
        result = cal_11_selection_share(19.9, 13.4, params)
        assert result.status is Status.PASS
        assert result.severity is Severity.HARD

    def test_fails_when_confounding_is_too_weak(self, params):
        """8% selection share: the dataset no longer supports the case study."""
        result = cal_11_selection_share(19.9, 18.3, params)
        assert result.status is Status.FAIL
        # The message must point at escalation, not at tuning — that is the whole
        # discipline this project is built to hold.
        assert "do not adjust a slope" in result.detail.lower()
        assert "escalate" in result.detail.lower()

    def test_fails_when_confounding_swamps_the_planted_effect(self, params):
        """60% selection share: is_cod is barely doing anything."""
        result = cal_11_selection_share(19.9, 8.0, params)
        assert result.status is Status.FAIL

    def test_handles_a_non_positive_naive_gap(self, params):
        """If COD does not RTO more than prepaid at all, the share is undefined.

        Dividing by zero here would crash the report generator; returning a
        misleading PASS would be worse.
        """
        result = cal_11_selection_share(0.0, 13.4, params)
        assert result.status is Status.FAIL
        assert "undefined" in result.detail

    def test_reads_its_band_from_params_not_from_code(self, params):
        gate = params.require("selection_share_gate")
        assert (float(gate["lo"]), float(gate["hi"])) == (0.25, 0.45)
        assert gate["severity"] == "HARD"


class TestLk06ShrinkagePrior:
    """LK-06 guards the subtlest leak in the project (decision A19)."""

    def test_passes_when_the_declared_constant_is_used(self, params):
        prior = float(params.require("priors.rto_prior"))
        k = float(params.require("priors.shrinkage_k"))
        assert lk_06_shrinkage_prior_is_declared(prior, k, params).status is Status.PASS

    def test_fails_when_the_prior_was_computed_from_the_data(self, params):
        """The realised in-window RTO rate would be close to 0.165 but not equal.

        Closeness is exactly why this needs an exact check: a prior of 0.1662
        derived from realised outcomes looks right and is a population-level leak.
        """
        k = float(params.require("priors.shrinkage_k"))
        result = lk_06_shrinkage_prior_is_declared(0.1662, k, params)
        assert result.status is Status.FAIL
        assert "population-level leak" in result.detail

    def test_fails_when_k_drifts(self, params):
        prior = float(params.require("priors.rto_prior"))
        result = lk_06_shrinkage_prior_is_declared(prior, 12.0, params)
        assert result.status is Status.FAIL


class TestCal09Coverage:
    """Decision A2 + A11 widened CAL-09 from two blocks to five."""

    def test_covers_all_five_calibrated_blocks(self):
        assert MODEL_BLOCKS == (
            "cod_model", "rto_model", "conversion_model",
            "pre_window_cod_model", "pre_window_rto_model",
        )

    def test_every_block_declares_an_intercept(self, params):
        solved = params.solved_intercepts()
        for block in MODEL_BLOCKS:
            assert f"{block}.intercept_solved" in solved

    def test_a_changed_slope_is_caught(self, params):
        """The failure mode CAL-09 exists for: nudging a slope to hit a target."""
        ledger = CoefficientLedger()
        declared = float(params.require("rto_model.coefficients.is_cod"))
        ledger.record("rto_model", "is_cod", declared + 0.10)
        result = cal_09_no_slope_changed(
            ledger, params, ("rto_model",), require_complete_coverage=False
        )
        assert result.status is Status.FAIL
        assert "is_cod" in result.detail

    def test_an_undeclared_coefficient_is_caught(self, params):
        """A business literal that leaked into src/."""
        ledger = CoefficientLedger()
        ledger.record("rto_model", "invented_term", 0.42)
        result = cal_09_no_slope_changed(
            ledger, params, ("rto_model",), require_complete_coverage=False
        )
        assert result.status is Status.FAIL
        assert "leaked into src/" in result.detail

    def test_partial_run_does_not_report_unconsumed_slopes(self, params):
        """Mid-pipeline, most coefficients legitimately have not run yet.

        Failing on those would train the reader to ignore a HARD test.
        """
        ledger = CoefficientLedger()
        ledger.record(
            "rto_model", "is_cod", float(params.require("rto_model.coefficients.is_cod"))
        )
        partial = cal_09_no_slope_changed(
            ledger, params, ("rto_model",), require_complete_coverage=False
        )
        complete = cal_09_no_slope_changed(ledger, params, ("rto_model",))
        assert partial.status is Status.PASS
        assert complete.status is Status.FAIL      # the end-of-run arm still bites


class TestCal10StillFrozen:
    def test_reason_weights_are_frozen_and_matching(self, params):
        result = cal_10_reason_weights_frozen(params)
        assert result.status is Status.PASS, result.detail
        assert result.severity is Severity.HARD


class TestDdlStalenessGate:
    """The staleness guard on `reports/database_checks.json` (decision A45).

    A database-backed result depends on two things that can go stale
    independently: the data it ran against, and the schema it ran against. The
    guard originally hashed only the data.

    A45 is what exposed that. It added a column and two CHECK constraints while
    leaving `fct_order` byte-identical *on purpose* — so a data-only guard would
    have accepted a results file describing 102 constraints as current evidence
    about a schema that now has 104. LK-01 is the sharper case: it is a claim
    about a VIEW, which lives entirely in the DDL, so the data hash says nothing
    about it at all.
    """

    def test_ddl_hash_is_stable_across_calls(self):
        from src.validation.dataset_hash import ddl_hash
        assert ddl_hash() == ddl_hash()
        assert len(ddl_hash()) == 64

    def test_ddl_hash_changes_when_a_statement_changes(self, tmp_path):
        from src.validation.dataset_hash import ddl_hash
        (tmp_path / "00_a.sql").write_text("CREATE TABLE t (a int);", encoding="utf-8")
        before = ddl_hash(tmp_path)
        (tmp_path / "00_a.sql").write_text(
            "CREATE TABLE t (a int, b bool NOT NULL);", encoding="utf-8")
        assert ddl_hash(tmp_path) != before

    def test_ddl_hash_changes_when_a_file_is_added(self, tmp_path):
        # The realistic A45 shape: existing DDL untouched, a new constraint file
        # alongside it. Nothing already-hashed changed, and the hash must move.
        from src.validation.dataset_hash import ddl_hash
        (tmp_path / "00_a.sql").write_text("CREATE TABLE t (a int);", encoding="utf-8")
        before = ddl_hash(tmp_path)
        (tmp_path / "01_b.sql").write_text("CREATE VIEW v AS SELECT a FROM t;",
                                           encoding="utf-8")
        assert ddl_hash(tmp_path) != before

    def test_ddl_hash_is_order_independent_of_filesystem_listing(self, tmp_path):
        # Hashed in name order, so two checkouts of the same tree agree even if
        # the directory enumerates differently.
        from src.validation.dataset_hash import ddl_hash
        (tmp_path / "01_b.sql").write_text("SELECT 2;", encoding="utf-8")
        (tmp_path / "00_a.sql").write_text("SELECT 1;", encoding="utf-8")
        first = ddl_hash(tmp_path)
        for f in tmp_path.glob("*.sql"):
            content = f.read_text(encoding="utf-8")
            f.unlink()
            f.write_text(content, encoding="utf-8")
        assert ddl_hash(tmp_path) == first

    def test_filename_is_part_of_the_hash(self, tmp_path):
        # Renaming a file changes which DDL runs and in what order, so it must
        # change the hash even when every byte of content is identical.
        from src.validation.dataset_hash import ddl_hash
        (tmp_path / "00_a.sql").write_text("SELECT 1;", encoding="utf-8")
        before = ddl_hash(tmp_path)
        (tmp_path / "00_a.sql").rename(tmp_path / "00_z.sql")
        assert ddl_hash(tmp_path) != before
