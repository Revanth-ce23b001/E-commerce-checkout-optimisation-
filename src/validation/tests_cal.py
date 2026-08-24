"""CAL family — calibration tests.

This module currently implements only the two immutability tests, CAL-09 and CAL-10.
The remaining CAL tests (COD share, RTO rates, conversion, payment-failure share,
addressable share) measure GENERATED DATA and are written alongside their
generator modules.

CAL-09 covers three model blocks (:data:`MODEL_BLOCKS`): cod_model, rto_model and
conversion_model. Decision A2 admits a third calibrated intercept -- which spec
§9.1 already listed as ``conversion_model_alpha0`` -- but grants no new freedom on
slopes. Three intercepts may be solved; zero slopes may move.

CAL-10 is ACTIVE: the RTO reason weights were approved and frozen under decision
A4, and ``rto_reasons.frozen_hash`` is set.

Why CAL-09 comes first
----------------------
Brief §3.5: *"Validation test CAL-09 asserts that no slope in the run manifest differs
from params.yaml. Implement CAL-09 early, not last."*

The reason is that CAL-09 is the only thing standing between this project and its
single worst failure mode: nudging a slope coefficient until a calibration target
passes. Spec §18 puts it plainly — if COD share cannot reach 62% with the fixed
slopes, *"that means the Phase 1 assumption set is internally inconsistent — and that
is a real result worth reporting, not a bug worth hiding."*

The subtlety that makes CAL-09 real
-----------------------------------
A naive CAL-09 would compare the run manifest against ``params.yaml``. But the
manifest is *written from* params.yaml, so that comparison is a file against a copy
of itself: it passes no matter what the generator actually did.

This implementation instead compares the :class:`CoefficientLedger` — every
coefficient the logit assembler genuinely consumed at runtime — against params.yaml.
A coefficient overridden anywhere in the pipeline is therefore caught.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.models.logit import INTERCEPT_TERM, CoefficientLedger
from src.validation.result import ResultSet, Severity, Status, TestResult

# Structural key names, not business values.
COEFFICIENTS_KEY = "coefficients"
# The RTO model's Stage-2 deltas live in their own block. They are slopes and are
# just as immutable as the Stage-1 coefficients, so CAL-09 must cover them --
# otherwise the post-dispatch shock would be the one place in the project where a
# coefficient could be nudged without any test noticing.
SHOCK_KEY = "post_dispatch_shock"
SHOCK_PREFIX = "shock."
# Not a slope: the spec calls this "the AUC ceiling lever" and GT-05 sets the
# ceiling as a target, so it is a calibrated quantity by design.
SHOCK_EXCLUDED = ("noise_sd",)
REASONS_BLOCK = "rto_reasons"
FROZEN_HASH_KEY = "frozen_hash"
IMMUTABLE_REASON_KEYS = ("base_weights", "driver_weights", "class_map")

# Every model block whose slopes CAL-09 protects. FIVE intercepts may now be
# solved -- cod, rto, conversion, pre_window_cod, pre_window_rto (decisions A2
# and A11). All five are LEVELS. ZERO slopes may move, in any block.
#
# The two pre-window blocks declare NO coefficients of their own: decision A11
# re-uses the already-approved cod_model / rto_model latent slopes. The
# pre-window generators therefore record those slopes into the PARENT block, so
# the ledger's duplicate-value check enforces that pre-window and in-window
# behaviour share one value per coefficient. That shared structure is what makes
# prior RTO predict future RTO (H3 / BR-02) and prior COD predict future COD
# (BR-03); if the two ever diverged, the ledger would raise before any data
# reached validation.
MODEL_BLOCKS = (
    "cod_model",
    "rto_model",
    "conversion_model",
    "pre_window_cod_model",
    "pre_window_rto_model",
)


def cal_09_no_slope_changed(
    ledger: CoefficientLedger,
    params: Any,
    model_blocks: tuple[str, ...] = MODEL_BLOCKS,
    *,
    require_complete_coverage: bool = True,
) -> TestResult:
    """CAL-09 (HARD) — no slope coefficient differs from params.yaml.

    Compares every coefficient the generator actually used against the declared value.
    Intercepts are excluded: they are the values the calibrator is permitted to solve.

    The test has two arms, and they are not equally valid at all times:

    * **A value mismatch, or a coefficient used but not declared.** Always fatal.
      Either the generator overrode params.yaml, or a business literal leaked into
      ``src/``.
    * **A coefficient declared but never used.** Fatal only on a COMPLETE run. Set
      ``require_complete_coverage=False`` at a mid-pipeline checkpoint, where most
      coefficients legitimately have not been consumed yet — modules 08 onward
      simply have not run. Reporting those as failures at a checkpoint would train
      the reader to ignore a HARD test, which is worse than not running it.

    Parameters
    ----------
    ledger
        The runtime record of coefficients consumed, from ``src.models.logit``.
    params
        The loaded :class:`~src.config.loader.Params`.
    model_blocks
        Which model blocks to check. Defaults to :data:`MODEL_BLOCKS` — the COD,
        RTO and conversion models. Still a parameter rather than a hard-coded
        constant so a test can narrow the scope.
    """
    mismatches: list[str] = []
    unused: list[str] = []
    undeclared: list[str] = []
    checked = 0

    for block in model_blocks:
        declared = _flatten_coefficients(params.get(f"{block}.{COEFFICIENTS_KEY}", default={}))
        for term, value in params.get(f"{block}.{SHOCK_KEY}", default={}).items():
            if term not in SHOCK_EXCLUDED:
                declared[f"{SHOCK_PREFIX}{term}"] = float(value)
        used = ledger.slopes(block)

        for term, used_value in used.items():
            if term not in declared:
                undeclared.append(f"{block}.{term}")
                continue
            checked += 1
            if not _bit_identical(used_value, declared[term]):
                mismatches.append(
                    f"{block}.{term}: params.yaml={declared[term]!r} but generator "
                    f"used {used_value!r}"
                )

        for term in declared:
            if term not in used:
                unused.append(f"{block}.{term}")

    problems: list[str] = []
    if mismatches:
        problems.append(f"{len(mismatches)} slope(s) differ: " + "; ".join(mismatches))
    if undeclared:
        problems.append(
            f"{len(undeclared)} coefficient(s) used but not declared in params.yaml: "
            + ", ".join(sorted(undeclared))
            + " — a business value has leaked into src/"
        )
    if unused and require_complete_coverage:
        problems.append(
            f"{len(unused)} coefficient(s) declared but never used: "
            + ", ".join(sorted(unused))
            + " — a planted relationship is silently absent from the data"
        )

    coverage = (
        f"{checked} slope(s) verified across {len(model_blocks)} block(s)"
        if require_complete_coverage
        else f"{checked} slope(s) verified across {len(model_blocks)} block(s); "
             f"{len(unused)} not yet consumed (partial run)"
    )

    return TestResult(
        test_id="CAL-09",
        name="No slope coefficient differs from params.yaml",
        severity=Severity.HARD,
        status=Status.PASS if not problems else Status.FAIL,
        expected="exact match on every slope (tolerance 0)",
        actual=coverage,
        delta="0 mismatches" if not problems else f"{len(mismatches)} mismatch(es)",
        detail=" | ".join(problems),
    )


def cal_10_reason_weights_frozen(params: Any) -> TestResult:
    """CAL-10 (HARD) — RTO reason weights are frozen.

    The driver weights that determine *why* an order failed are not slopes in
    ``cod_model`` or ``rto_model``, so CAL-09 does not cover them. Without this test,
    they could be nudged until CAL-08's 65% addressable target passed — which would
    make the avoidability waterfall circular, the exact outcome spec §11.1 warns
    against.

    Asserts that ``base_weights``, ``driver_weights`` and ``class_map`` hash to the
    value frozen at approval time.

    Returns SKIP only if the weights or the frozen hash are absent, so the harness
    never silently claims a protection that is not in force. As of decision A4 the
    weights are approved and the hash is set, so this test is live.
    """
    block = params.get(REASONS_BLOCK, default=None)
    if not block:
        return TestResult(
            test_id="CAL-10",
            name="RTO reason weights frozen",
            severity=Severity.HARD,
            status=Status.SKIP,
            expected="hash matches frozen_hash",
            actual="rto_reasons block absent",
            detail="Driver weights are pending approval (decision A4). Not yet in force.",
        )

    frozen = block.get(FROZEN_HASH_KEY)
    payload = {k: block.get(k) for k in IMMUTABLE_REASON_KEYS}
    actual_hash = _canonical_sha256(payload)

    if frozen is None:
        return TestResult(
            test_id="CAL-10",
            name="RTO reason weights frozen",
            severity=Severity.HARD,
            status=Status.SKIP,
            expected="hash matches frozen_hash",
            actual=f"computed {actual_hash[:16]}…",
            detail=(
                f"{REASONS_BLOCK}.{FROZEN_HASH_KEY} not set. Once the driver-weight "
                "matrix is approved, record this hash to activate the protection."
            ),
        )

    matched = frozen == actual_hash
    return TestResult(
        test_id="CAL-10",
        name="RTO reason weights frozen",
        severity=Severity.HARD,
        status=Status.PASS if matched else Status.FAIL,
        expected=f"{frozen[:16]}…",
        actual=f"{actual_hash[:16]}…",
        delta="identical" if matched else "CHANGED",
        detail=(
            ""
            if matched
            else (
                "RTO reason base weights, driver weights or class map changed after "
                "approval. If CAL-08 failed, report it as a finding — do not tune "
                "these weights."
            )
        ),
    )


def cal_11_selection_share(
    naive_gap_pp: float,
    average_marginal_effect_pp: float,
    params: Any,
) -> TestResult:
    """CAL-11 (HARD) — the selection share of the naive COD-RTO gap.

    ``selection_share = (naive_gap - AME) / naive_gap``

    Decision A7 made this the real gate on the dataset. The entire analytical
    payoff of the project is the claim that a naive crosstab overstates the causal
    effect of COD by roughly a third. If the selection share lands at 8% the
    confounding is too weak to be worth analysing; if it lands at 60% the planted
    ``is_cod`` coefficient is barely doing anything and the "COD causes RTO" story
    is not what the data contains. **Either way the dataset fails to support the
    case study — regardless of whether the RTO rate levels hit their targets.**

    This is HARD precisely where CAL-03 and CAL-04 no longer are. Those measure
    *levels*, which only one knob steers; this measures the *structure*, which is
    what the project is actually about.

    Note the direction of the fix if this fails: the answer is never to move a
    slope. The selection share is a consequence of the fixed ``is_cod = +1.60``
    against the latent structure in the COD model. A miss means the Phase 1
    assumption set does not produce the claimed one-third — a finding to escalate
    (CLAUDE.md rule 3), not a bug to tune away.
    """
    gate = params.get("selection_share_gate")
    lo, hi = float(gate["lo"]), float(gate["hi"])

    if naive_gap_pp <= 0:
        return TestResult(
            test_id="CAL-11",
            name="Selection share of the naive COD-RTO gap",
            severity=Severity.HARD,
            status=Status.FAIL,
            expected=f"selection share in [{lo:.2f}, {hi:.2f}]",
            actual=f"naive gap is {naive_gap_pp:.2f}pp",
            detail=(
                "The naive COD-prepaid gap is not positive, so the selection share is "
                "undefined. COD orders are not RTO-ing more often than prepaid ones at "
                "all — the planted structure did not materialise."
            ),
        )

    share = (naive_gap_pp - average_marginal_effect_pp) / naive_gap_pp
    passed = lo <= share <= hi

    return TestResult(
        test_id="CAL-11",
        name="Selection share of the naive COD-RTO gap",
        severity=Severity.HARD,
        status=Status.PASS if passed else Status.FAIL,
        expected=f"[{lo:.2f}, {hi:.2f}]",
        actual=f"{share:.3f}",
        delta=(
            f"naive {naive_gap_pp:.2f}pp - AME {average_marginal_effect_pp:.2f}pp "
            f"= {naive_gap_pp - average_marginal_effect_pp:.2f}pp of selection"
        ),
        detail=(
            ""
            if passed
            else (
                "The dataset no longer supports the case study. Do NOT adjust a slope: "
                "the selection share is emergent from the fixed is_cod = +1.60 against "
                "the COD model's latent structure. Escalate as a finding about the "
                "Phase 1 assumption set."
            )
        ),
    )


def run_immutability_tests(
    ledger: CoefficientLedger,
    params: Any,
    model_blocks: tuple[str, ...] = MODEL_BLOCKS,
    results: ResultSet | None = None,
) -> ResultSet:
    """Run the CAL immutability tests. Called after every generation run."""
    results = results if results is not None else ResultSet()
    results.add(cal_09_no_slope_changed(ledger, params, model_blocks))
    results.add(cal_10_reason_weights_frozen(params))
    return results


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _flatten_coefficients(coefficients: dict[str, Any]) -> dict[str, float]:
    """Flatten nested coefficient maps to match the ledger's term naming.

    ``{"geo_tier": {"METRO": -0.45}}`` becomes ``{"geo_tier[METRO]": -0.45}``, which
    is how :meth:`LogitAssembler.add_categorical` records it.
    """
    flat: dict[str, float] = {}
    for term, value in coefficients.items():
        if term == INTERCEPT_TERM:
            continue
        if isinstance(value, dict):
            for level, level_value in value.items():
                flat[f"{term}[{level}]"] = float(level_value)
        elif isinstance(value, (int, float)):
            flat[term] = float(value)
    return flat


def _bit_identical(a: float, b: float) -> bool:
    """Exact equality — CAL-09's tolerance is zero, not 'close enough'."""
    return float(a) == float(b)


def _canonical_sha256(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
