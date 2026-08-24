"""Modules 10–12 orchestration: the joint solve for beta_0 and alpha_0.

Why these two intercepts cannot be solved independently
-------------------------------------------------------
They are coupled in both directions.

* Conversion depends on COD share, because only **prepaid-intent** sessions can
  hit a payment failure, and a terminal failure removes ~20% of those sessions
  from the order population. Raise beta_0 (more COD intent) and fewer sessions
  are exposed to payment risk, so conversion rises on its own.
* COD share depends on conversion, because the 62% target is measured on
  **orders**, not sessions, and the two hurdles change *which* sessions become
  orders.

So each is solved with the other held fixed, alternately, until both stop moving.
Three passes is enough in practice; the runner reports the drift on the final
pass so a silent non-convergence cannot pass as a solve.

Everything the objectives consume is pre-allocated before the first pass:
epsilon, nu, and every uniform in the payment state machine. That is what keeps
both objectives monotone step functions of their intercept, which is the only
condition bisection needs (spec §7.3).

⚠️ PROVISIONAL. Modules 13–17 do not exist, so no in-window order ever resolves
and every ``pit_*`` feature reflects pre-window history alone. Once the day loop
from decision A1 closes, ``pit_cod_share`` and ``pit_rto_rate_shrunk`` will carry
in-window history too and **both intercepts must be re-solved**. The values
produced here are a working checkpoint, not a calibration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.random import Generator

from src.generators import conversion as conv
from src.generators import payment_attempts as pay
from src.generators.cod_choice import build_cod_intent_predictor
from src.models.calibrate import CalibrationResult, scaled_tolerance, solve_intercept
from src.models.logit import CoefficientLedger, logistic


@dataclass
class CheckoutResult:
    """Resolved sessions plus everything needed to report the solve."""

    sessions: pd.DataFrame
    payment_attempts: pd.DataFrame
    checkout_events: pd.DataFrame
    truth: pd.DataFrame
    cod_calibration: CalibrationResult
    conversion_calibration: CalibrationResult
    conversion_rate: float
    cod_share: float
    switch_cod_share_of_cod: float
    switch_cod_share_of_orders: float
    abandon_breakdown: dict[str, float] = field(default_factory=dict)
    solve_drift: dict[str, float] = field(default_factory=dict)


def run_checkout(
    params,
    sessions: pd.DataFrame,
    state: pd.DataFrame,
    customers: pd.DataFrame,
    latents: pd.DataFrame,
    products: pd.DataFrame,
    sellers: pd.DataFrame,
    geography: pd.DataFrame,
    dates: pd.DataFrame,
    rng_cod: Generator,
    rng_payment: Generator,
    rng_conversion: Generator,
    ledger: CoefficientLedger,
) -> CheckoutResult:
    """Solve both intercepts, then materialise the resolved checkout."""
    n = len(sessions)
    search = params.require("calibration_search")
    conv_cfg = params.require("distributions.conversion")
    address_share = float(conv_cfg["address_hurdle_share"])

    # --- design matrices, built once ----------------------------------------
    cod_slopes, cod_components = build_cod_intent_predictor(
        params, sessions, state, customers, latents, products, sellers,
        geography, dates, ledger,
    )
    conv_slopes, conv_components = conv.build_conversion_predictor(
        params, sessions, state, ledger
    )

    # --- common random numbers, drawn once ----------------------------------
    eps_cod = rng_cod.normal(0.0, float(params.require("cod_model.noise_sd")), size=n)
    u_cod = rng_cod.random(n)
    eps_conv = rng_conversion.normal(
        0.0, float(params.require("conversion_model.noise_sd")), size=n
    )
    u_address = rng_conversion.random(n)
    u_payment_page = rng_conversion.random(n)
    pay_draws = pay.allocate_draws(rng_payment, n)
    p_fail_first, rail_index = pay.failure_probability(
        params, sessions, state, pay_draws, rng_payment
    )

    def realise(beta_0: float, alpha_0: float) -> dict:
        """One full pass of modules 10 -> 11a -> 11b -> 11c, no randomness consumed."""
        is_cod_intent = u_cod < logistic(cod_slopes + beta_0 + eps_cod)
        hurdles = conv.draw_hurdles(
            conv_slopes, alpha_0, eps_conv, u_address, u_payment_page, address_share
        )
        eligible = hurdles["reached_payment_page"] & ~is_cod_intent
        outcome = pay.simulate_payment_outcomes(
            params, eligible, p_fail_first, rail_index, pay_draws
        )
        converted = (
            (hurdles["reached_payment_page"] & is_cod_intent)
            | outcome.succeeded
            | outcome.switched_to_cod
        )
        cod_orders = (hurdles["reached_payment_page"] & is_cod_intent) | outcome.switched_to_cod
        return {
            "is_cod_intent": is_cod_intent,
            "hurdles": hurdles,
            "outcome": outcome,
            "converted": converted,
            "cod_orders": cod_orders,
            "conversion_rate": float(converted.mean()),
            "cod_share": float(cod_orders.sum() / max(converted.sum(), 1)),
        }

    # --- alternating solve ---------------------------------------------------
    cod_target = float(params.require("calibration_targets.cod_share.target"))
    conv_target = float(params.require("calibration_targets.checkout_conversion.target"))
    tol = scaled_tolerance(
        n, float(search["share_tolerance_floor"]), float(search["tolerance_n_scaling"])
    )

    beta_0 = 0.0
    alpha_0 = 0.0
    conv_result = cod_result = None

    for _ in range(int(conv_cfg["joint_solve_passes"])):
        previous = (beta_0, alpha_0)

        conv_result = solve_intercept(
            lambda a: realise(beta_0, a)["conversion_rate"],
            block="conversion_model", target=conv_target,
            tolerance=min(tol, float(params.require("calibration_targets.checkout_conversion.tol"))),
            bracket=tuple(search["conversion_model"]["bracket"]),
            max_iterations=int(search["max_iterations"]),
        )
        alpha_0 = conv_result.intercept

        cod_result = solve_intercept(
            lambda b: realise(b, alpha_0)["cod_share"],
            block="cod_model", target=cod_target,
            tolerance=min(tol, float(params.require("calibration_targets.cod_share.tol"))),
            bracket=tuple(search["cod_model"]["bracket"]),
            max_iterations=int(search["max_iterations"]),
        )
        beta_0 = cod_result.intercept

    drift = {"beta_0": abs(beta_0 - previous[0]), "alpha_0": abs(alpha_0 - previous[1])}

    # --- materialise at the solved intercepts --------------------------------
    final = realise(beta_0, alpha_0)
    resolved = conv.assemble_conversion(
        params, sessions, final["hurdles"], final["is_cod_intent"],
        final["outcome"], conv_components,
    )
    attempts = pay.materialise_attempts(params, resolved, final["outcome"], rng_payment)
    events = conv.project_checkout_events(resolved)

    truth = pd.DataFrame({
        "session_id": sessions["session_id"].to_numpy(),
        "p_convert": np.round(final["hurdles"]["p_convert"], 5),
        "p_cod_intent": np.round(logistic(cod_slopes + beta_0 + eps_cod), 5),
        "logit_cod_components": _component_json(cod_components),
    })

    cod_orders = final["cod_orders"]
    switch = resolved["switched_to_cod_after_failure"].to_numpy()
    converted = final["converted"]

    return CheckoutResult(
        sessions=resolved,
        payment_attempts=attempts,
        checkout_events=events,
        truth=truth,
        cod_calibration=cod_result,
        conversion_calibration=conv_result,
        conversion_rate=final["conversion_rate"],
        cod_share=final["cod_share"],
        switch_cod_share_of_cod=float(switch.sum() / max(cod_orders.sum(), 1)),
        switch_cod_share_of_orders=float(switch.sum() / max(converted.sum(), 1)),
        abandon_breakdown=_abandon_breakdown(resolved),
        solve_drift=drift,
    )


def _abandon_breakdown(resolved: pd.DataFrame) -> dict[str, float]:
    """Share of ALL sessions lost at each step. The Branch-5 diagnosis."""
    steps = resolved["abandon_step"].to_numpy()
    total = len(resolved)
    return {
        step: float((steps == step).sum() / total)
        for step in ("ADDRESS", "PAYMENT_PAGE", "FEE_REVEAL", "PAYMENT_FAILURE")
    }


def _component_json(components: dict[str, np.ndarray]) -> list[str]:
    """Per-row named component traces, for the JSONB column in schema ``truth``.

    Kept as compact strings rather than dicts so the parquet round-trip is stable
    and the PostgreSQL COPY can hand them straight to a JSONB column.
    """
    names = list(components)
    stacked = np.column_stack([components[n] for n in names])
    return [
        "{" + ",".join(f'"{n}":{stacked[i, j]:.5f}' for j, n in enumerate(names)) + "}"
        for i in range(stacked.shape[0])
    ]
