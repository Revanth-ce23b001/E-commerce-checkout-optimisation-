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

    def verdict(self) -> tuple[str, str]:
        """Apply the spec §18 PASS / CONDITIONAL PASS / FAIL rule.

        Returns ``(verdict, reason)``.
        """
        hard = len(self.hard_failures)
        soft = len(self.soft_failures)

        if hard > 0:
            return (
                "🔴 NOT READY",
                f"{hard} HARD failure(s). Do not proceed. Fix the generator, or escalate "
                "the assumption that cannot be satisfied.",
            )
        if soft > 5:
            return "🔴 NOT READY", f"{soft} SOFT failures (>5)."
        if soft >= 3:
            return (
                "🟡 CONDITIONAL",
                f"All HARD pass; {soft} SOFT failure(s). Each must be written into "
                "docs/validation.md with a stated reason before proceeding.",
            )
        return "🟢 DATASET READY", f"All HARD pass; {soft} SOFT failure(s)."
