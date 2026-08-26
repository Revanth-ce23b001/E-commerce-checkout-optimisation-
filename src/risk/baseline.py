"""The rules floor any model has to beat.

Blueprint §9.3 specifies a three-rule baseline: "payment method + prior RTO +
tenure". **M1 can only have two of them.** Payment method is Stage-3, and M1 is
by definition the pre-selection model, so the canonical three-rule baseline is
not available to it. That is decision A21's split:

* ``pit_risk_tier_rule_based``  — prior RTO + tenure. The M1 floor.
* ``order_risk_tier_rule_based`` — the same, escalated one tier for COD. M2 only,
  and hard-blocked from the view.

Recorded rather than worked around: M1's floor is a weaker baseline than the
blueprint describes, and the model has correspondingly less to beat. Reporting
M1 against the three-rule floor would flatter it with a comparison it never had
to make.

The tiers convert to a score by their empirical TRAIN RTO rate, which is the
fairest possible reading of a rules baseline: three tiers can only produce three
distinct probabilities, and the best three are the ones the data supports.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TIER_ORDER = ("LOW", "MED", "HIGH")


class RulesBaseline:
    """Three tiers in, three probabilities out."""

    def __init__(self, column: str = "pit_risk_tier_rule_based"):
        self.column = column
        self.rates_: dict[str, float] = {}
        self.base_rate_: float = float("nan")

    def fit(self, frame: pd.DataFrame, target: str = "rto_flag") -> "RulesBaseline":
        self.base_rate_ = float(frame[target].mean())
        self.rates_ = {
            tier: float(group[target].mean())
            for tier, group in frame.groupby(self.column)
        }
        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return frame[self.column].map(self.rates_).fillna(self.base_rate_).to_numpy(float)

    def table(self, frame: pd.DataFrame, target: str = "rto_flag") -> pd.DataFrame:
        grouped = frame.groupby(self.column).agg(n=(target, "size"), observed=(target, "mean"))
        grouped["share_pct"] = grouped["n"] / grouped["n"].sum() * 100
        grouped["assigned_p"] = [self.rates_.get(t, self.base_rate_) for t in grouped.index]
        grouped["lift"] = grouped["observed"] / frame[target].mean()
        return grouped.reindex([t for t in TIER_ORDER if t in grouped.index])


class M2RulesBaseline(RulesBaseline):
    """Blueprint §9.3's canonical floor: payment method + prior RTO + tenure.

    All three rules, which is what makes this the baseline M2 actually has to
    beat — and M1 did not. The M1 report was explicit that scoring M1 against the
    three-rule floor would have flattered it with a comparison it never made.

    ``fct_order.order_risk_tier_rule_based`` is the planted version of this
    column and it is deliberately NOT in ``vw_risk_model_input``: it embeds
    payment method, so exposing it to M1 would breach the Stage-3 projection.
    Rather than widen the firewall, the tier is **reconstructed** from two columns
    that are already in the view — ``pit_risk_tier_rule_based`` (prior RTO +
    tenure) escalated one tier when the order is COD, which is exactly decision
    A21's definition and exactly what ``generators.orders.m2_risk_tier`` does.

    ``verify_against_planted`` checks the reconstruction against the planted
    column. It is the only place the planted column is read, it is read for
    agreement only, and it never touches a feature.
    """

    COLUMN = "order_risk_tier_rule_based"

    def __init__(self, cod_escalates_one_tier: bool = True):
        super().__init__(column=self.COLUMN)
        self.cod_escalates = cod_escalates_one_tier

    def add_column(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Return ``frame`` with the reconstructed three-rule tier attached."""
        out = frame.copy()
        rank = out["pit_risk_tier_rule_based"].map(
            {t: i for i, t in enumerate(TIER_ORDER)}).to_numpy()
        if self.cod_escalates:
            is_cod = (out["payment_method"] == "COD").to_numpy()
            rank = np.where(is_cod, np.minimum(rank + 1, len(TIER_ORDER) - 1), rank)
        out[self.COLUMN] = np.array(TIER_ORDER, dtype=object)[rank]
        return out

    def verify_against_planted(self, frame: pd.DataFrame,
                               planted: pd.DataFrame) -> dict:
        """Agreement between the reconstruction and ``fct_order``'s own column.

        Anything short of 100% means the reconstruction is not the rule the
        generator planted, and the baseline would be measuring something else.
        """
        merged = frame[["session_id", self.COLUMN]].merge(
            planted[["session_id", self.COLUMN]], on="session_id",
            suffixes=("_rebuilt", "_planted"))
        agree = (merged[f"{self.COLUMN}_rebuilt"]
                 == merged[f"{self.COLUMN}_planted"])
        return {"rows_compared": int(len(merged)),
                "agreement": float(agree.mean()) if len(merged) else float("nan"),
                "exact": bool(len(merged) and agree.all())}
