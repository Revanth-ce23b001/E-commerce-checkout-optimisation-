"""LK family — leakage tests. All HARD.

This module currently implements LK-06 only. LK-01 through LK-05 test the
*generated data* and the *live database* (view column lists, training frames,
AUC, point-in-time re-derivation, role grants), so they are written alongside
the modules that produce those artefacts.

LK-06 is different: it can be written now, because it guards a decision rather
than a dataset.

Why LK-06 exists
----------------
Decision A19. The empirical-Bayes prior that ``pit_rto_rate_shrunk`` shrinks
toward must be a **declared constant** in ``params.yaml``, never a value computed
from the generated population.

The failure it prevents is the subtlest leak in the project. Suppose the prior
were computed as the realised in-window RTO rate. Every customer's Stage-1 risk
feature would then be a function of the window's Stage-5 outcomes — including
outcomes from orders placed *after* their session. No column-level check would
catch it: ``pit_rto_rate_shrunk`` is on the safe whitelist, appears in the risk
view legitimately, and looks like exactly what it claims to be. LK-01 and LK-02
compare column *names*, so both would pass. LK-03's AUC would drift upward with
no obviously-wrong column to blame it on.

It would also break calibration outright: the prior would move on every bisection
iteration, so the feature would never be reproducible and DQ-01 would fail for
reasons nobody could locate.
"""

from __future__ import annotations

from typing import Any

from src.validation.result import ResultSet, Severity, Status, TestResult

# Structural key names, not business values.
PRIORS_BLOCK = "priors"
RTO_PRIOR_KEY = "rto_prior"
COD_PRIOR_KEY = "cod_prior"
SHRINKAGE_K_KEY = "shrinkage_k"


def lk_06_shrinkage_prior_is_declared(
    prior_used_at_runtime: float,
    k_used_at_runtime: float,
    params: Any,
    cod_prior_used_at_runtime: float | None = None,
) -> TestResult:
    """LK-06 (HARD) — every declared prior used at runtime matches params.yaml.

    Covers the empirical-Bayes shrinkage prior AND, since decision A39, the two
    centring constants the generator's logits use.

    Parameters
    ----------
    prior_used_at_runtime
        The prior mean the generator actually passed to
        :func:`src.utils.shrinkage.shrink_rate`, captured at call time rather than
        re-read from config. Reading it back from ``params.yaml`` would compare the
        file to a copy of itself and pass no matter what the generator did — the
        same trap CAL-09 is built to avoid.
    k_used_at_runtime
        The shrinkage weight actually used, checked for the same reason.
    params
        The loaded :class:`~src.config.loader.Params`.
    """
    declared_prior = float(params.get(f"{PRIORS_BLOCK}.{RTO_PRIOR_KEY}"))
    declared_k = float(params.get(f"{PRIORS_BLOCK}.{SHRINKAGE_K_KEY}"))
    declared_cod = float(params.get(f"{PRIORS_BLOCK}.{COD_PRIOR_KEY}"))

    problems: list[str] = []
    # Decision A39 made both priors CENTRING constants in the generator's logits
    # as well as the shrinkage prior, which widens what this test has to protect:
    # a centring constant computed from the generated population would shift every
    # customer's deviation by a function of realised outcomes.
    if (cod_prior_used_at_runtime is not None
            and float(cod_prior_used_at_runtime) != declared_cod):
        problems.append(
            f"cod_prior: params.yaml declares {declared_cod!r}, generator used "
            f"{float(cod_prior_used_at_runtime)!r}"
        )
    if float(prior_used_at_runtime) != declared_prior:
        problems.append(
            f"prior: params.yaml declares {declared_prior!r}, generator used "
            f"{float(prior_used_at_runtime)!r}"
        )
    if float(k_used_at_runtime) != declared_k:
        problems.append(
            f"k: params.yaml declares {declared_k!r}, generator used "
            f"{float(k_used_at_runtime)!r}"
        )

    return TestResult(
        test_id="LK-06",
        name="Shrinkage prior is a declared constant, not computed from the data",
        severity=Severity.HARD,
        status=Status.PASS if not problems else Status.FAIL,
        expected=f"rto_prior={declared_prior}, cod_prior={declared_cod}, "
                 f"k={declared_k} (exact)",
        actual=(f"rto_prior={float(prior_used_at_runtime)}, "
                f"cod_prior={cod_prior_used_at_runtime}, k={float(k_used_at_runtime)}"),
        delta="identical" if not problems else "DIFFERS",
        detail=(
            ""
            if not problems
            else (
                " | ".join(problems)
                + " — a prior derived from the generated population is a population-level "
                "leak that no column-level check can detect. Restore the declared "
                "constant (decision A19)."
            )
        ),
    )


def run_config_leakage_tests(
    prior_used_at_runtime: float,
    k_used_at_runtime: float,
    params: Any,
    results: ResultSet | None = None,
) -> ResultSet:
    """Run the LK tests that do not need a live database."""
    results = results if results is not None else ResultSet()
    results.add(
        lk_06_shrinkage_prior_is_declared(prior_used_at_runtime, k_used_at_runtime, params)
    )
    return results
