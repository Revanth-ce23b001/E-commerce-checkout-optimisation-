"""Turn the view into a design matrix, with every exclusion stated out loud.

Design rules, in priority order:

* **M1 projects away Stage-3.** ``payment_method``, ``paid_via_switch`` and
  ``payment_attempt_count`` are removed for M1 and kept for M2. This is the only
  difference between the two feature sets.
* **A18 — unknown is unknown.** A null point-in-time feature means "this customer
  has no history", not "zero". Every nullable numeric gets an explicit
  ``*_is_missing`` indicator and is filled with the TRAIN-set median, so the
  indicator carries the offset and the fill value carries none.
* **Fit statistics come from train only.** Medians, means and standard deviations
  are learned on the training rows and applied to test. Learning them on the
  pooled data is a small leak that inflates test performance for free.
* **Standardised continuous features.** Coefficients are then per-standard-
  deviation and directly comparable, which is what makes a scorecard readable.

Four columns are dropped on judgement, not on rules. Each is recorded in
``EXCLUSIONS`` with its reason, and the report prints that table.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .dataset import KEYS, M2_ONLY, TARGET

# The five raw pit_* counts below are culled for ONE reason, stated once here and
# printed once above the exclusions table rather than repeated on every row.
#
# They are numerators over the same denominator, so they carried VIFs of 30-216
# against each other. The first fit split one effect into a near-cancelling
# -0.53 / +0.47 pair on features that mean almost the same thing. The retained
# history set is blueprint §9.2's own pre-registered feature audit -- shrunk RTO
# rate, COD share, prepaid-success count, delivered count, tenure, payment-failure
# rate. Culling costs 0.0000 AUC and buys a readable scorecard, which is the
# scorecard's entire justification.
COUNT_CULL_NOTE = (
    "The five `pit_*` raw counts share a single reason, so it is stated here "
    "rather than repeated on each row. They are numerators over the same "
    "denominator and carried VIFs of 30-216 against each other; the first fit "
    "split one effect into a near-cancelling -0.53 / +0.47 pair. The retained "
    "history set is blueprint §9.2's own pre-registered feature audit -- shrunk "
    "RTO rate, COD share, prepaid-success count, delivered count, tenure, "
    "payment-failure rate. Culling them cost 0.0000 AUC and bought a readable "
    "scorecard, which is the scorecard's entire justification."
)

_COUNT_CULL = "raw count -- collinear pit_* count, see the note above the table"

EXCLUSIONS = {
    "delivery_geography_id": (
        "500 levels, one per pincode cluster. One-hot encoding it lets the model "
        "memorise postcodes, which is precisely the proxying the §8.4 fairness "
        "overlay exists to prevent. Its content is already carried by geo_tier, "
        "serviceability_score, courier_reliability_score and cod_cultural_index."
    ),
    "list_price": (
        "correlates 0.90 with order_value. Two near-duplicate money variables "
        "give a scorecard unstable, often opposite-signed coefficients. "
        "order_value is what the customer owes and what the economics price."
    ),
    "cart_value": (
        "correlates 0.71 with order_value and 0.41 with cart_size, both of which "
        "are retained. Same instability argument as list_price."
    ),
    "pit_rto_rate_raw": (
        "null for 21.7% of rows and superseded by pit_rto_rate_shrunk, which is "
        "the low-n-safe version the spec designed for exactly this feature "
        "(blueprint §9.2, 'INCLUDE — with shrinkage for low-n')."
    ),
    "pit_risk_tier_rule_based": (
        "it IS the M1 rules baseline (decision A21). It is a deterministic "
        "function of pit_rto_rate_shrunk and pit_is_new_customer, both of which "
        "are model features. Including it would score the baseline twice."
    ),
    "is_shipped": "constant TRUE — the view's own row filter.",
    "pit_orders_placed": _COUNT_CULL,
    "pit_orders_resolved": _COUNT_CULL,
    "pit_rto_count": _COUNT_CULL,
    "pit_cod_orders": _COUNT_CULL,
    "pit_payment_failure_count": _COUNT_CULL,
}

LOG_FEATURES = ("order_value", "review_count", "seller_rating_count",
                "pit_tenure_days", "pit_orders_delivered",
                "pit_prepaid_success_count",
                "pit_days_since_last_order", "pit_avg_order_value")

CATEGORICALS = {
    "geo_tier": "METRO",
    "category": "GROCERY_FMCG",
    "device_type": "ANDROID",
    "age_bucket": "25-34",
    "acquisition_channel": "ORGANIC",
    "payment_method": "PREPAID",
}

# Two columns are redundant if one is a perfect affine function of the other.
# Detected generically rather than named, per the A44 methodology note: a
# targeted fix is only ever as complete as the person writing it. The first fit
# carried three such pairs and none of them was on anyone's list —
# pit_has_history against pit_cod_share_is_missing (exact complements),
# pit_days_since_last_order_is_missing against pit_avg_order_value_is_missing
# (identical), and a full device_type dummy set whose declared reference level
# ("ANDROID_APP") did not exist in the data, so nothing was dropped and the set
# was collinear with the intercept. VIFs ran to 2.4e7.
REDUNDANCY_TOLERANCE = 1e-8


class DesignMatrix:
    """Learns its transforms on train, applies them unchanged to test."""

    def __init__(self, model: str):
        if model not in ("M1", "M2"):
            raise ValueError("model must be 'M1' or 'M2'")
        self.model = model
        self.medians_: dict[str, float] = {}
        self.means_: pd.Series | None = None
        self.stds_: pd.Series | None = None
        self.columns_: list[str] = []
        self.redundant_: list[tuple[str, str]] = []

    def _source_columns(self, frame: pd.DataFrame) -> list[str]:
        drop = set(KEYS) | {TARGET} | set(EXCLUSIONS)
        if self.model == "M1":
            drop |= set(M2_ONLY)
        return [c for c in frame.columns if c not in drop]

    def _raw(self, frame: pd.DataFrame) -> pd.DataFrame:
        cols = self._source_columns(frame)
        out = frame[cols].copy()

        for col in out.columns:
            if out[col].dtype == bool:
                out[col] = out[col].astype(float)
            elif out[col].dtype.name in ("object", "str") and col not in CATEGORICALS:
                raise AssertionError(f"unencoded categorical reached the matrix: {col}")
            elif col not in CATEGORICALS:
                out[col] = pd.to_numeric(out[col], errors="raise").astype(float)
        return out

    def fit_transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        raw = self._raw(frame)
        numeric = [c for c in raw.columns if c not in CATEGORICALS]
        self.medians_ = {c: float(raw[c].median()) for c in numeric if raw[c].isna().any()}
        built = self._build(raw)
        built = self._drop_redundant(built)
        self.means_ = built.mean()
        self.stds_ = built.std().replace(0.0, 1.0)
        scaled = self._scale(built)
        self.columns_ = list(scaled.columns)
        return scaled

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        built = self._build(self._raw(frame))
        scaled = self._scale(built)
        return scaled.reindex(columns=self.columns_, fill_value=0.0)

    # -- internals ----------------------------------------------------------

    def _build(self, raw: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=raw.index)

        for col, fill in self.medians_.items():
            out[f"{col}_is_missing"] = raw[col].isna().astype(float)
            raw[col] = raw[col].fillna(fill)

        for col in raw.columns:
            if col in CATEGORICALS:
                continue
            if col in LOG_FEATURES:
                out[f"log1p_{col}"] = np.log1p(raw[col].clip(lower=0.0))
            else:
                out[col] = raw[col]

        for col, reference in CATEGORICALS.items():
            if col not in raw.columns:
                continue
            present = set(raw[col].dropna().unique())
            if reference not in present:
                raise AssertionError(
                    f"{col}: declared reference level {reference!r} is not in the "
                    f"data ({sorted(present)}). Every level would be dummied and "
                    "the set would be collinear with the intercept."
                )
            for level in sorted(present - {reference}):
                out[f"{col}[{level}]"] = (raw[col] == level).astype(float)
        return out

    def _drop_redundant(self, built: pd.DataFrame) -> pd.DataFrame:
        """Remove any column that is a perfect affine function of an earlier one.

        |r| = 1 covers both exact duplicates and exact complements, which is what
        redundant binary indicators look like. Constant columns go too: they carry
        no information and their standard deviation is zero.
        """
        constant = [c for c in built.columns if built[c].std() == 0.0]
        kept = built.drop(columns=constant)
        self.redundant_ = [(c, "constant") for c in constant]

        corr = kept.corr().abs()
        drop: list[str] = []
        # Scan named columns first so that when a named column and a generated
        # `*_is_missing` indicator are the same thing, the named one survives.
        columns = ([c for c in kept.columns if not c.endswith("_is_missing")]
                   + [c for c in kept.columns if c.endswith("_is_missing")])
        for i, col in enumerate(columns):
            if col in drop:
                continue
            for other in columns[i + 1:]:
                if other in drop:
                    continue
                if abs(corr.loc[col, other] - 1.0) < REDUNDANCY_TOLERANCE:
                    drop.append(other)
                    self.redundant_.append((other, f"perfectly collinear with {col}"))
        return kept.drop(columns=drop)

    def _scale(self, built: pd.DataFrame) -> pd.DataFrame:
        aligned = built.reindex(columns=self.means_.index, fill_value=0.0)
        return (aligned - self.means_) / self.stds_
