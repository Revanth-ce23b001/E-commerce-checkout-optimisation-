"""Intercept calibration by bisection.

Business concept
----------------
Five levels in this project are *targets*, not inputs: the COD share, the blended
RTO rate, checkout conversion, and the two pre-window history rates. Each is hit
by solving one intercept — and **only** an intercept. Every slope is fixed a
priori, so if a target cannot be reached, that is a finding about the Phase 1
assumption set, not a bug to tune away (CLAUDE.md rule 3).

The one thing that makes this work
----------------------------------
Bisection needs the realised share to be **monotone** in the intercept. It is
only monotone if the randomness is held fixed across iterations. Draw fresh
uniforms each time and the realised share jitters by more than the tolerance, the
bracket never closes, and the "calibration" becomes a random walk — the exact
failure spec §7.3 warns about with *"run the full realisation pipeline WITHOUT
resampling other randomness"*.

So every objective passed here must consume pre-allocated uniforms from
:func:`src.config.seeds.common_random_numbers`. This module does not enforce that
— it cannot see inside the objective — but :func:`solve_intercept` does detect
the symptom: a non-monotone bracket is reported as an error rather than silently
returning whichever endpoint happened to be closer.

Decision A13: the objective evaluates the **realised draw**, not the expected
share, because the realised draw is what CAL-01 and CAL-05 actually measure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class CalibrationError(RuntimeError):
    """Raised when an intercept cannot be solved for the requested target."""


@dataclass(frozen=True)
class CalibrationResult:
    """The outcome of one intercept solve, for the run manifest and _truth.json."""

    block: str
    intercept: float
    achieved: float
    target: float
    tolerance: float
    iterations: int
    converged: bool

    @property
    def error(self) -> float:
        return self.achieved - self.target

    def describe(self) -> str:
        status = "converged" if self.converged else "DID NOT CONVERGE"
        return (
            f"{self.block}: intercept={self.intercept:+.6f} -> {self.achieved:.4f} "
            f"(target {self.target:.4f} +/-{self.tolerance:.4f}, "
            f"error {self.error:+.4f}, {self.iterations} iters, {status})"
        )


def scaled_tolerance(
    n_units: int, floor: float, n_scaling: float
) -> float:
    """Tolerance for the solve, widened at small n.

    Decision A13. A target of +/-0.004 is finer than one order's worth of
    resolution on a 5,000-row dev dataset, so a solve that is *correct* would
    still be reported as not converged. The tolerance therefore scales with
    sample size and never goes below the floor::

        tol = max(floor, n_scaling / sqrt(n_units))

    At 100,000 units this gives 0.0047; at 5,000 it gives 0.021.
    """
    if n_units <= 0:
        raise CalibrationError("Cannot compute a tolerance for zero units.")
    return max(float(floor), float(n_scaling) / (float(n_units) ** 0.5))


def solve_intercept(
    objective: Callable[[float], float],
    *,
    block: str,
    target: float,
    tolerance: float,
    bracket: tuple[float, float],
    max_iterations: int = 60,
) -> CalibrationResult:
    """Solve for the intercept whose objective equals ``target``.

    Parameters
    ----------
    objective
        Maps an intercept to a realised share. Must be non-decreasing in its
        argument and must reuse the same uniforms on every call.
    block
        The params block being solved, e.g. ``"cod_model"``. Used in messages.
    target, tolerance
        The share to hit and how close counts as hit.
    bracket
        ``(lo, hi)``. Widened automatically up to four times if the target lies
        outside it — a bracket that is merely too narrow is a configuration
        annoyance, not a finding, and should not masquerade as one.
    """
    lo, hi = float(bracket[0]), float(bracket[1])
    if lo >= hi:
        raise CalibrationError(f"{block}: bracket {bracket} is not ordered.")

    f_lo, f_hi = objective(lo), objective(hi)
    iterations = 2

    # Widen a too-narrow bracket before declaring the target unreachable.
    width = hi - lo
    for _ in range(4):
        if f_lo <= target <= f_hi:
            break
        if target < f_lo:
            hi, f_hi = lo, f_lo
            lo -= width
            f_lo = objective(lo)
        else:
            lo, f_lo = hi, f_hi
            hi += width
            f_hi = objective(hi)
        width *= 2
        iterations += 1

    if f_lo > f_hi:
        raise CalibrationError(
            f"{block}: the objective DECREASES with the intercept "
            f"(f({lo:.3f})={f_lo:.4f} > f({hi:.3f})={f_hi:.4f}). Either a sign is "
            "inverted, or the randomness is being resampled between iterations — "
            "the objective must consume pre-allocated common random numbers."
        )

    if not (f_lo <= target <= f_hi):
        raise CalibrationError(
            f"{block}: target {target:.4f} is unreachable. The objective spans "
            f"[{f_lo:.4f}, {f_hi:.4f}] across intercepts [{lo:.3f}, {hi:.3f}]. "
            "With every slope fixed, this means the assumption set cannot produce "
            "the target. Escalate and document it (CLAUDE.md rule 3) — do NOT move "
            "a slope."
        )

    achieved = f_lo
    mid = lo
    while iterations < max_iterations:
        mid = 0.5 * (lo + hi)
        achieved = objective(mid)
        iterations += 1

        if abs(achieved - target) <= tolerance:
            return CalibrationResult(
                block=block, intercept=mid, achieved=achieved, target=target,
                tolerance=tolerance, iterations=iterations, converged=True,
            )
        if achieved < target:
            lo = mid
        else:
            hi = mid

        # The realised share is a step function, so the bracket can collapse
        # before the tolerance is met. Report that honestly rather than looping.
        if hi - lo < 1e-9:
            break

    return CalibrationResult(
        block=block, intercept=mid, achieved=achieved, target=target,
        tolerance=tolerance, iterations=iterations, converged=False,
    )
