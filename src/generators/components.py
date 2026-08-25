"""Per-term logit traces for a documented audit sample (decision A45).

What these columns are for
--------------------------
``truth_order_probability.logit_cod_components`` and ``logit_rto_components``
exist so **GT-01 is auditable**. When Phase 5 regresses the generated data and
compares recovered coefficients against planted ones, "the regression matched"
is a weaker claim than "here is one order, and here is every additive term that
produced its probability." The second is what a reviewer can check by hand.

Why a sample and not the whole table
------------------------------------
That audit is a *lookup*, never a bulk scan. Populating all 155,000 sessions
costs roughly 190 MB of JSONB for a query nobody runs at scale. Ruling A45
fixes the sample at 2,000 sessions across four strata: a random draw plus the
three cases anyone would actually open — a COD order that came back, a prepaid
order that came back, and a high-risk order that arrived safely. The nulls are
then explained rather than ambiguous, because ``components_populated`` says
which rows were drawn.

Why the trace is RECONSTRUCTED rather than recorded in the loop
---------------------------------------------------------------
The strata depend on outcomes, so the sample cannot be chosen until the day
loop has finished. The trace is therefore rebuilt afterwards from
``setup`` and the collected arrays.

Reconstruction is the risk: a second implementation of the logit would be free
to drift from the first, and a drifted trace is worse than no trace — it looks
authoritative. Two things prevent that:

1. **One implementation.** The term builders in :mod:`src.generators.predictors`
   and :mod:`src.generators.rto` return *named terms*; the day loop adds their
   sum. This module reads the same functions. There is no parallel copy.
2. **The check executes.** :func:`build_component_traces` re-derives each
   sampled session's probability from its own trace and compares it against the
   probability the day loop stored. A mismatch raises. This is the A44 lesson
   applied to its own remedy: a comment claiming the trace is faithful would
   constrain nothing.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src.generators import predictors as pred
from src.generators import rto as rto_mod
from src.generators.simulate import population_z
from src.models.logit import CoefficientLedger, logistic, sum_terms

# The trace is stored rounded. Six decimals on the logit scale moves a
# probability by less than 1e-7 -- far below anything an audit reads -- while
# keeping the JSONB small. The verification below runs at FULL precision, before
# any rounding, so the tolerance is a property of the arithmetic and not of the
# storage format.
TRACE_DECIMALS = 6

# Full-precision agreement between the reconstruction and the day loop. Both
# evaluate the same terms in the same order, so the only admissible difference is
# the float noise of re-summing.
RECONSTRUCTION_TOL = 1e-9


def build_component_traces(params, setup, solved, extra, sessions_out, rng):
    """Draw the audit sample and build its per-term traces.

    Returns
    -------
    (frame, summary)
        ``frame`` has one row per SAMPLED session with ``session_id``,
        ``logit_cod_components``, ``logit_rto_components`` (JSON text, or None
        where the session produced no shipped order) and ``components_populated``.
        ``summary`` is the provenance recorded in ``_truth.json``.
    """
    cfg = params.require("truth_sampling")
    positions, strata = _draw_sample(cfg, extra, rng)

    cod = _cod_trace(params, setup, solved, extra, positions)
    rto = _rto_trace(params, setup, solved, extra, positions)

    frame = pd.DataFrame({
        "session_id": sessions_out["session_id"].to_numpy()[positions],
        "logit_cod_components": cod["json"],
        "logit_rto_components": rto["json"],
        "components_populated": True,
    })
    summary = {
        "rule": "decision A45 - stratified audit sample, not full population",
        "target_per_stratum": {k: int(v) for k, v in cfg["strata"].items()},
        "drawn_per_stratum": strata,
        "sessions_sampled": int(len(positions)),
        "rto_traces": int(rto["n"]),
        "high_risk_quantile": float(cfg["high_risk_quantile"]),
        "trace_decimals": TRACE_DECIMALS,
        "max_reconstruction_error": {
            "p_cod_intent": cod["max_error"],
            "p_rto_precheckout": rto["max_error_pre"],
            "p_rto_final": rto["max_error_final"],
        },
        "reconstruction_tolerance": RECONSTRUCTION_TOL,
    }
    return frame, summary


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def _draw_sample(cfg, extra: dict, rng) -> tuple[np.ndarray, dict]:
    """The four strata of ruling A45, drawn from the ``truth_sampling`` substream.

    Strata overlap is possible and harmless -- a random draw may land on a COD
    RTO -- so the union is de-duplicated and the realised count is reported
    rather than asserted to be exactly 2,000.

    A stratum that cannot be filled is reported, never silently shortened: a
    quietly half-sized stratum would make the audit sample claim coverage it does
    not have.
    """
    want = cfg["strata"]
    resolved = extra["shipped"] & ~extra["censored"]
    rto = extra["rto_flag"].astype(bool)
    cod = extra["is_cod_order"].astype(bool)
    delivered = resolved & ~rto

    pre = extra["p_rto_precheckout"]
    if delivered.any():
        cut = float(np.nanquantile(pre[delivered], float(cfg["high_risk_quantile"])))
    else:  # pragma: no cover - the dataset always has delivered orders
        cut = np.inf
    high_risk_delivered = delivered & (pre >= cut)

    pools = {
        "random_sessions": np.ones(len(cod), dtype=bool),
        "cod_rto": resolved & rto & cod,
        "prepaid_rto": resolved & rto & ~cod,
        "high_risk_delivered": high_risk_delivered,
    }

    chosen, drawn = [], {}
    for name, pool in pools.items():
        available = np.flatnonzero(pool)
        target = int(want[name])
        take = min(target, len(available))
        if take < target:
            print(f"       WARNING truth_sampling.{name}: wanted {target:,}, "
                  f"only {len(available):,} sessions qualify")
        picked = rng.choice(available, size=take, replace=False) if take else available
        drawn[name] = int(take)
        chosen.append(picked)

    positions = np.unique(np.concatenate(chosen)) if chosen else np.array([], dtype=int)
    return positions, drawn


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------


def _scalar_adjusted(terms: dict, log_ov_base: np.ndarray, coefs: dict,
                     ln_s: float) -> dict:
    """Re-apply decision A36's closed-form price-scalar shift to the value terms.

    The day loop never rebuilds the design matrix for the scalar. Because
    ``log(v*s) = log(v) + log(s)``, it adds the whole effect as a constant to the
    summed static predictor. The trace has to place that shift on the *terms it
    belongs to*, or ``log_order_value`` in the trace would describe a price the
    simulation never used.
    """
    out = dict(terms)
    if "log_order_value" in out:
        out["log_order_value"] = out["log_order_value"] + coefs["lov"] * ln_s
    if "log_order_value_sq" in out:
        out["log_order_value_sq"] = out["log_order_value_sq"] + coefs["lov2"] * (
            2.0 * log_ov_base * ln_s + ln_s**2
        )
    return out


def _cod_trace(params, setup, solved, extra, positions) -> dict:
    """Every additive term of the COD-intent logit, for the sampled sessions."""
    ledger = CoefficientLedger()
    ctx, ln_s = setup["ctx"], float(np.log(solved["price_scalar"]))

    static = _scalar_adjusted(
        pred.cod_static_terms(params, ctx, ledger),
        setup["log_ov_base"],
        {"lov": setup["c_cod_lov"], "lov2": setup["c_cod_lov2"]},
        ln_s,
    )
    dynamic = pred.cod_dynamic_terms(
        setup["cod_dyn"],
        extra["pit_cod_share"][positions],
        extra["pit_success"][positions],
        extra["pit_new"][positions].astype(bool),
        extra["pit_delivered"][positions],
        _failure_rate(extra, positions),
        setup["cod_prior"],
        setup["payment_failure_prior"],
    )

    terms = {"__intercept__": np.full(len(positions), float(solved["beta_0"]))}
    terms.update({k: v[positions] for k, v in static.items()})
    # The COD epsilon is sampling, not a coefficient. Named so the trace adds up.
    terms["noise_eps"] = setup["draws"]["eps_cod"][positions]
    terms.update(dynamic)

    total = sum_terms(terms)
    probability = logistic(total)
    error = _assert_matches(
        probability, extra["p_cod_intent"][positions], "p_cod_intent"
    )
    return {"json": _serialise(terms, {"__total__": total, "__probability__": probability}),
            "max_error": error}


def _rto_trace(params, setup, solved, extra, positions) -> dict:
    """Every additive term of both RTO stages, for the sampled sessions.

    NULL for a sampled session that produced no shipped order -- there is no RTO
    logit to trace. That is the same rule the ``tp_rto_pair`` constraint already
    applies to ``p_rto_precheckout``.
    """
    ledger = CoefficientLedger()
    ctx, ln_s = setup["ctx"], float(np.log(solved["price_scalar"]))

    has_rto = np.isfinite(extra["p_rto_precheckout"][positions])
    rows = positions[has_rto]
    if len(rows) == 0:  # pragma: no cover - never true at project scale
        return {"json": [None] * len(positions), "n": 0,
                "max_error_pre": 0.0, "max_error_final": 0.0}

    static = _scalar_adjusted(
        pred.rto_static_terms(params, ctx, ledger),
        setup["log_ov_base"],
        {"lov": setup["c_rto_lov"], "lov2": 0.0},
        ln_s,
    )
    stage1 = rto_mod.stage1_dynamic_terms(
        setup["rto_dyn"],
        extra["pit_rto_shrunk"][rows],
        extra["pit_new"][rows].astype(bool),
        extra["pit_delivered"][rows],
        extra["pit_cod_share"][rows],
        extra["is_cod_order"][rows],
        extra["switched"][rows],
        ctx["is_month_end"][rows],
        setup["prior"],
        setup["cod_prior"],
    )

    terms = {"__intercept__": np.full(len(rows), float(solved["gamma_0"]))}
    terms.update({k: v[rows] for k, v in static.items()})
    terms.update(stage1)

    precheckout = sum_terms(terms)
    error_pre = _assert_matches(
        logistic(precheckout), extra["p_rto_precheckout"][rows], "p_rto_precheckout"
    )

    # Stage 2. The shock exists only after the parcel moves, so its terms are
    # prefixed rather than mixed in -- a Phase 5 reader must be able to tell which
    # half of the trace was knowable at checkout.
    shock = rto_mod.post_dispatch_shock_terms(
        setup["shock_coef"],
        population_z(ctx["courier_reliability"], rows),
        extra["attempt_delay"][rows],
        extra["seller_dispatch_late"][rows].astype(bool),
        setup["draws"]["nu_std"][rows] * float(solved["noise_sd"]),
    )
    shock = {f"shock.{k}": v for k, v in shock.items()}
    final = precheckout + sum_terms(shock)
    error_final = _assert_matches(
        logistic(final), extra["p_rto_final"][rows], "p_rto_final"
    )

    traced = _serialise({**terms, **shock}, {
        "__total_precheckout__": precheckout,
        "__total_final__": final,
        "__p_precheckout__": logistic(precheckout),
        "__p_final__": logistic(final),
    })
    out: list = [None] * len(positions)
    for slot, payload in zip(np.flatnonzero(has_rto), traced):
        out[int(slot)] = payload
    return {"json": out, "n": len(rows),
            "max_error_pre": error_pre, "max_error_final": error_final}


def _failure_rate(extra, positions) -> np.ndarray:
    """``pit_payment_failure_rate`` rebuilt from the two counters it divides.

    ``pit_arrays`` derives it the same way and does not survive the loop. Decision
    A18's rule holds: no attempts means no denominator, so the rate is NaN and the
    centred term contributes exactly zero.
    """
    failures = extra["pit_failures"][positions]
    attempts = failures + extra["pit_success"][positions]
    return np.where(attempts > 0, failures / np.maximum(attempts, 1), np.nan)


def _assert_matches(rebuilt: np.ndarray, stored: np.ndarray, name: str) -> float:
    """Fail loudly if the trace does not reproduce the probability it describes.

    This is the whole guarantee. A trace that merely *looks* like a decomposition
    is worse than an empty column, because a reader would trust it. Decision A44
    was written about exactly this failure mode, so its remedy is checked at
    runtime rather than asserted in a docstring.
    """
    error = float(np.max(np.abs(rebuilt - stored))) if len(rebuilt) else 0.0
    if not np.isfinite(error) or error > RECONSTRUCTION_TOL:
        worst = int(np.argmax(np.abs(rebuilt - stored)))
        raise ValueError(
            f"Component trace does not reproduce {name}: max |rebuilt - stored| "
            f"= {error:.3e} > {RECONSTRUCTION_TOL:.0e} (worst row {worst}: "
            f"{rebuilt[worst]!r} vs {stored[worst]!r}). The trace and the day loop "
            "have diverged -- a term was added, dropped, or scaled differently. "
            "Do not store the trace until they agree."
        )
    return error


def _serialise(terms: dict[str, np.ndarray], totals: dict[str, np.ndarray]) -> list[str]:
    """One compact JSON object per row, terms plus the totals they sum to.

    The totals are carried so a reader can verify the decomposition without
    re-running anything: add the named terms, compare against ``__total__``.
    """
    names = list(terms)
    stacked = np.column_stack([np.round(terms[n], TRACE_DECIMALS) for n in names])
    extras = {k: np.round(v, TRACE_DECIMALS) for k, v in totals.items()}
    return [
        json.dumps(
            {name: float(stacked[i, j]) for j, name in enumerate(names)}
            | {k: float(v[i]) for k, v in extras.items()},
            separators=(",", ":"),
        )
        for i in range(stacked.shape[0])
    ]
