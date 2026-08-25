"""Shared logit assembly with component tracing.

Business concept
----------------
Both behavioural models in this project — payment-method choice and delivery failure —
are additive log-odds models. A customer's probability of choosing COD, or of an order
failing at delivery, is built by adding up ~25 named contributions and passing the total
through a logistic curve. No outcome is ever assigned; every flag is a draw from the
probability this module computes.

Two jobs, both essential
------------------------
1. **Assemble the linear predictor** from named terms whose coefficients come from
   ``params.yaml``. There are no coefficient values in this file.
2. **Record every coefficient actually consumed**, in a :class:`CoefficientLedger`.
   This is what makes validation test CAL-09 meaningful. If CAL-09 compared the run
   manifest against ``params.yaml``, it would be comparing the file to a copy of
   itself and would pass no matter what the generator did. Instead it compares what
   the *assembler actually used* against the file — so a coefficient overridden
   anywhere in the pipeline is caught.

Spec references
---------------
- Spec §7.2   — COD logit, 23 terms
- Spec §8.1-2 — RTO logit, two stages
- Spec §3.13  — ``logit_cod_components`` / ``logit_rto_components`` stored as JSONB
- Brief §3.5  — "CAL-09 asserts that no slope in the run manifest differs from params.yaml.
                 Implement CAL-09 early, not last."
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Structural key name for the intercept term, so it can be told apart from slopes.
# CAL-09 checks slopes for immutability; intercepts are the only values allowed to
# move -- three of them: cod_model, rto_model, conversion_model (decision A2).
INTERCEPT_TERM = "__intercept__"


class LogitError(RuntimeError):
    """Raised when a logit is assembled inconsistently."""


@dataclass
class CoefficientLedger:
    """Records every coefficient the generator actually consumed.

    One ledger per run, shared across all models. Validation test CAL-09 reads this
    and asserts each recorded value is bit-identical to ``params.yaml``.

    Recording the same (block, term) twice with different values is an error — it
    means two code paths disagreed about a coefficient, which is precisely the kind
    of silent drift CAL-09 exists to catch.
    """

    _entries: dict[str, dict[str, float]] = field(default_factory=dict)

    def record(self, block: str, term: str, value: float) -> float:
        """Record one coefficient as used, and return it unchanged."""
        value = float(value)
        block_entries = self._entries.setdefault(block, {})
        if term in block_entries and block_entries[term] != value:
            raise LogitError(
                f"Coefficient {block}.{term} was used with two different values: "
                f"{block_entries[term]} then {value}. A coefficient must have exactly "
                "one value for the whole run (CAL-09)."
            )
        block_entries[term] = value
        return value

    def as_dict(self) -> dict[str, dict[str, float]]:
        """The full ledger, for the run manifest and for CAL-09."""
        return {block: dict(terms) for block, terms in self._entries.items()}

    def slopes(self, block: str) -> dict[str, float]:
        """Every coefficient in a block except the intercept.

        CAL-09 asserts these never differ from params.yaml. The intercept is excluded
        because it is one of the values the calibrator is permitted to solve.
        """
        return {k: v for k, v in self._entries.get(block, {}).items() if k != INTERCEPT_TERM}


@dataclass
class LogitAssembler:
    """Builds one additive log-odds model, term by term, tracing each contribution.

    Example
    -------
    ::

        rto = LogitAssembler("rto_model", n_rows=len(orders), ledger=ledger)
        rto.add_intercept(params.require("rto_model.intercept_solved"))
        rto.add_numeric("is_cod", coef=coefs["is_cod"], values=is_cod)
        rto.add_categorical("geo_tier", coef_map=coefs["geo_tier"], levels=tier)
        rto.add_interaction("month_end_x_cod", coef=coefs["month_end_x_cod"],
                            left=is_month_end, right=is_cod)

        linear_predictor = rto.linear_predictor()
        probability      = logistic(linear_predictor)
        trace            = rto.components()     # -> JSONB in truth_order_probability
    """

    block: str
    n_rows: int
    ledger: CoefficientLedger
    _terms: dict[str, np.ndarray] = field(default_factory=dict)

    # -- term constructors --------------------------------------------------

    def add_intercept(self, value: float) -> "LogitAssembler":
        """Add the model intercept — one of the values the calibrator may solve."""
        if value is None:
            raise LogitError(
                f"{self.block}.intercept_solved is null. The calibrator must run before "
                "the model can be evaluated."
            )
        coef = self.ledger.record(self.block, INTERCEPT_TERM, value)
        return self._store(INTERCEPT_TERM, np.full(self.n_rows, coef, dtype=np.float64))

    def add_numeric(self, term: str, coef: float, values: np.ndarray) -> "LogitAssembler":
        """Add ``coef * values`` — a continuous or binary driver."""
        coef = self.ledger.record(self.block, term, coef)
        values = self._as_column(term, values)
        return self._store(term, coef * values)

    def add_categorical(
        self,
        term: str,
        coef_map: dict[str, float],
        levels: np.ndarray,
    ) -> "LogitAssembler":
        """Add a categorical driver, e.g. ``geo_tier`` or ``category``.

        Every level present in the data must appear in ``coef_map``. An unmapped level
        would silently contribute zero, which would look like a deliberate reference
        category rather than a missing parameter.
        """
        levels = np.asarray(levels)
        if levels.shape[0] != self.n_rows:
            raise LogitError(
                f"{self.block}.{term}: expected {self.n_rows} rows, got {levels.shape[0]}."
            )

        observed = set(np.unique(levels).tolist())
        missing = observed - set(coef_map)
        if missing:
            raise LogitError(
                f"{self.block}.{term}: no coefficient in params.yaml for level(s) "
                f"{sorted(missing)}. Every observed level needs an explicit coefficient."
            )

        contribution = np.zeros(self.n_rows, dtype=np.float64)
        for level, coef in coef_map.items():
            coef = self.ledger.record(self.block, f"{term}[{level}]", coef)
            contribution[levels == level] = coef
        return self._store(term, contribution)

    def add_interaction(
        self,
        term: str,
        coef: float,
        left: np.ndarray,
        right: np.ndarray,
    ) -> "LogitAssembler":
        """Add ``coef * left * right`` — e.g. month-end x COD (spec §8.2)."""
        coef = self.ledger.record(self.block, term, coef)
        left = self._as_column(term, left)
        right = self._as_column(term, right)
        return self._store(term, coef * left * right)

    def add_noise(self, term: str, draws: np.ndarray) -> "LogitAssembler":
        """Add a pre-drawn noise term, e.g. the COD epsilon or the RTO nu.

        The draws are supplied by the caller from its own named substream. The noise
        *scale* is a parameter and is recorded; the draws themselves are sampling.
        """
        return self._store(term, self._as_column(term, draws))

    def add_precomputed(self, term: str, contribution: np.ndarray) -> "LogitAssembler":
        """Add an already-multiplied contribution, e.g. the post-dispatch shock total."""
        return self._store(term, self._as_column(term, contribution))

    # -- outputs ------------------------------------------------------------

    def linear_predictor(self) -> np.ndarray:
        """Sum of every term — the log-odds."""
        if INTERCEPT_TERM not in self._terms:
            raise LogitError(
                f"{self.block}: no intercept added. Assemble the intercept explicitly so "
                "it appears in the component trace and in the ledger."
            )
        total = np.zeros(self.n_rows, dtype=np.float64)
        for contribution in self._terms.values():
            total += contribution
        return total

    def probability(self) -> np.ndarray:
        """The logistic of the linear predictor."""
        return logistic(self.linear_predictor())

    def components(self) -> dict[str, np.ndarray]:
        """Every additive term by name — written to JSONB in the ``truth`` schema.

        This is what lets Phase 5 decompose an individual order's probability into
        its named drivers, and what CAL-09 cross-checks the ledger against.
        """
        return dict(self._terms)

    def component_rows(self, index: np.ndarray | None = None) -> list[dict[str, float]]:
        """Per-row component traces, ready to serialise as JSONB."""
        names = list(self._terms)
        stacked = np.column_stack([self._terms[n] for n in names]) if names else np.empty((self.n_rows, 0))
        rows = range(self.n_rows) if index is None else index
        return [
            {name: float(stacked[i, j]) for j, name in enumerate(names)}
            for i in rows
        ]

    # -- internals ----------------------------------------------------------

    def _store(self, term: str, contribution: np.ndarray) -> "LogitAssembler":
        if term in self._terms:
            raise LogitError(
                f"{self.block}.{term} added twice. Each term contributes exactly once; "
                "adding it again would double-count the driver."
            )
        if not np.all(np.isfinite(contribution)):
            raise LogitError(
                f"{self.block}.{term} produced non-finite values. Check for NULLs in the "
                "driver — every feature needs an explicit imputation rule."
            )
        self._terms[term] = contribution
        return self

    def _as_column(self, term: str, values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=np.float64)
        if values.shape != (self.n_rows,):
            raise LogitError(
                f"{self.block}.{term}: expected shape ({self.n_rows},), got {values.shape}."
            )
        return values


def sum_terms(terms: dict[str, np.ndarray]) -> np.ndarray:
    """Sum named contributions in insertion order, without requiring an intercept.

    Used by the model blocks whose intercept is the value the calibrator solves,
    so the caller adds it separately.

    Accumulating into a zero array reproduces the left-to-right evaluation order
    of the equivalent inline expression exactly. That is what makes it safe to
    split a summed expression into named terms: the component trace and the
    generator share one implementation, and the split moves no bits (decision
    A45). ``0.0 + a`` is exact in IEEE-754, so the leading zero costs nothing.
    """
    if not terms:
        raise LogitError("sum_terms received no terms.")
    total = np.zeros(np.shape(next(iter(terms.values()))), dtype=np.float64)
    for contribution in terms.values():
        total += contribution
    return total


def logistic(x: np.ndarray | float) -> np.ndarray:
    """Numerically stable logistic function.

    The naive ``1 / (1 + exp(-x))`` overflows for large negative x. Since RTO
    log-odds sit around -3.2 and the post-dispatch shock has a standard deviation
    of 0.85, the tails reach far enough that stability matters.
    """
    x = np.asarray(x, dtype=np.float64)
    out = np.empty_like(x)
    positive = x >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-x[positive]))
    exp_x = np.exp(x[~positive])
    out[~positive] = exp_x / (1.0 + exp_x)
    return out


def logit(p: np.ndarray | float) -> np.ndarray:
    """Inverse of :func:`logistic` — converts a probability to log-odds."""
    p = np.asarray(p, dtype=np.float64)
    if np.any(p <= 0.0) or np.any(p >= 1.0):
        raise LogitError("logit is undefined at p <= 0 or p >= 1.")
    return np.log(p / (1.0 - p))


def marginal_effect_at_baseline(intercept: float, coefficient: float) -> float:
    """Effect of switching one binary driver on, holding everything else at reference.

    ``logistic(intercept + coefficient) - logistic(intercept)``, in percentage points.

    This is the definition spec §8.3 uses for the planted COD effect. It is the effect
    at *one specific reference point*, not the average across the order population —
    the two differ because the logistic curve is steeper in the middle than at the ends.
    Both are reported in ``_truth.json``.
    """
    return float(logistic(np.array([intercept + coefficient]))[0]
                 - logistic(np.array([intercept]))[0]) * 100.0


def average_marginal_effect(
    linear_predictor_off: np.ndarray, coefficient: float
) -> float:
    """Average effect of switching one binary driver on, across the actual population.

    Computes the counterfactual for every row with the driver off and on, and averages
    the difference. In percentage points.
    """
    off = logistic(linear_predictor_off)
    on = logistic(linear_predictor_off + coefficient)
    return float(np.mean(on - off)) * 100.0
