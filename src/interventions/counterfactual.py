"""[D] — what an order's RTO probability becomes if the payment method changes.

Two channels, and keeping them apart is the point of the module.

**The truth channel (primary).** ``p_rto_final`` in ``truth_order_probability``
is the probability the generator actually drew from, including the realised
post-dispatch shock. The DGP's RTO logit carries three terms that switch on the
payment method and nothing else:

    is_cod            +1.60
    month_end_x_cod   +0.30   (fires only when both is_cod and is_month_end)
    paid_via_switch   -0.45   (only a COD order can have switched)

So the counterfactual is exact: subtract those three contributions from the
order's own logit and read the probability back. Same shock draw, same latents,
same everything else — a per-order counterfactual rather than a population
average applied to individuals.

**The naive channel (contrast).** What an analyst without the truth file would
do, and what blueprint §10.2 does: take the observed COD−prepaid RTO gap and
apply it to every switched order. _truth.json measures that gap at **17.73pp**
against a true average marginal effect of **9.99pp** — the naive figure is
**1.77x** the truth. Carrying both through the simulation is the only way to show
what the difference is worth in rupees, which is the case study's whole point.

WHAT THE TRUTH CHANNEL IS AND IS NOT USED FOR
---------------------------------------------
It is used to **evaluate** what a policy would produce. It is never used to
**target**: every tier, threshold and eligibility flag in this phase comes from
``m2_score``, which is fitted on the firewalled view. That separation is the
whole design — target with what the business can see, score against what is
true — and it is why the truth channel is legitimate here and would not be
legitimate one function earlier.

An M2 score cannot substitute for it. M2's logit is attenuated by roughly 0.480
(limitation L14, decision A37), so applying an un-attenuated +1.60 to a
compressed logit would overstate the shift. The truth logit is on the scale the
coefficient was planted at.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# The three DGP terms that depend on the payment method. Read from the truth
# file's coefficient ledger rather than typed, so a regenerated dataset with
# different coefficients cannot leave this module quietly stale.
PAYMENT_TERMS = ("is_cod", "month_end_x_cod", "paid_via_switch")

_EPS = 1e-12


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), _EPS, 1.0 - _EPS)
    return np.log(p / (1.0 - p))


def _expit(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


class CodEffect:
    """The planted payment-method contribution to each order's RTO logit."""

    def __init__(self, frame: pd.DataFrame, truth: dict):
        ledger = truth["coefficient_ledger"]["rto_model"]
        self.beta = {name: float(ledger[name]) for name in PAYMENT_TERMS}

        self.is_cod = (frame["payment_method"] == "COD").to_numpy()
        self.is_month_end = frame["is_month_end_window"].to_numpy(bool)
        self.paid_via_switch = frame["paid_via_switch"].to_numpy(bool)
        self.p_actual = frame["p_rto_final"].to_numpy(float)

    def cod_contribution(self) -> np.ndarray:
        """Total logit carried by the three payment terms, as this order stands.

        Zero for a prepaid order by construction: all three terms are gated on
        ``is_cod`` — ``paid_via_switch`` is only ever set on an order that fell
        back to cash after a payment failure.
        """
        cod = self.is_cod.astype(float)
        return (self.beta["is_cod"] * cod
                + self.beta["month_end_x_cod"] * self.is_month_end * cod
                + self.beta["paid_via_switch"] * self.paid_via_switch.astype(float))

    def logit_base(self) -> np.ndarray:
        """The order's logit with every payment-method term removed."""
        return _logit(self.p_actual) - self.cod_contribution()

    def p_if_prepaid(self, dose: float = 1.0) -> np.ndarray:
        """P(RTO) if this order were prepaid.

        ``dose`` < 1 delivers a *fraction* of the causal shift, which is how
        intervention D's partial payment is modelled: a token payment is a
        partial commitment, not a full one. At ``dose = 1`` this is the exact
        planted counterfactual.
        """
        return _expit(_logit(self.p_actual) - dose * self.cod_contribution())

    def p_if_cod(self) -> np.ndarray:
        """P(RTO) if this order were COD. Identity on orders that already are.

        A prepaid order has no ``paid_via_switch``, so the COD contribution it
        would acquire is ``is_cod + month_end_x_cod`` and nothing else.
        """
        acquired = (self.beta["is_cod"]
                    + self.beta["month_end_x_cod"] * self.is_month_end)
        return np.where(self.is_cod, self.p_actual,
                        _expit(_logit(self.p_actual) + acquired))

    # -- the naive contrast ------------------------------------------------

    def p_if_prepaid_naive(self, naive_gap: float) -> np.ndarray:
        """§10.2's arithmetic: subtract the observed gap, floored at zero.

        A flat pp subtraction is not a probability model — it can go negative on
        a low-risk COD order, and the floor is where that shows up. Kept because
        it is exactly what the obvious analysis does.
        """
        return np.maximum(self.p_actual - naive_gap, 0.0)

    def summary(self, truth: dict) -> pd.DataFrame:
        """This population's counterfactual, next to _truth.json's headline.

        **The two are not the same population and must not be read as equal.**
        The truth file's canonical AME (decision A6) is measured over every
        shipped, uncensored COD order in the 90-day window; this simulation runs
        on the M2 *test* window, which is later, riskier, and about a sixth the
        size. The exact identity is checked separately by
        :func:`reconcile_canonical`, on the population the truth file used.

        The canonical figure also holds the two interaction terms fixed and moves
        ``is_cod`` alone. A per-order counterfactual cannot: an order that goes
        prepaid loses its month-end interaction and its payment-failure switch
        along with the main effect. Both are shown so the difference is visible
        rather than buried.
        """
        cod = self.is_cod
        canonical = float(truth["planted_causal_effects"]["cod_on_rto"]
                          ["average_marginal_effect_pp"])
        beta_only = _expit(_logit(self.p_actual[cod]) - self.beta["is_cod"])
        full = self.p_if_prepaid()[cod]
        naive = float(truth["planted_causal_effects"]["cod_on_rto"]
                      ["naive_observed_gap_pp"])
        return pd.DataFrame([
            {"quantity": "truth file canonical AME (is_cod only)",
             "population": "full 90-day window",
             "pp": round(canonical, 4), "note": "_truth.json, decision A6"},
            {"quantity": "is_cod only",
             "population": "M2 test window",
             "pp": round(float((self.p_actual[cod] - beta_only).mean()) * 100, 4),
             "note": "same estimator, later and riskier population"},
            {"quantity": "all three payment terms",
             "population": "M2 test window",
             "pp": round(float((self.p_actual[cod] - full).mean()) * 100, 4),
             "note": "PRIMARY - the exact per-order counterfactual"},
            {"quantity": "naive observed COD-prepaid gap",
             "population": "full 90-day window",
             "pp": round(naive, 4),
             "note": "what 10.2 uses; {:.2f}x the truth".format(
                 naive / max(canonical, _EPS))},
        ])


