"""[D] — the arithmetic. No assumption and no policy enters this file.

Every treated COD order faces exactly three branches, and the whole simulation is
those three branches priced per order:

    switches to prepaid    with probability  s   ->  earns the prepaid EV
    does not happen        with probability  a   ->  earns nothing, and its
                                                     baseline EV is forgone
    stays COD              with probability 1-s-a -> earns the COD EV under the
                                                     lever's cash terms

    E[dCM] = s.EV_prepaid + a.0 + (1-s-a).EV_cod_treated  -  EV_baseline

The sign structure is blueprint §10.2's and it is why targeting matters at all:
the `a` branch is a **loss** wherever the baseline EV is positive and a **gain**
wherever it is negative. That flips exactly at p-star, which is why p-star is the
high-risk line and not a percentile.

An already-prepaid order in a treated cohort is not inert. It collects any
incentive the lever pays — pure leakage, since it was going to pay online anyway
— and that leakage is the single reason a flat prepaid incentive destroys value
in §10.2 while a targeted one creates it.

What is held fixed, and why
---------------------------
* **Pre-ship cancellation** is payment-method-independent in the DGP, so an order
  that switches rails still cancels at the same rate. Cancelled orders carry zero
  on every line (decision A23), so they drop out of the margin arithmetic
  entirely and are carried only in the session denominator.
* **The post-dispatch shock** stays with the order. A counterfactual that
  re-drew it would be comparing two different days of courier luck rather than
  two payment methods.
* **Order value** does not move. No lever in the library changes what is in the
  basket; if one did, the switch would also be a mix shift and the two effects
  would need separating.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TIERS = ("LOW", "MED", "HIGH")


class Simulation:
    """Baseline and treated expectations for one population, in one place."""

    def __init__(self, frame: pd.DataFrame, econ, effect, sessions: int):
        self.frame = frame.reset_index(drop=True)
        self.econ = econ
        self.effect = effect
        self.sessions = int(sessions)

        self.is_cod = (self.frame["payment_method"] == "COD").to_numpy()
        self.p_base = effect.p_actual
        self.p_prepaid = effect.p_if_prepaid()

        # Baseline expectations, computed once. Every lever is scored against
        # these, so a lever's delta never depends on which lever ran before it.
        self.ev_base = econ.expected(self.p_base, self.is_cod)

    # -- baseline ----------------------------------------------------------

    def baseline(self, tiers: pd.Series) -> pd.DataFrame:
        rows = []
        for label, mask in self._blocks(tiers):
            rows.append({
                "tier": label,
                "orders": int(mask.sum()),
                "conversion": round(float(mask.sum()) / self.sessions, 4),
                "cod_share": round(float(self.is_cod[mask].mean()), 4),
                "rto_rate": round(float(self.p_base[mask].mean()), 4),
                "cm_per_order": round(float(self.ev_base[mask].mean()), 2),
                "cm_per_session": round(float(self.ev_base[mask].sum())
                                        / self.sessions, 2),
                "realised_rto_rate": round(
                    float(self.frame.loc[mask, "rto_flag"].mean()), 4),
                "realised_cm_per_order": round(
                    float(self.frame.loc[mask, "contribution_margin"].mean()), 2),
            })
        return pd.DataFrame(rows)

    def _blocks(self, tiers: pd.Series, eligible=None):
        """Risk-tier blocks, the whole population, and — if given — the treated set.

        The TREATED row is not a tier. It exists because blueprint 12.1 states the
        RTO clause "in treated cohorts", and measuring that on a risk tier would
        dilute it with the untreated orders that happen to sit in the same tier.
        It is emitted last and excluded from anything that sums across tiers.
        """
        tiers = pd.Series(np.asarray(tiers)).reset_index(drop=True)
        for tier in TIERS:
            yield tier, (tiers == tier).to_numpy()
        yield "ALL", np.ones(len(self.frame), dtype=bool)
        if eligible is not None and np.asarray(eligible).any():
            yield "TREATED", np.asarray(eligible, dtype=bool)

    # -- one lever ---------------------------------------------------------

    def run(self, eligible: np.ndarray, can_switch: np.ndarray, resp: dict) -> dict:
        """Per-order expected counts and margins under one lever's response.

        ``eligible`` is who the lever is shown to; ``can_switch`` is who it can
        move. They differ for intervention A, where the offer reaches every order
        in the cohort — including the ones already paying online, which collect
        the incentive and switch nothing. Collapsing the two would delete the
        leakage that decides whether a flat incentive is worth running.

        Returns arrays, not summaries: the caller slices them by tier, by geo,
        or by anything else without the simulation needing to know about it.
        """
        n = len(self.frame)
        s, a = resp["switch"], resp["abandon"]
        a_pre, dose = resp["abandon_prepaid"], resp["dose"]

        treated_cod = eligible & can_switch & self.is_cod
        treated_pre = eligible & ~self.is_cod

        # The COD order that stays COD. Under D it stays COD *with a token*, so
        # its RTO probability moves by a fraction of the full causal shift and
        # its collection splits across two rails.
        p_stay = np.where(dose > 0, self.effect.p_if_prepaid(dose), self.p_base)
        ev_stay = self.econ.expected(
            p_stay, np.ones(n, bool), fee=resp["cod_fee"],
            partial=resp["partial"], partial_retained=resp["partial_retained"])
        ev_switch = self.econ.expected(
            self.p_prepaid, np.zeros(n, bool), incentive=resp["incentive"])
        ev_prepaid_leak = self.econ.expected(
            self.p_base, np.zeros(n, bool), incentive=resp["incentive"])

        # --- expected orders --------------------------------------------------
        orders = np.ones(n)
        orders[treated_cod] = 1.0 - a
        orders[treated_pre] = 1.0 - a_pre

        # --- expected COD orders ---------------------------------------------
        cod_orders = self.is_cod.astype(float).copy()
        cod_orders[treated_cod] = 1.0 - a - s

        # --- expected RTO count ----------------------------------------------
        rto = self.p_base.copy()
        rto[treated_cod] = s * self.p_prepaid[treated_cod] + (1 - a - s) * p_stay[treated_cod]
        rto[treated_pre] = (1 - a_pre) * self.p_base[treated_pre]

        # --- expected contribution margin ------------------------------------
        cm = self.ev_base.copy()
        cm[treated_cod] = (s * ev_switch[treated_cod]
                           + (1 - a - s) * ev_stay[treated_cod])
        cm[treated_pre] = (1 - a_pre) * ev_prepaid_leak[treated_pre]

        return {"orders": orders, "cod_orders": cod_orders, "rto": rto, "cm": cm,
                "eligible": eligible, "treated_cod": treated_cod,
                "treated_prepaid": treated_pre, "response": resp}


def summarise(sim: Simulation, result: dict, tiers: pd.Series,
              label: str) -> pd.DataFrame:
    """Per-tier deltas: conversion, COD share, RTO, CM/order, CM/session.

    Three denominators are in play and confusing them is easy, so all three are
    named.

    ``d_rto_pp`` divides by orders that still happen, per CLAUDE.md invariant 8 —
    an intervention that removes orders must not be allowed to book that as an
    RTO improvement for free.

    ``d_cm_per_session`` divides by the FULL checkout-session count, which is
    fixed, so the tier rows add up to the total.

    **Two conversion metrics, and they can move in opposite directions.**
    ``d_conversion_rel_pct`` is orders over sessions — §5.3's *checkout*
    conversion, "kept as guardrail". ``d_net_conversion_rel_pct`` is DELIVERED
    orders over sessions — §5.3's *net* conversion, "the real conversion
    metric", and the one §12.1 names in its aggregate floor. A lever that removes
    orders which were going to come back anyway loses checkout conversion and
    *gains* net conversion. Reporting only one of them would let a lever be
    argued through on whichever metric happened to suit it, which is §12.4's
    warning pointed at the conversion side of the ledger instead of the RTO side.
    """
    rows = []
    for tier, mask in sim._blocks(tiers, result["eligible"]):
        n0 = float(mask.sum())
        n1 = float(result["orders"][mask].sum())
        cod0 = float(sim.is_cod[mask].sum())
        cod1 = float(result["cod_orders"][mask].sum())
        rto0 = float(sim.p_base[mask].sum())
        rto1 = float(result["rto"][mask].sum())
        cm0 = float(sim.ev_base[mask].sum())
        cm1 = float(result["cm"][mask].sum())
        delivered0, delivered1 = n0 - rto0, n1 - rto1

        rows.append({
            "arm": label,
            "tier": tier,
            "orders": int(n0),
            "treated": int(result["eligible"][mask].sum()),
            "treated_pct": round(float(result["eligible"][mask].mean()) * 100, 1),
            "d_orders": round(n1 - n0, 1),
            "d_conversion_rel_pct": round((n1 / n0 - 1.0) * 100, 3) if n0 else 0.0,
            "d_net_conversion_rel_pct": round(
                (delivered1 / delivered0 - 1.0) * 100, 3) if delivered0 else 0.0,
            "d_cod_share_pp": round((_safe(cod1, n1) - _safe(cod0, n0)) * 100, 2),
            "d_rto_pp": round((_safe(rto1, n1) - _safe(rto0, n0)) * 100, 2),
            "d_cm_per_order": round(_safe(cm1, n1) - _safe(cm0, n0), 2),
            "d_cm_total": round(cm1 - cm0, 0),
            "d_cm_per_session": round((cm1 - cm0) / sim.sessions, 3),
        })
    return pd.DataFrame(rows)


def _safe(num: float, den: float) -> float:
    return num / den if den else 0.0


def naive_contrast(sim: Simulation, eligible: np.ndarray, can_switch: np.ndarray,
                   resp: dict, naive_gap_pp: float) -> dict:
    """What the same lever looks like if switching is priced at the OBSERVED gap.

    Blueprint §10.2 prices a switch at ``(24.0% - 4.1%) x ₹416`` — the observed
    COD/prepaid RTO difference, applied flat. _truth.json measures that gap at
    1.77x the true average marginal effect, so this arm is the same simulation
    with one substitution: the counterfactual prepaid probability comes from the
    rate gap instead of from the planted structural shift.

    It is not a sensitivity. It is the analysis a competent analyst without the
    truth file would produce, and the difference between the two is what the
    causal work in Phase 3 is worth in rupees.
    """
    n = len(sim.frame)
    s, a = resp["switch"], resp["abandon"]
    treated_cod = eligible & can_switch & sim.is_cod
    p_naive = sim.effect.p_if_prepaid_naive(naive_gap_pp / 100.0)

    ev_switch = sim.econ.expected(p_naive, np.zeros(n, bool),
                                  incentive=resp["incentive"])
    # A partial payment delivers a FRACTION of the switch effect, so its naive
    # analogue is the same fraction of the flat gap. Leaving the dose out here
    # would compare D's causal mechanism against no mechanism at all, which
    # measures the existence of the dose rather than the size of the gap.
    p_stay_naive = (np.maximum(sim.p_base - resp["dose"] * naive_gap_pp / 100.0, 0.0)
                    if resp["dose"] > 0 else sim.p_base)
    ev_stay = sim.econ.expected(
        p_stay_naive, np.ones(n, bool), fee=resp["cod_fee"],
        partial=resp["partial"], partial_retained=resp["partial_retained"])

    cm = sim.ev_base.copy()
    cm[treated_cod] = s * ev_switch[treated_cod] + (1 - a - s) * ev_stay[treated_cod]
    if resp["incentive"] > 0:
        leak = eligible & ~sim.is_cod
        cm[leak] = sim.econ.expected(sim.p_base, np.zeros(n, bool),
                                     incentive=resp["incentive"])[leak]
    return {"cm": cm, "d_cm_total": float(cm.sum() - sim.ev_base.sum())}
