"""[D] — per-order contribution margin under a counterfactual payment method.

Everything in this module is a DERIVATION. There is no behavioural assumption
anywhere in it: given an order, a payment method, an outcome and a lever's cash
terms, it returns the rupees. The [A] half lives in ``config/interventions.yaml``
and never crosses into here.

Why not just use ``fct_order_economics.contribution_margin``
-----------------------------------------------------------
Because that column is the margin of the order **that actually happened**. An
intervention asks what the same order would have earned as a *prepaid* order, or
as a COD order carrying a fee, or as a delivered order when it in fact returned.
None of those rows exist.

So the margin is rebuilt line by line from ``params.yaml``'s cost registry, with
the **realised draws reused for every cost line that does not depend on the
payment method** — forward freight, packaging, and the goods value. Those were
drawn once per order at generation time and they are properties of the parcel,
not of how it was paid for. Reusing them keeps the counterfactual anchored to
this order rather than to a population average, which is the failure mode
blueprint §10.2 has: it prices every switch at one ₹416 swing.

Three deliberate departures from ``src/economics/order_economics.py``
--------------------------------------------------------------------
1. **A fee does not increase COGS.** The generator computes
   ``cogs = cogs_ratio × net_revenue`` and folds ``cod_fee`` into net revenue, so
   a ₹39 convenience fee would silently add ₹29 of procurement cost. It does not:
   the fee buys no goods. COGS here is pinned to ``cogs_value`` — the realised
   goods value, computed at a zero fee — so it is invariant to every lever.
   Without this, B's economics would be understated by roughly three quarters.

2. **Outcome-conditional cost lines are used in EXPECTATION, not as realised.**
   Reverse freight, reverse handling, shrink and working capital are drawn only
   on orders that actually returned. A counterfactual needs them on orders that
   did not, so they are taken at their registry expectations. On a returned
   order this replaces a realised draw with its mean; the reconciliation in
   ``reconcile`` reports what that costs.

3. **Support/NDR is the registry mean, not ``base + slope × attempts``.**
   ``delivery_attempts`` is behind the Stage-4 bar (phase4_closeout §2.3), and a
   counterfactual order has no attempt count anyway. Decision A38 solved the base
   so the realised mean is the registry's ₹18.00, so the mean is the right value
   and it is also the only one this module is entitled to.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Expected days of blocked working capital for a lognormal(mu, sigma):
# exp(mu + sigma^2 / 2). Written as a function so the registry stays the source.
def _expected_wc_days(cfg: dict) -> float:
    wc = cfg["wc_days_blocked"]
    return float(np.exp(float(wc["mu"]) + float(wc["sigma"]) ** 2 / 2))


class OrderEconomics:
    """Per-order margin under any (method, outcome, lever) combination.

    Vectorised over the whole population: every method returns an array aligned
    with the frame it was constructed from.
    """

    def __init__(self, frame: pd.DataFrame, params, effective_pg_rate: float):
        cfg = params.require("economics")
        self.cfg = cfg
        self.n = len(frame)

        # --- payment-method-independent, taken from the realised draws --------
        self.order_value = frame["order_value"].to_numpy(float)
        self.forward = frame["forward_shipping_cost"].to_numpy(float)
        self.packaging = frame["packaging_cost"].to_numpy(float)
        self.cogs_value = frame["cogs_value"].to_numpy(float)
        self.shrink_rate = frame["shrink_rate"].to_numpy(float)
        self.weight_factor = frame["weight_factor"].to_numpy(float)

        # --- registry constants ----------------------------------------------
        self.pg_rate = float(effective_pg_rate)
        self.cod_rate = float(cfg["cod_handling_rate"])
        self.cod_fixed = float(cfg["cod_handling_fixed"])
        self.cod_attempt_fee = float(cfg["cod_failed_attempt_fee"])
        self.support_delivered = float(cfg["support_delivered"])
        self.support_ndr = float(cfg["support_ndr_target_mean"]["target"])
        self.ops = float(cfg["ops_allocation_delivered"])
        self.shipping_fee = float(cfg["shipping_fee_charged"])

        # --- outcome-conditional lines, at their registry expectations --------
        self.reverse = float(cfg["reverse_freight_multiplier"]) * self.forward
        self.reverse_handling = float(cfg["reverse_handling_base"]) * self.weight_factor
        self.shrink = self.shrink_rate * self.cogs_value
        self.working_capital = (
            self.cogs_value * float(cfg["wc_annual_rate"])
            * _expected_wc_days(cfg) / 365.0
        )

        # Costs paid on every shipped order regardless of how it ends.
        self.dispatch = self.forward + self.packaging
        # Costs paid ONLY when the parcel comes back, excluding the COD-only
        # failed-attempt fee, which is added by the COD branch.
        self.return_leg = (self.reverse + self.reverse_handling + self.shrink
                           + self.working_capital + self.support_ndr)

    # -- delivered ---------------------------------------------------------

    def cm_delivered(self, is_cod, fee=0.0, incentive=0.0, partial=0.0) -> np.ndarray:
        """Margin if this order is delivered under ``is_cod``.

        ``partial`` splits the collection: ``partial`` rupees ride the prepaid
        rail at order time and the balance is collected in cash on delivery, so
        the order pays a PG fee on the token and COD handling on the remainder.
        """
        is_cod = np.asarray(is_cod, dtype=bool)
        collected = self.order_value + self.shipping_fee + np.where(is_cod, fee, 0.0)
        pay_cost = np.where(
            is_cod,
            self.pg_rate * partial
            + self.cod_rate * np.maximum(self.order_value - partial, 0.0) + self.cod_fixed,
            self.pg_rate * self.order_value,
        )
        return (collected - self.cogs_value - self.dispatch - pay_cost
                - self.support_delivered - self.ops - incentive)

    # -- returned ----------------------------------------------------------

    def cm_rto(self, is_cod, incentive=0.0, partial=0.0,
               partial_retained=True) -> np.ndarray:
        """Margin if this order comes back. Nothing is collected, everything is paid.

        A prepaid order still pays its PG fee on an RTO — the generator applies
        it unconditionally and so does this. A partial payment retains its token
        if the lever says the token is non-refundable on refusal, which is the
        only thing that makes it a commitment device.
        """
        is_cod = np.asarray(is_cod, dtype=bool)
        retained = partial if partial_retained else 0.0
        pay_cost = np.where(
            is_cod,
            self.cod_attempt_fee + self.pg_rate * partial,
            self.pg_rate * self.order_value,
        )
        return (retained - self.dispatch - self.return_leg - pay_cost - incentive)

    # -- expected value ----------------------------------------------------

    def expected(self, p_rto, is_cod, fee=0.0, incentive=0.0, partial=0.0,
                 partial_retained=True) -> np.ndarray:
        """``(1 - p) x delivered + p x returned``. The quantity every lever moves."""
        p = np.asarray(p_rto, dtype=float)
        return ((1.0 - p) * self.cm_delivered(is_cod, fee, incentive, partial)
                + p * self.cm_rto(is_cod, incentive, partial, partial_retained))

    # -- the affordability line -------------------------------------------

    def max_affordable_switch_spend(self, p_cod, p_prepaid) -> np.ndarray:
        """§6.6's "how much can we pay to convert this order to prepaid".

        ``EV_prepaid - EV_cod`` at each order's own economics and own two
        probabilities. Blueprint §6.6 states one number, ₹87.6, for a ₹1,000
        order at population RTO rates; this is that quantity per order, which is
        what a tier boundary needs.
        """
        cod = np.ones(self.n, dtype=bool)
        pre = np.zeros(self.n, dtype=bool)
        return self.expected(p_prepaid, pre) - self.expected(p_cod, cod)


def reconcile(econ: OrderEconomics, frame: pd.DataFrame) -> pd.DataFrame:
    """Rebuilt margin against the realised ledger, on the orders that exist.

    This is the check that the three departures above did not quietly change the
    baseline. It is reported rather than asserted: the expectations replace real
    draws, so the rebuilt figure is *supposed* to differ on individual orders and
    the question is only whether it differs in aggregate.
    """
    is_cod = (frame["payment_method"] == "COD").to_numpy()
    rto = frame["rto_flag"].to_numpy(bool)
    rebuilt = np.where(rto, econ.cm_rto(is_cod), econ.cm_delivered(is_cod))
    realised = frame["contribution_margin"].to_numpy(float)

    rows = []
    for label, mask in (("all orders", np.ones(len(frame), bool)),
                        ("COD delivered", is_cod & ~rto),
                        ("COD returned", is_cod & rto),
                        ("prepaid delivered", ~is_cod & ~rto),
                        ("prepaid returned", ~is_cod & rto)):
        rows.append({
            "population": label,
            "orders": int(mask.sum()),
            "realised_cm": round(float(realised[mask].mean()), 2),
            "rebuilt_cm": round(float(rebuilt[mask].mean()), 2),
            "delta": round(float((rebuilt - realised)[mask].mean()), 2),
        })
    return pd.DataFrame(rows)


def effective_pg_rate(frame: pd.DataFrame, params) -> float:
    """The realised blended PG rate on prepaid orders, measured not assumed.

    ``pg_fee_rate_by_rail`` ranges from 0.9% on UPI to 2.1% on cards, and the
    realised mix is not the registry's headline 1.8%. A counterfactual switch has
    no rail yet, so it has to be priced at the blended rate the platform actually
    pays — and the same rate is then used for orders that are already prepaid, so
    no artificial gap opens between the baseline arm and the switched arm.
    """
    prepaid = frame[frame["payment_method"] != "COD"]
    if prepaid.empty:
        return float(params.require("economics.pg_fee_rate"))
    fee = prepaid["payment_processing_fee"].to_numpy(float)
    value = prepaid["order_value"].to_numpy(float)
    return float(fee.sum() / value.sum())
