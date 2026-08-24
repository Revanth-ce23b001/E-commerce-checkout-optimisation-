"""Module 11c — ``fct_payment_attempt``. The payment-failure state machine.

Business concept
----------------
This module is the entire evidence base for H11: *what share of COD orders were
caused by prepaid payment friction rather than COD preference?* Without
attempt-level rows there is no way to separate the two, and the question stays
rhetorical.

It also produces deviation **D5**, which turns out to be the sharper finding.
Customers who *tried* to prepay and were forced onto COD have demonstrated
intent, so their orders carry ``paid_via_switch`` and a **−0.45** RTO
coefficient. Switch-COD orders should RTO at roughly 16% against 24% for
intent-COD. So fixing payment reliability does not merely move volume to
prepaid — it recovers *the better half* of COD.

Runs at 11c, after both conversion hurdles (decision A26): only sessions that
reached the payment page **and** intended prepaid can attempt a payment.

Why this file is split in two
-----------------------------
:func:`simulate_payment_outcomes` computes *flags only*, from a block of
uniforms pre-allocated per session. It is called many times inside the
calibration bisection, and it consumes **no** randomness of its own — which is
what keeps the realised COD share a monotone step function of beta_0. Drawing
fresh uniforms per iteration would make calibration a random walk (spec §7.3).

:func:`materialise_attempts` builds the actual table, once, after both
intercepts are solved. Failure *reasons* and timestamps do not affect any
calibration target, so they are drawn at the end where they are cheap.

Do not tune to hit 6.8%
-----------------------
Spec §10.3 sets the expectation deliberately *below* Phase 1's pre-registered
8–15% prior. CAL-07 is SOFT for exactly that reason: a documented wrong prior is
a finding, not a defect.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.random import Generator

# The uniform draws the state machine needs, one per session.
DRAW_NAMES = ("rail", "attempt1", "retry", "attempt2", "terminal", "switch_rail")

ATTEMPT_COLUMNS = [
    "session_id", "attempt_seq", "payment_rail", "attempt_ts", "attempt_amount",
    "payment_success", "failure_reason", "is_retry", "post_failure_action",
]


@dataclass
class PaymentOutcome:
    """Per-session payment results. Boolean masks over ALL sessions, not a subset —
    so indexing never has to be reconciled between calibration iterations."""

    eligible: np.ndarray
    succeeded: np.ndarray
    switched_to_cod: np.ndarray
    abandoned: np.ndarray
    attempt_count: np.ndarray
    rail_index: np.ndarray
    failed_first: np.ndarray
    retried: np.ndarray
    succeeded_second: np.ndarray
    switched_rail: np.ndarray


def allocate_draws(rng: Generator, n_sessions: int) -> dict[str, np.ndarray]:
    """Pre-allocate the state machine's uniforms, indexed by session position.

    Session *i* always sees the same draws regardless of which sessions happen to
    be eligible on a given bisection iteration. That is the property that makes
    the objective monotone and the solve convergent.
    """
    return {name: rng.random(n_sessions) for name in DRAW_NAMES}


def failure_probability(
    params,
    sessions: pd.DataFrame,
    state: pd.DataFrame,
    draws: dict[str, np.ndarray],
    rng: Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """First-attempt failure probability per session, and the chosen rail index.

    Computed once, outside the bisection: it depends on the customer and the
    hour, not on either intercept.
    """
    cfg = params.require("payment_failure")
    rails = list(cfg["rail_mix"])
    rail_probs = _probs(cfg["rail_mix"], rails)
    base_failure = np.array([float(cfg["first_attempt_failure"][r]) for r in rails])

    rail_index = _choice_from_uniform(draws["rail"], rail_probs)

    # Failure-prone customers stay failure-prone (spec §10.2). A NULL history
    # contributes zero, for the same reason it does in the logits (decision A18).
    history_rate = np.nan_to_num(
        state["pit_payment_failure_rate"].to_numpy(dtype=float), nan=0.0
    )
    multiplier = 1.0 + float(cfg["customer_failure_multiplier"]) * history_rate

    # Bank downtime: 3% of HOURS get a 2.2x multiplier. Chosen at the hour level,
    # not per session, so failures CLUSTER. A per-session coin flip would give the
    # same marginal rate and none of the structure an analyst could detect.
    hour_key = (
        pd.to_datetime(sessions["session_start_ts"]).astype("int64") // 10**9 // 3600
    ).to_numpy()
    unique_hours = np.unique(hour_key)
    n_down = int(round(len(unique_hours) * float(cfg["downtime_hour_share"])))
    if n_down:
        down = rng.choice(unique_hours, size=n_down, replace=False)
        multiplier = multiplier * np.where(
            np.isin(hour_key, down), float(cfg["downtime_multiplier"]), 1.0
        )

    p_fail = np.clip(base_failure[rail_index] * multiplier, 0.0, 0.98)
    return p_fail, rail_index


def simulate_payment_outcomes(
    params,
    eligible: np.ndarray,
    p_fail_first: np.ndarray,
    rail_index: np.ndarray,
    draws: dict[str, np.ndarray],
) -> PaymentOutcome:
    """Run the state machine. Deterministic given the pre-allocated draws."""
    cfg = params.require("payment_failure")
    terminal = cfg["terminal"]
    p_cod = float(terminal["switch_to_cod"])
    p_rail = float(terminal["switch_rail"])

    failed_1 = eligible & (draws["attempt1"] < p_fail_first)
    retried = failed_1 & (draws["retry"] < float(cfg["p_retry"]))
    succeeded_2 = retried & (draws["attempt2"] < float(cfg["retry_success"]))

    terminal_failure = failed_1 & ~succeeded_2
    succeeded = eligible & (~failed_1 | succeeded_2)

    u_term = draws["terminal"]
    switch_cod = terminal_failure & (u_term < p_cod)
    switch_rail = terminal_failure & (u_term >= p_cod) & (u_term < p_cod + p_rail)
    abandoned = terminal_failure & (u_term >= p_cod + p_rail)

    rail_rescued = switch_rail & (draws["switch_rail"] < float(cfg["switch_rail_success"]))
    # A failed rail switch still has to end somewhere. It falls back to the same
    # terminal split, so this small branch cannot silently swallow sessions.
    rail_failed = switch_rail & ~rail_rescued
    switch_cod = switch_cod | (rail_failed & (draws["switch_rail"] < p_cod))
    abandoned = abandoned | (rail_failed & (draws["switch_rail"] >= p_cod))
    succeeded = succeeded | rail_rescued

    attempt_count = np.zeros(len(eligible), dtype=np.int16)
    attempt_count[eligible] = 1
    attempt_count[retried] += 1
    attempt_count[switch_rail] += 1

    return PaymentOutcome(
        eligible=eligible,
        succeeded=succeeded,
        switched_to_cod=switch_cod,
        abandoned=abandoned,
        attempt_count=attempt_count,
        rail_index=rail_index,
        failed_first=failed_1,
        retried=retried,
        succeeded_second=succeeded_2,
        switched_rail=switch_rail,
    )


def materialise_attempts(
    params,
    sessions: pd.DataFrame,
    outcome: PaymentOutcome,
    rng: Generator,
) -> pd.DataFrame:
    """Build ``fct_payment_attempt``. Called once, after calibration.

    Failure reasons are drawn here rather than during the bisection because they
    affect no calibration target — only the diagnostic breakdown an analyst reads.
    """
    cfg = params.require("payment_failure")
    rails = np.array(list(cfg["rail_mix"]), dtype=object)
    reason_names = list(cfg["failure_reason_mix"])
    reason_probs = _probs(cfg["failure_reason_mix"], reason_names)

    session_ids = sessions["session_id"].to_numpy()
    session_ts = pd.to_datetime(sessions["session_start_ts"])
    amount = sessions["order_value"].to_numpy(dtype=float)
    rail_names = rails[outcome.rail_index]

    frames: list[pd.DataFrame] = []

    def add(mask: np.ndarray, seq: int, success: bool, offset: int) -> None:
        if not mask.any():
            return
        k = int(mask.sum())
        reasons = (
            np.array([None] * k, dtype=object)
            if success
            else np.array(reason_names, dtype=object)[
                rng.choice(len(reason_names), size=k, p=reason_probs)
            ]
        )
        frames.append(pd.DataFrame({
            "session_id": session_ids[mask],
            "attempt_seq": np.full(k, seq, dtype=np.int16),
            "payment_rail": rail_names[mask],
            "attempt_ts": session_ts[mask].to_numpy() + np.timedelta64(offset, "s"),
            "attempt_amount": amount[mask],
            "payment_success": success,
            "failure_reason": reasons,
            "is_retry": seq > 1,
            "post_failure_action": _actions(outcome, mask, seq, success),
        }))

    add(outcome.eligible & ~outcome.failed_first, 1, True, 45)
    add(outcome.failed_first, 1, False, 45)
    add(outcome.succeeded_second, 2, True, 120)
    add(outcome.retried & ~outcome.succeeded_second, 2, False, 120)

    if not frames:
        return pd.DataFrame(columns=ATTEMPT_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(
        ["session_id", "attempt_seq"], ignore_index=True
    )


def _actions(outcome: PaymentOutcome, mask: np.ndarray, seq: int, success: bool):
    """What the customer did next. NULL on success — nothing to react to."""
    k = int(mask.sum())
    if success:
        return np.array([None] * k, dtype=object)

    action = np.full(k, "ABANDON", dtype=object)
    if seq == 1:
        retried = outcome.retried[mask]
        action = np.where(retried, "RETRY_SAME", action)
        action = np.where(~retried & outcome.switched_to_cod[mask], "SWITCH_TO_COD", action)
        action = np.where(~retried & outcome.switched_rail[mask], "SWITCH_RAIL", action)
    else:
        action = np.where(outcome.switched_to_cod[mask], "SWITCH_TO_COD", action)
        action = np.where(outcome.switched_rail[mask], "SWITCH_RAIL", action)
    return action


def _choice_from_uniform(uniforms: np.ndarray, probs: np.ndarray) -> np.ndarray:
    """Inverse-CDF categorical draw from a fixed uniform block.

    Using the pre-allocated uniforms rather than ``rng.choice`` is what keeps rail
    selection stable across bisection iterations.
    """
    return np.searchsorted(np.cumsum(probs), uniforms, side="right").clip(0, len(probs) - 1)


def _probs(mapping: dict, order: list) -> np.ndarray:
    probs = np.array([float(mapping[k]) for k in order], dtype=np.float64)
    if not np.isclose(probs.sum(), 1.0):
        raise ValueError(f"Weights sum to {probs.sum()}, expected 1.0: {mapping}")
    return probs
