"""Validation result types and the report row format.

Every test in the seven families (VOL, CAL, EC, BR, LK, DQ, GT) returns a
:class:`TestResult`. HARD failures block the dataset; SOFT failures are logged and
require written sign-off in ``docs/validation.md``.

Spec references
---------------
- Spec §17    — the test families and their severities
- Spec §18    — the report layout and the PASS / CONDITIONAL / FAIL rule
- Brief §12.3 — the exact rendering of a test row
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    """HARD failures block the dataset. SOFT failures require written sign-off."""

    HARD = "HARD"
    SOFT = "SOFT"


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class TestResult:
    """One validation test outcome, renderable as a report row.

    ``expected``, ``actual`` and ``delta`` are free-form strings rather than numbers
    because the families measure different things — percentages, rupees, counts,
    AUC, boolean grants — and the report needs each in its natural units.
    """

    test_id: str
    name: str
    severity: Severity
    status: Status
    expected: str
    actual: str
    delta: str = ""
    detail: str = ""
    # Sampling context, so a dev-scale near-miss is not mistaken for a calibration bug.
    sampling_ci: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.HARD and self.status is Status.FAIL

    def render(self) -> str:
        """Render in the exact format required by brief §12.3."""
        lines = [
            f"Test:      {self.name}",
            f"Expected:  {self.expected}",
            f"Actual:    {self.actual}",
        ]
        if self.delta:
            lines.append(f"Delta:     {self.delta}")
        if self.sampling_ci:
            lines.append(f"Sampling:  {self.sampling_ci}")
        lines.append(f"Status:    {self.status.value}")
        if self.detail:
            lines.append(f"Detail:    {self.detail}")
        return "\n".join(lines)

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return f"[{self.test_id}] {self.status.value} ({self.severity.value}) {self.name}"


@dataclass
class ResultSet:
    """A collection of results with the spec §18 verdict rule applied."""

    results: list[TestResult] = field(default_factory=list)

    def add(self, result: TestResult) -> TestResult:
        self.results.append(result)
        return result

    @property
    def hard_failures(self) -> list[TestResult]:
        return [r for r in self.results if r.blocking]

    @property
    def soft_failures(self) -> list[TestResult]:
        return [
            r
            for r in self.results
            if r.severity is Severity.SOFT and r.status is Status.FAIL
        ]

    @property
    def hard_skipped(self) -> list[TestResult]:
        """HARD tests that could not run.

        These are **not** passes. A verdict that ignored them would report a green
        light on a dataset where a third of the leakage and ground-truth families
        never executed — which is exactly the kind of quietly-wrong claim this
        project exists to avoid making.
        """
        return [
            r for r in self.results
            if r.severity is Severity.HARD and r.status is Status.SKIP
        ]

    def verdict(self) -> tuple[str, str]:
        """Apply the spec §18 PASS / CONDITIONAL PASS / FAIL rule.

        Extended beyond §18 in one way: a skipped HARD test caps the verdict at
        CONDITIONAL. The spec's rule assumed every test runs, and it does not here
        — several need a live PostgreSQL or a fitted model that belongs to Phase 5.

        Returns ``(verdict, reason)``.
        """
        hard = len(self.hard_failures)
        soft = len(self.soft_failures)
        skipped = self.hard_skipped

        if hard > 0:
            return (
                "🔴 NOT READY",
                f"{hard} HARD failure(s). Do not proceed. Fix the generator, or escalate "
                "the assumption that cannot be satisfied.",
            )
        if soft > 5:
            return "🔴 NOT READY", f"{soft} SOFT failures (>5)."

        if skipped:
            ids = ", ".join(r.test_id for r in skipped)
            return (
                "🟡 CONDITIONAL",
                f"All HARD tests that RAN pass, and {soft} SOFT failure(s) — but "
                f"{len(skipped)} HARD test(s) could not run and are NOT passes: {ids}. "
                + _skip_cause(skipped) + " Proceed only with each one written into "
                "docs/limitations.md with a stated reason.",
            )

        if soft >= 3:
            return (
                "🟡 CONDITIONAL",
                f"All HARD pass; {soft} SOFT failure(s). Each must be written into "
                "docs/limitations.md with a stated reason before proceeding.",
            )
        return "🟢 DATASET READY", f"All HARD pass; {soft} SOFT failure(s); nothing skipped."


# Which blocker to name depends on what is actually skipped. Saying "needs a live
# PostgreSQL" once the database checks pass would be a stale claim in the one
# sentence a reader is most likely to quote.
_DATABASE_TESTS = {"LK-01", "LK-05", "DQ-01"}


def _skip_cause(skipped) -> str:
    ids = {r.test_id for r in skipped}
    needs_db = ids & _DATABASE_TESTS
    if needs_db and ids - _DATABASE_TESTS:
        return "They need a live PostgreSQL or a fitted model (Phase 5)."
    if needs_db:
        return "They need a live PostgreSQL."
    return "Every one needs a fitted model and belongs to Phase 5."
