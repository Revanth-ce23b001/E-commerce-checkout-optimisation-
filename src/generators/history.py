"""Module 07 — pre-window customer history. Decision A11.

Business concept — read this one carefully
------------------------------------------
This is the module that creates the confounding. Everything the project claims
rests on it.

Pre-window history is generated **from the latents**, using the **same latent
slopes** as the in-window models. A customer whose ``latent_intent`` is high was
already more likely to choose COD before the window opened, and already more
likely to have had a parcel come back. When the window starts, those facts appear
as ``pit_cod_share`` and ``pit_rto_rate_shrunk`` — the two strongest observable
features in the project (+2.20 and +2.80).

That shared structure is exactly why prior RTO predicts future RTO (H3 / BR-02)
and prior COD predicts future COD (BR-03). Draw history independently of the
latents and both features become noise: the confounding never forms, the naive
and adjusted COD estimates converge, and the finding this whole case study exists
to produce — that the raw gap overstates the causal effect by about a third —
evaporates.

**No new slopes.** The two logits below re-use coefficients already approved in
``cod_model`` and ``rto_model``. Only two intercepts are new, and both are pinned
to population targets rather than chosen. The re-used slopes are recorded into
the **parent** ledger block, so if pre-window and in-window ever disagreed about
a coefficient, :class:`CoefficientLedger` would raise before any data reached
validation.

Order *frequency* is deliberately **not** latent-driven: it appears in no Phase 1
hypothesis, so inventing a coefficient for it would be an unanchored assumption.

Spec references
---------------
- Brief §9.5   — the consistency constraints, each unit-tested
- Decision A11 — the full parametrisation

⚠️ ``distributions.pre_window`` is [A27 PROPOSED] and not yet approved.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.random import Generator

from src.models.calibrate import CalibrationResult, scaled_tolerance, solve_intercept
from src.models.logit import CoefficientLedger, LogitAssembler, logistic

# Structural key names.
COD_BLOCK = "cod_model"
RTO_BLOCK = "rto_model"
PRE_COD_BLOCK = "pre_window_cod_model"
PRE_RTO_BLOCK = "pre_window_rto_model"


@dataclass
class HistoryResult:
    """Pre-window history plus everything needed to report the calibration."""

    history: pd.DataFrame
    cod_calibration: CalibrationResult
    rto_calibration: CalibrationResult
    n_pre_window_orders: int
    realised_cod_share: float
    realised_rto_rate: float
    realised_cancel_rate: float


def generate_history(
    params,
    customers: pd.DataFrame,
    latents: pd.DataFrame,
    geography: pd.DataFrame,
    rng: Generator,
    ledger: CoefficientLedger,
) -> HistoryResult:
    """Generate ``dim_customer.pre_window_*`` from the latents.

    Blocking dependency: **module 06**. History must be generated FROM the
    latents — that is the whole point.
    """
    n_customers = len(customers)
    pre_window = params.require("distributions.pre_window")
    search = params.require("calibration_search")

    # --- 1. how many prior orders, per customer -----------------------------
    order_counts = _draw_order_counts(params, customers, rng, pre_window)
    n_orders = int(order_counts.sum())
    if n_orders == 0:
        raise ValueError("No pre-window orders were drawn — history would carry no signal.")

    # Expand to one row per prior order. `owner` maps each order to its customer.
    owner = np.repeat(np.arange(n_customers), order_counts)

    # --- 2. common random numbers -------------------------------------------
    # Drawn ONCE, before either bisection. Reusing them across iterations is what
    # makes the realised share a monotone step function of the intercept; drawing
    # fresh uniforms per iteration would turn calibration into a random walk.
    u_cod = rng.random(n_orders)
    u_cancel = rng.random(n_orders)
    u_rto = rng.random(n_orders)
    u_failure = rng.random(n_orders)

    # --- 3. per-order latent and geography context --------------------------
    tier_by_customer = _tier_by_customer(customers, geography)
    order_tier = tier_by_customer[owner]
    z = {
        name: latents[f"latent_{name}"].to_numpy(dtype=np.float64)[owner]
        for name in ("trust", "liquidity", "intent", "price_sensitivity")
    }

    # --- 4. COD: build slopes once, then solve the intercept -----------------
    cod_slopes = _cod_slope_predictor(params, ledger, z, order_tier, n_orders)
    cod_target = params.require("calibration_targets.pre_window_cod_share")
    tol = scaled_tolerance(
        n_orders,
        float(search["share_tolerance_floor"]),
        float(search["tolerance_n_scaling"]),
    )
    cod_calibration = solve_intercept(
        lambda intercept: float(np.mean(u_cod < logistic(cod_slopes + intercept))),
        block=PRE_COD_BLOCK,
        target=float(cod_target["target"]),
        tolerance=min(tol, float(cod_target["tol"])),
        bracket=tuple(search[PRE_COD_BLOCK]["bracket"]),
        max_iterations=int(search["max_iterations"]),
    )
    is_cod = u_cod < logistic(cod_slopes + cod_calibration.intercept)

    # --- 5. pre-ship cancellations, before the RTO draw ---------------------
    # is_shipped is the RTO-rate denominator (CLAUDE.md invariant 8). Cancelled
    # orders leave the RTO population BEFORE the draw, here as in the window.
    cancel_rate = sum(float(v) for v in params.require("fulfilment.preship_cancel_rate").values())
    is_cancelled = u_cancel < cancel_rate
    is_shipped = ~is_cancelled

    # --- 6. RTO: same pattern, and is_cod is now a real driver --------------
    rto_slopes = _rto_slope_predictor(params, ledger, z, order_tier, is_cod, n_orders)
    rto_target = params.require("calibration_targets.pre_window_rto_rate")
    rto_calibration = solve_intercept(
        lambda intercept: float(
            np.mean(u_rto[is_shipped] < logistic(rto_slopes[is_shipped] + intercept))
        ),
        block=PRE_RTO_BLOCK,
        target=float(rto_target["target"]),
        tolerance=min(tol, float(rto_target["tol"])),
        bracket=tuple(search[PRE_RTO_BLOCK]["bracket"]),
        max_iterations=int(search["max_iterations"]),
    )
    is_rto = is_shipped & (u_rto < logistic(rto_slopes + rto_calibration.intercept))

    # --- 7. prepaid payment history -----------------------------------------
    # Every prepaid order in history eventually succeeded — it became an order.
    # Failures are the attempts along the way, which is what seeds
    # pit_payment_failure_rate and therefore H11.
    # Pre-window switch-to-COD is deliberately NOT modelled: the switch is a live
    # in-session behaviour, and faking it here would inflate CAL-07's numerator
    # with orders that never had a session. Recorded in params, not silent.
    is_prepaid = ~is_cod
    blended_failure = _blended_first_attempt_failure(params)
    had_failure = is_prepaid & (u_failure < blended_failure)

    # --- 8. aggregate to the customer grain ---------------------------------
    history = _aggregate(
        n_customers, owner, order_counts, is_cod, is_rto, is_cancelled,
        is_prepaid, had_failure,
    )
    history.insert(0, "customer_id", customers["customer_id"].to_numpy())

    assert_history_constraints(history)

    return HistoryResult(
        history=history,
        cod_calibration=cod_calibration,
        rto_calibration=rto_calibration,
        n_pre_window_orders=n_orders,
        realised_cod_share=float(is_cod.mean()),
        realised_rto_rate=float(is_rto[is_shipped].mean()),
        realised_cancel_rate=float(is_cancelled.mean()),
    )


# ---------------------------------------------------------------------------
# the two logits — slopes only, re-used from the approved blocks
# ---------------------------------------------------------------------------


def _cod_slope_predictor(
    params, ledger: CoefficientLedger, z: dict, order_tier: np.ndarray, n: int
) -> np.ndarray:
    """Latent + geography terms of the COD logit, with NO intercept.

    Point-in-time terms are absent because none exist before the window opens —
    there is no prior order to compute a COD share from. Only the latents and
    geography are knowable, which is why only those coefficients are re-used.
    """
    coefficients = params.require(f"{COD_BLOCK}.coefficients")
    assembler = LogitAssembler(block=COD_BLOCK, n_rows=n, ledger=ledger)
    assembler.add_numeric("latent_trust", coefficients["latent_trust"], z["trust"])
    assembler.add_numeric("latent_liquidity", coefficients["latent_liquidity"], z["liquidity"])
    assembler.add_numeric("latent_intent", coefficients["latent_intent"], z["intent"])
    assembler.add_numeric(
        "latent_price_sensitivity",
        coefficients["latent_price_sensitivity"],
        z["price_sensitivity"],
    )
    assembler.add_categorical("geo_tier", coefficients["geo_tier"], order_tier)
    return _sum_components(assembler)


def _rto_slope_predictor(
    params,
    ledger: CoefficientLedger,
    z: dict,
    order_tier: np.ndarray,
    is_cod: np.ndarray,
    n: int,
) -> np.ndarray:
    """Latent + geography + is_cod terms of the RTO logit, with NO intercept.

    ``is_cod`` enters at the same **+1.60** the in-window model uses. That is what
    makes prior COD and prior RTO correlate in history the way they will in the
    window — and it is recorded into the ``rto_model`` ledger block, so the two
    uses cannot silently drift apart.
    """
    coefficients = params.require(f"{RTO_BLOCK}.coefficients")
    assembler = LogitAssembler(block=RTO_BLOCK, n_rows=n, ledger=ledger)
    assembler.add_numeric("is_cod", coefficients["is_cod"], is_cod.astype(np.float64))
    assembler.add_numeric("latent_intent", coefficients["latent_intent"], z["intent"])
    assembler.add_numeric("latent_liquidity", coefficients["latent_liquidity"], z["liquidity"])
    assembler.add_numeric("latent_trust", coefficients["latent_trust"], z["trust"])
    assembler.add_categorical("geo_tier", coefficients["geo_tier"], order_tier)
    return _sum_components(assembler)


def _sum_components(assembler: LogitAssembler) -> np.ndarray:
    """Sum the assembled terms WITHOUT requiring an intercept.

    ``linear_predictor()`` insists on an intercept, correctly — but during
    bisection the intercept is the variable, and the ledger would reject the
    second distinct value it saw. So the slopes are summed here and the candidate
    intercept is added as a scalar by the objective.
    """
    total = np.zeros(assembler.n_rows, dtype=np.float64)
    for contribution in assembler.components().values():
        total += contribution
    return total


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _draw_order_counts(
    params, customers: pd.DataFrame, rng: Generator, pre_window: dict
) -> np.ndarray:
    """Zero-inflated negative binomial, capped by tenure.

    Brief §9.5: "A customer cannot have more orders than their tenure plausibly
    allows." A 30-day-old account with 14 prior orders would be an obvious tell,
    and would corrupt the tenure gradient the COD model relies on.
    """
    spec = params.require("distributions.pre_window_orders")
    n = len(customers)

    counts = rng.negative_binomial(float(spec["r"]), float(spec["p"]), size=n)

    # Zero-inflation: a realistic share of customers have never ordered before.
    # These are the customers whose pit_cod_share is NULL (decision A18).
    zero_inflated = rng.random(n) < float(spec["zero_inflation"])
    counts = np.where(zero_inflated, 0, counts)

    max_by_tenure = (
        customers["tenure_days_at_window_start"].to_numpy()
        // int(pre_window["min_days_between_orders"])
    )
    return np.minimum(counts, max_by_tenure).astype(np.int64)


def _tier_by_customer(customers: pd.DataFrame, geography: pd.DataFrame) -> np.ndarray:
    tier_lookup = geography.set_index("geography_id")["geo_tier"]
    return customers["home_geography_id"].map(tier_lookup).to_numpy()


def _blended_first_attempt_failure(params) -> float:
    """Rail-mix-weighted first-attempt failure rate."""
    mix = params.require("payment_failure.rail_mix")
    failure = params.require("payment_failure.first_attempt_failure")
    return float(sum(float(mix[rail]) * float(failure[rail]) for rail in mix))


def _aggregate(
    n_customers: int,
    owner: np.ndarray,
    order_counts: np.ndarray,
    is_cod: np.ndarray,
    is_rto: np.ndarray,
    is_cancelled: np.ndarray,
    is_prepaid: np.ndarray,
    had_failure: np.ndarray,
) -> pd.DataFrame:
    """Roll per-order draws up to the customer grain."""

    def total(flags: np.ndarray) -> np.ndarray:
        return np.bincount(owner, weights=flags.astype(np.float64),
                           minlength=n_customers).astype(np.int64)

    cod = total(is_cod)
    rto = total(is_rto)
    cancelled = total(is_cancelled)

    return pd.DataFrame({
        "pre_window_orders": order_counts,
        "pre_window_delivered": order_counts - rto - cancelled,
        "pre_window_rto_count": rto,
        "pre_window_cod_orders": cod,
        # Every prepaid order that exists is one that eventually went through.
        "pre_window_prepaid_success": total(is_prepaid),
        "pre_window_payment_failures": total(had_failure),
    })


def assert_history_constraints(history: pd.DataFrame) -> None:
    """Brief §9.5: enforce, do not hope.

    Each of these is also a unit test. They are checked here as well because a
    violation means the confounding is being built on incoherent history, and
    that is worth failing the run over rather than discovering at validation.
    """
    orders = history["pre_window_orders"].to_numpy()
    delivered = history["pre_window_delivered"].to_numpy()
    rto = history["pre_window_rto_count"].to_numpy()
    cod = history["pre_window_cod_orders"].to_numpy()
    prepaid_success = history["pre_window_prepaid_success"].to_numpy()
    failures = history["pre_window_payment_failures"].to_numpy()

    checks = {
        "counts are non-negative": (
            (orders >= 0) & (delivered >= 0) & (rto >= 0) & (cod >= 0)
            & (prepaid_success >= 0) & (failures >= 0)
        ),
        "delivered + rto <= orders": delivered + rto <= orders,
        "cod_orders <= orders": cod <= orders,
        "prepaid_success <= orders - cod_orders": prepaid_success <= orders - cod,
        "payment_failures <= prepaid orders": failures <= orders - cod,
    }
    for name, ok in checks.items():
        violations = int((~ok).sum())
        if violations:
            raise ValueError(
                f"Pre-window history violates '{name}' for {violations} customer(s). "
                "Brief §9.5 requires these to be enforced, not hoped for."
            )

    if (orders == 0).sum() == 0:
        raise ValueError(
            "No customer has zero pre-window orders. Zero-inflation is required: "
            "these are the historyless customers whose pit_cod_share is NULL "
            "(decision A18), and without them that whole code path is untested."
        )