def naive_bias_profile(beta: float, naive_gap: float,
                       grid=(0.05, 0.10, 0.15, 0.20, 0.26, 0.30, 0.40, 0.50)
                       ) -> pd.DataFrame:
    """Where a flat pp gap over- and under-states the per-order causal effect.

    The headline in ``_truth.json`` — naive 17.73pp against a true AME of
    9.99pp — is a statement about **population averages**. Applied to an
    individual order it does not carry, and it does not even carry with a
    consistent sign, because a logit shift is not a constant pp shift: it is
    largest near p = 0.5 and vanishes at both extremes.

    So a flat subtraction **overstates** the benefit of switching a low-risk
    order and **understates** it for a high-risk one. Every lever in this library
    is aimed at high-risk orders, which is the half of the distribution where the
    naive analysis is conservative — the opposite direction to the headline.
    """
    rows = []
    for p in grid:
        causal = float(p - _expit(_logit(np.array([p]))[0] - beta))
        naive = float(min(p, naive_gap))
        rows.append({"p_cod": p,
                     "causal drop (pp)": round(causal * 100, 2),
                     "naive flat-gap drop (pp)": round(naive * 100, 2),
                     "naive / causal": round(naive / causal, 2),
                     "direction": "overstates" if naive > causal else "understates"})
    return pd.DataFrame(rows)


def naive_crossover(beta: float, naive_gap: float) -> float:
    """The p at which a flat pp gap and the causal logit shift agree."""
    grid = np.linspace(0.01, 0.80, 8000)
    causal = grid - _expit(_logit(grid) - beta)
    naive = np.minimum(grid, naive_gap)
    sign = np.sign(naive - causal)
    flips = np.flatnonzero(np.diff(sign) != 0)
    return float(grid[flips[-1]]) if flips.size else float("nan")


def reconcile_canonical(orders: pd.DataFrame, truth_p: pd.DataFrame,
                        truth: dict) -> dict:
    """Rebuild _truth.json's AME on _truth.json's own population.

    ``rollup._average_marginal_effect`` computes it over every shipped,
    uncensored COD order using ``is_cod`` alone. Reproducing it here to the
    fourth decimal is what licenses this module to apply the same coefficients
    per order. If it ever stops matching, the coefficient ledger and the
    generator have diverged and nothing downstream of here is trustworthy.
    """
    beta = float(truth["coefficient_ledger"]["rto_model"]["is_cod"])
    expected = float(truth["planted_causal_effects"]["cod_on_rto"]
                     ["average_marginal_effect_pp"])
    merged = orders.merge(truth_p[["session_id", "p_rto_final"]], on="session_id")
    mask = (merged["is_shipped"].to_numpy(bool)
            & ~merged["is_censored"].to_numpy(bool)
            & (merged["payment_method"] == "COD").to_numpy())
    p = merged.loc[mask, "p_rto_final"].to_numpy(float)
    rebuilt = float((p - _expit(_logit(p) - beta)).mean()) * 100
    return {"orders": int(mask.sum()), "truth_file_pp": round(expected, 4),
            "rebuilt_pp": round(rebuilt, 4),
            "matches": bool(abs(rebuilt - expected) < 1e-3)}
