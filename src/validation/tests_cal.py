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
REASONS_BLOCK = "rto_reasons"
FROZEN_HASH_KEY = "frozen_hash"
IMMUTABLE_REASON_KEYS = ("base_weights", "driver_weights", "class_map")

# Every model block whose slopes CAL-09 protects. Three intercepts may be solved
# (decision A2 admits conversion_model_alpha0, which spec §9.1 already lists in
# calibrated_intercepts); ZERO slopes may move, in any of the three.
MODEL_BLOCKS = ("cod_model", "rto_model", "conversion_model")


def cal_09_no_slope_changed(
    ledger: CoefficientLedger,
    params: Any,
    model_blocks: tuple[str, ...] = MODEL_BLOCKS,
) -> TestResult:
    """CAL-09 (HARD) — no slope coefficient differs from params.yaml.

    Compares every coefficient the generator actually used against the declared value.
    Intercepts are excluded: they are the values the calibrator is permitted to solve.

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
    if unused:
        problems.append(
            f"{len(unused)} coefficient(s) declared but never used: "
            + ", ".join(sorted(unused))
            + " — a planted relationship is silently absent from the data"
        )

    return TestResult(
        test_id="CAL-09",
        name="No slope coefficient differs from params.yaml",
        severity=Severity.HARD,
        status=Status.PASS if not problems else Status.FAIL,
        expected="exact match on every slope (tolerance 0)",
        actual=f"{checked} slope(s) verified across {len(model_blocks)} block(s)",
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
