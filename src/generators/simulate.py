"""Execute the decision-A1 day loop and solve the three in-window intercepts.

One pass of :func:`simulate_window` runs modules 09→17 across all 90 days at a
given ``(alpha_0, beta_0, gamma_0)``. It consumes **no randomness**: every draw
comes from blocks pre-allocated in :func:`prepare`, indexed by session position.
That is the property bisection needs — without it each iteration would resample
and the solve would be a random walk (spec §7.3).

``collect=True`` additionally materialises the per-order arrays for the final
run. During calibration it stays False, because the aggregate rates are all the
objectives need.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.generators import payment_attempts as pay
from src.generators import predictors as pred
from src.generators import rto as rto_mod
from src.generators.conversion import split_hurdles
from src.generators.orders import draw_cancellations, m2_risk_tier
from src.generators.window import WindowMetrics, WindowState, build_day_index, pit_arrays
from src.models.calibrate import scaled_tolerance, solve_intercept
from src.models.logit import logistic

COD, PREPAID = 1, 0


def prepare(params, sessions, customers, latents, products, sellers, geography,
            dates, rngs, ledger) -> dict:
    """Assemble the static design matrices and pre-allocate every draw."""
    n = len(sessions)
    ctx = pred.session_context(
        sessions, customers, latents, products, sellers, geography, dates
    )

    setup = {
        "n": n,
        "ctx": ctx,
        "cod_static": pred.cod_static(params, ctx, ledger),
        "conv_static": pred.conversion_static(params, ctx, ledger),
        "rto_static": pred.rto_static(params, ctx, ledger),
        "cod_dyn": pred.record_dynamic(params, pred.COD_BLOCK, pred.COD_DYNAMIC, ledger),
        "conv_dyn": pred.record_dynamic(
            params, pred.CONVERSION_BLOCK, pred.CONVERSION_DYNAMIC, ledger
        ),
        "rto_dyn": rto_mod.record_dynamic_coefficients(params, ledger),
        "shock_coef": rto_mod.record_shock_coefficients(params, ledger),
        "pre_window": WindowState.pre_window_arrays(customers),
        "index": build_day_index(sessions, customers),
        "prior": float(params.require("priors.rto_prior")),
        "k": float(params.require("priors.shrinkage_k")),
        "address_share": float(params.require("distributions.conversion.address_hurdle_share")),
        "tier_rules": params.require("distributions.risk_tier_rules"),
        "cancel_rates": params.require("fulfilment.preship_cancel_rate"),
        "window_days": int(params.require("meta.window_days")),
        "n_customers": len(customers),
        "order_value": sessions["order_value"].to_numpy(float),
        "gmv": sessions["prospective_gmv"].to_numpy(float),
    }

    rc, rp, rv, rr = rngs["cod"], rngs["payment"], rngs["conversion"], rngs["rto"]
    rd = rngs["delivery"]
    setup["draws"] = {
        "eps_cod": rc.normal(0.0, float(params.require("cod_model.noise_sd")), n),
        "u_cod": rc.random(n),
        "eps_conv": rv.normal(0.0, float(params.require("conversion_model.noise_sd")), n),
        "u_address": rv.random(n),
        "u_payment_page": rv.random(n),
        "u_cancel": rr.random(n),
        "u_actor": rr.random(n),
        "u_rto": rr.random(n),
        "nu": rr.normal(
            0.0, float(params.require("rto_model.post_dispatch_shock.noise_sd")), n
        ),
        "u_dispatch": rd.normal(0.0, 1.0, n),
        "u_transit": rd.normal(0.0, 1.0, n),
        "u_return": rd.normal(0.0, 1.0, n),
    }
    setup["pay_draws"] = pay.allocate_draws(rp, n)
    setup["p_fail_first"], setup["rail_index"] = pay.failure_probability(
        params, sessions, _shim_state(n), setup["pay_draws"], rp
    )
    setup["params"] = params
    return setup


def _shim_state(n: int) -> pd.DataFrame:
    """Payment failure probability needs a historical failure rate per session.

    It is read from the running counters inside the loop, so the one-off setup
    call supplies NaN — which `failure_probability` treats as "no history", the
    same convention decision A18 uses everywhere else.
    """
    return pd.DataFrame({"pit_payment_failure_rate": np.full(n, np.nan)})


def simulate_window(setup: dict, alpha0: float, beta0: float, gamma0: float,
                    collect: bool = False) -> WindowMetrics:
    """One full pass of the day loop. Deterministic given the pre-allocated draws."""
    p = setup["params"]
    d = setup["draws"]
    idx = setup["index"]
    ctx = setup["ctx"]
    n = setup["n"]

    state = WindowState.from_arrays(setup["n_customers"], setup["pre_window"])
    resolutions: list[list] = [[] for _ in range(setup["window_days"] + 2)]

    converted = np.zeros(n, dtype=bool)
    is_cod_order = np.zeros(n, dtype=bool)
    switched = np.zeros(n, dtype=bool)
    shipped = np.zeros(n, dtype=bool)
    cancelled = np.zeros(n, dtype=bool)
    censored = np.zeros(n, dtype=bool)
    rto_flag = np.zeros(n, dtype=bool)
    p_pre = np.full(n, np.nan)
    p_final = np.full(n, np.nan)
    collected: dict[str, np.ndarray] = {}
    if collect:
        for name in ("attempt_delay", "shock", "days_to_resolve", "resolved_day",
                     "attempts", "dispatch_lag", "pit_cod_share", "pit_rto_shrunk",
                     "pit_placed", "pit_resolved", "pit_delivered", "pit_rto_count",
                     "pit_cod_orders", "pit_success", "pit_failures", "pit_new",
                     "pit_last_order_day"):
            collected[name] = np.full(n, np.nan)
        collected["order_tier"] = np.array([None] * n, dtype=object)
        collected["cancel_actor"] = np.array([None] * n, dtype=object)
        collected["rail_final"] = np.array([None] * n, dtype=object)
        for name in ("cleared_address", "reached_payment", "cod_intent",
                     "pay_succeeded", "pay_abandoned", "attempt_count",
                     "pay_eligible", "failed_first", "retried",
                     "succeeded_second", "switched_rail"):
            collected[name] = np.zeros(n, dtype=np.int64)

    for day, day_batches in enumerate(idx["batches"]):
        # Resolutions from STRICTLY earlier days only (A1's safety argument).
        for cust, is_rto, is_del in resolutions[day]:
            state.resolved[cust] += 1
            state.rto_count[cust] += is_rto
            state.delivered[cust] += is_del

        for positions in day_batches:
            cust = idx["customer_position"][positions]
            pit = pit_arrays(state, cust, setup["prior"], setup["k"])

            # -- module 10: COD intent -------------------------------------
            cod_logit = (
                setup["cod_static"][positions] + beta0 + d["eps_cod"][positions]
                + pred.cod_dynamic(
                    setup["cod_dyn"], pit["cod_share"], pit["prepaid_success"],
                    pit["is_new"], pit["delivered"], pit["payment_failure_rate"],
                )
            )
            cod_intent = d["u_cod"][positions] < logistic(cod_logit)

            # -- modules 11a / 11b: the two hurdles -------------------------
            conv_logit = (
                setup["conv_static"][positions] + alpha0 + d["eps_conv"][positions]
                + setup["conv_dyn"]["pit_is_new_customer"] * pit["is_new"].astype(float)
            )
            p_convert = logistic(conv_logit)
            p_addr, p_pay = split_hurdles(p_convert, setup["address_share"])
            cleared_addr = d["u_address"][positions] < p_addr
            reached = cleared_addr & (d["u_payment_page"][positions] < p_pay)

            # -- module 11c: payment attempts -------------------------------
            eligible = reached & ~cod_intent
            outcome = pay.simulate_payment_outcomes(
                p, eligible,
                _scaled_failure(setup, positions, pit),
                setup["rail_index"][positions],
                {k: v[positions] for k, v in setup["pay_draws"].items()},
            )

            if collect:
                # Session-grain capture, BEFORE filtering to orders: module 09 and
                # the funnel columns are per session, not per order.
                for key, values in (
                    ("pit_cod_share", pit["cod_share"]),
                    ("pit_rto_shrunk", pit["rto_rate_shrunk"]),
                    ("pit_placed", pit["placed"]), ("pit_resolved", pit["resolved"]),
                    ("pit_delivered", pit["delivered"]), ("pit_rto_count", pit["rto_count"]),
                    ("pit_cod_orders", pit["cod_orders"]),
                    ("pit_success", pit["prepaid_success"]),
                    ("pit_failures", pit["payment_failures"]),
                    ("pit_new", pit["is_new"]),
                ):
                    collected[key][positions] = values
                collected["pit_last_order_day"][positions] = state.last_order_day[cust]
                collected["cleared_address"][positions] = cleared_addr
                collected["reached_payment"][positions] = reached
                collected["cod_intent"][positions] = cod_intent
                collected["pay_succeeded"][positions] = outcome.succeeded
                collected["pay_abandoned"][positions] = outcome.abandoned
                collected["attempt_count"][positions] = outcome.attempt_count
                collected["rail_final"][positions] = np.where(
                    outcome.succeeded, setup["rail_index"][positions], -1
                )
                for name, mask in (
                    ("pay_eligible", outcome.eligible),
                    ("failed_first", outcome.failed_first),
                    ("retried", outcome.retried),
                    ("succeeded_second", outcome.succeeded_second),
                    ("switched_rail", outcome.switched_rail),
                ):
                    collected[name][positions] = mask

            # -- module 13: orders ------------------------------------------
            became_order = (reached & cod_intent) | outcome.succeeded | outcome.switched_to_cod
            cod_here = (reached & cod_intent) | outcome.switched_to_cod
            ord_local = np.flatnonzero(became_order)
            if len(ord_local) == 0:
                continue
            ord_pos = positions[ord_local]
            ord_cust = cust[ord_local]

            converted[ord_pos] = True
            is_cod_order[ord_pos] = cod_here[ord_local]
            switched[ord_pos] = outcome.switched_to_cod[ord_local]

            # -- module 14: cancellations. is_shipped is the RTO denominator.
            is_cancelled, actor = draw_cancellations(
                len(ord_pos), d["u_cancel"][ord_pos], d["u_actor"][ord_pos],
                setup["cancel_rates"],
            )
            cancelled[ord_pos] = is_cancelled
            ship = ~is_cancelled
            shipped[ord_pos] = ship

            # -- module 15: pre-checkout score, frozen before Stage-4 info --
            pre = (
                setup["rto_static"][ord_pos] + gamma0
                + rto_mod.stage1_dynamic(
                    setup["rto_dyn"],
                    pit["rto_rate_shrunk"][ord_local], pit["is_new"][ord_local],
                    pit["delivered"][ord_local], pit["cod_share"][ord_local],
                    cod_here[ord_local], outcome.switched_to_cod[ord_local],
                    ctx["is_month_end"][ord_pos],
                )
            )
            p_pre[ord_pos] = logistic(pre)

            # -- module 16: delivery + the post-dispatch shock --------------
            courier_z = _z_of(ctx["courier_reliability"], ord_pos)
            order_day = np.full(len(ord_pos), float(day))
            timeline = rto_mod.delivery_timeline(
                p, order_day,
                ctx["estimated_delivery_days"][ord_pos],
                ctx["base_delivery_days"][ord_pos], courier_z,
                d["u_dispatch"][ord_pos], d["u_transit"][ord_pos],
            )
            shock = rto_mod.post_dispatch_shock(
                setup["shock_coef"], courier_z, timeline["attempt_delay_days"],
                timeline["seller_dispatch_late"], d["nu"][ord_pos],
            )
            final_logit = pre + shock
            p_final[ord_pos] = logistic(final_logit)

            # -- module 17: the draw ----------------------------------------
            drawn = rto_mod.draw_rto(logistic(final_logit), d["u_rto"][ord_pos]) & ship
            resolved_info = rto_mod.resolve_outcomes(
                p, order_day, timeline, drawn, d["u_return"][ord_pos], setup["window_days"]
            )
            is_cens = resolved_info["is_censored"] & ship
            censored[ord_pos] = is_cens
            rto_flag[ord_pos] = drawn & ~is_cens

            # -- counters: placements are exact, resolutions are scheduled ---
            np.add.at(state.placed, ord_cust, 1)
            np.add.at(state.cod_orders, ord_cust[cod_here[ord_local]], 1)
            np.add.at(state.value_sum, ord_cust, setup["order_value"][ord_pos])
            np.add.at(state.value_count, ord_cust, 1)
            state.last_order_day[ord_cust] = float(day)
            np.add.at(state.prepaid_success, cust[outcome.succeeded], 1)
            np.add.at(state.payment_failures, cust[outcome.failed_first], 1)

            observable = ship & ~is_cens
            for j in np.flatnonzero(observable):
                resolutions[min(int(resolved_info["resolved_day_index"][j]) + 1,
                                setup["window_days"] + 1)].append(
                    (ord_cust[j], bool(drawn[j]), bool(not drawn[j]))
                )

            if collect:
                _collect(collected, ord_pos, ord_local, pit, timeline, shock,
                         resolved_info, actor, m2_risk_tier(
                             pit["rto_rate_shrunk"][ord_local], pit["is_new"][ord_local],
                             cod_here[ord_local], setup["tier_rules"]))

    return _metrics(setup, converted, is_cod_order, switched, shipped, cancelled,
                    censored, rto_flag, p_pre, p_final, collected, collect)



def _scaled_failure(setup, positions, pit) -> np.ndarray:
    """Re-apply the customer failure multiplier using CURRENT history."""
    cfg = setup["params"].require("payment_failure")
    base = setup["p_fail_first"][positions]
    history = np.nan_to_num(pit["payment_failure_rate"], nan=0.0)
    return np.clip(base * (1.0 + float(cfg["customer_failure_multiplier"]) * history), 0.0, 0.98)


def _z_of(values: np.ndarray, positions: np.ndarray) -> np.ndarray:
    """Standardise against the WHOLE population, not the day's subset.

    Standardising per day would make a coefficient mean something different on a
    quiet Tuesday than on a month-end Saturday.
    """
    sd = values.std(ddof=0)
    return (values[positions] - values.mean()) / sd if sd > 0 else np.zeros(len(positions))


def _collect(collected, ord_pos, ord_local, pit, timeline, shock, resolved, actor, tier):
    collected["attempt_delay"][ord_pos] = timeline["attempt_delay_days"]
    collected["dispatch_lag"][ord_pos] = timeline["dispatch_lag_days"]
    collected["shock"][ord_pos] = shock
    collected["days_to_resolve"][ord_pos] = resolved["days_to_resolve"]
    collected["resolved_day"][ord_pos] = resolved["resolved_day_index"]
    collected["attempts"][ord_pos] = resolved["delivery_attempts"]
    collected["cancel_actor"][ord_pos] = actor
    collected["order_tier"][ord_pos] = tier


def _metrics(setup, converted, is_cod_order, switched, shipped, cancelled, censored,
             rto_flag, p_pre, p_final, collected, collect) -> WindowMetrics:
    denom = shipped & ~censored
    cod_denom = denom & is_cod_order
    prepaid_denom = denom & ~is_cod_order

    m = WindowMetrics(
        conversion_rate=float(converted.mean()),
        cod_share=float(is_cod_order.sum() / max(converted.sum(), 1)),
        rto_rate_blended=float(rto_flag[denom].mean()) if denom.any() else 0.0,
        rto_rate_cod=float(rto_flag[cod_denom].mean()) if cod_denom.any() else 0.0,
        rto_rate_prepaid=float(rto_flag[prepaid_denom].mean()) if prepaid_denom.any() else 0.0,
        n_orders=int(converted.sum()),
        n_shipped=int(shipped.sum()),
        n_censored=int(censored.sum()),
        n_cancelled=int(cancelled.sum()),
        switch_cod_orders=int(switched.sum()),
    )
    if collect:
        m.extra = {
            "converted": converted, "is_cod_order": is_cod_order, "switched": switched,
            "shipped": shipped, "cancelled": cancelled, "censored": censored,
            "rto_flag": rto_flag, "p_rto_precheckout": p_pre, "p_rto_final": p_final,
            **collected,
        }
    return m


def solve_intercepts(setup: dict, params) -> dict:
    """Alternately solve alpha_0, beta_0 and gamma_0 until all three settle.

    They are coupled through the point-in-time features: conversion changes which
    sessions become orders, COD share changes who is exposed to payment risk, and
    RTO outcomes feed back into ``pit_rto_rate_shrunk`` for later sessions. Solving
    any one in isolation would move the other two.

    Decision A7: ``gamma_0`` is solved against the **blended** RTO rate alone.
    CAL-03 and CAL-04 are emergent and are reported, never solved for.
    """
    search = params.require("calibration_search")
    targets = params.require("calibration_targets")
    n_sessions = setup["n"]

    def tol_for(name: str) -> float:
        return min(
            scaled_tolerance(
                n_sessions,
                float(search["share_tolerance_floor"]),
                float(search["tolerance_n_scaling"]),
            ),
            float(targets[name]["tol"]),
        )

    alpha0 = beta0 = 0.0
    gamma0 = -3.0
    results: dict = {}
    history: list[tuple[float, float, float]] = []

    for _ in range(int(params.require("distributions.conversion.joint_solve_passes"))):
        previous = (alpha0, beta0, gamma0)

        results["conversion_model"] = solve_intercept(
            lambda a: simulate_window(setup, a, beta0, gamma0).conversion_rate,
            block="conversion_model",
            target=float(targets["checkout_conversion"]["target"]),
            tolerance=tol_for("checkout_conversion"),
            bracket=tuple(search["conversion_model"]["bracket"]),
            max_iterations=int(search["max_iterations"]),
        )
        alpha0 = results["conversion_model"].intercept

        results["cod_model"] = solve_intercept(
            lambda b: simulate_window(setup, alpha0, b, gamma0).cod_share,
            block="cod_model",
            target=float(targets["cod_share"]["target"]),
            tolerance=tol_for("cod_share"),
            bracket=tuple(search["cod_model"]["bracket"]),
            max_iterations=int(search["max_iterations"]),
        )
        beta0 = results["cod_model"].intercept

        results["rto_model"] = solve_intercept(
            lambda g: simulate_window(setup, alpha0, beta0, g).rto_rate_blended,
            block="rto_model",
            target=float(targets["rto_rate_blended"]["target"]),
            tolerance=tol_for("rto_rate_blended"),
            bracket=tuple(search["rto_model"]["bracket"]),
            max_iterations=int(search["max_iterations"]),
        )
        gamma0 = results["rto_model"].intercept

        history.append((alpha0, beta0, gamma0))

    drift = {
        "alpha_0": abs(alpha0 - previous[0]),
        "beta_0": abs(beta0 - previous[1]),
        "gamma_0": abs(gamma0 - previous[2]),
    }
    return {
        "alpha_0": alpha0, "beta_0": beta0, "gamma_0": gamma0,
        "calibrations": results, "drift": drift, "history": history,
    }
