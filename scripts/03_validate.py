"""Run the validation suite against the generated dataset.

Reads the parquet artefacts and `_truth.json`, runs all seven families, writes
`reports/data_validation_report.md`, and prints the verdict.

    python scripts/03_validate.py
    python scripts/03_validate.py --dev
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.config.loader import load_params  # noqa: E402
from src.validation.result import Severity, Status  # noqa: E402
from src.validation.suite import run_suite  # noqa: E402

DATA = REPO_ROOT / "data" / "raw"
TRUTH = REPO_ROOT / "data" / "truth" / "_truth.json"
REPORT = REPO_ROOT / "reports" / "data_validation_report.md"

FAMILIES = ("VOL", "CAL", "EC", "BR", "LK", "DQ", "GT")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args(argv)

    scenario = REPO_ROOT / "config" / "scenarios" / "dev_small.yaml" if args.dev else None
    params = load_params(
        REPO_ROOT / "config" / "params.yaml",
        REPO_ROOT / "config" / "params.schema.json",
        scenario_path=scenario,
    )

    if not TRUTH.exists():
        print(f"No {TRUTH} — run scripts/01_generate.py first.")
        return 2
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))

    tables = {p.stem: pd.read_parquet(p) for p in sorted(DATA.glob("*.parquet"))}
    if not tables:
        print(f"No parquet files in {DATA} — run scripts/01_generate.py first.")
        return 2

    # CAL-09 needs the ledger the GENERATOR consumed, not one rebuilt from
    # params.yaml — comparing the file to a copy of itself would pass regardless.
    from src.models.logit import CoefficientLedger
    ledger = CoefficientLedger()
    for block, terms in truth.get("coefficient_ledger", {}).items():
        for term, value in terms.items():
            ledger.record(block, term, value)

    results = run_suite(params, tables, truth, ledger, {})
    verdict, reason = results.verdict()

    _write_report(params, tables, truth, results, verdict, reason)
    _print(results, verdict, reason)
    return 0 if not results.hard_failures else 1


def _print(results, verdict, reason) -> None:
    rule = "=" * 78
    print(f"\n{rule}\nVALIDATION REPORT\n{rule}")
    for family in FAMILIES:
        rows = [r for r in results.results if r.test_id.startswith(family)]
        if not rows:
            continue
        counts = {s: sum(1 for r in rows if r.status is s) for s in Status}
        print(f"\n{family}  ({len(rows)} tests: "
              f"{counts[Status.PASS]} pass, {counts[Status.FAIL]} fail, "
              f"{counts[Status.SKIP]} skip)")
        for r in rows:
            mark = {Status.PASS: "PASS", Status.FAIL: "FAIL", Status.SKIP: "SKIP"}[r.status]
            sev = "HARD" if r.severity is Severity.HARD else "SOFT"
            print(f"   [{mark}] {r.test_id:<8} {sev}  {r.name[:44]:<44} {r.actual}")
            if r.status is not Status.PASS and r.detail:
                print(f"          -> {r.detail[:150]}")

    total = len(results.results)
    print(f"\n{rule}")
    print(f"{total} tests | {sum(1 for r in results.results if r.status is Status.PASS)} pass "
          f"| {len(results.hard_failures)} HARD fail "
          f"| {len(results.soft_failures)} SOFT fail "
          f"| {sum(1 for r in results.results if r.status is Status.SKIP)} skip")
    print(f"\nVERDICT: {verdict}\n{reason}")
    print(rule)


def _write_report(params, tables, truth, results, verdict, reason) -> None:
    lines = [
        "# Data Validation Report", "",
        f"**Verdict: {verdict}**", "", reason, "",
        "## 1 — Dataset summary", "",
        f"- master seed: `{params.require('seed.master')}`",
        f"- params sha256: `{truth['run_manifest']['params_sha256'][:32]}…`",
        f"- dgp sha256: `{truth['run_manifest']['dgp_sha256'][:32]}…`",
        f"- generated: {truth['run_manifest']['generated_at_utc']}", "",
        "| Table | Rows |", "|---|---:|",
    ]
    for name, frame in sorted(tables.items()):
        lines.append(f"| `{name}` | {len(frame):,} |")

    lines += ["", "## 2 — Calibrated levels", "", "| Level | Solved |", "|---|---:|"]
    for k, v in truth["calibrated_levels"].items():
        lines.append(f"| `{k}` | {v:.6f} |")
    lines += ["", "Frozen (decision A38):", ""]
    for k, v in truth["frozen"].items():
        lines.append(f"- `{k}` = {v}")

    cod = truth["planted_causal_effects"]["cod_on_rto"]
    lines += [
        "", "## 3 — The planted causal effect (DERIVED, decision A6)", "",
        "| Quantity | Value |", "|---|---:|",
        f"| naive COD−prepaid gap | {cod['naive_observed_gap_pp']:.2f}pp |",
        f"| average marginal effect | {cod['average_marginal_effect_pp']:.2f}pp |",
        f"| selection share of the gap | {cod['selection_share_of_naive_gap']:.3f} |",
        f"| naive ÷ truth | {cod['naive_over_truth_multiple']:.2f}× |",
        "",
        "> The spec's prose figures (13.4pp / 19.9pp / 33%) belong to "
        "`noise_sd = 0.85` and no longer describe this dataset. See limitation L8. "
        "Everything downstream must quote `_truth.json`, never the prose.",
        "", "## 4 — Test results", "",
        "| ID | Severity | Status | Test | Expected | Actual |",
        "|---|---|---|---|---|---|",
    ]
    for r in results.results:
        sev = "HARD" if r.severity is Severity.HARD else "SOFT"
        lines.append(
            f"| {r.test_id} | {sev} | {r.status.value} | {r.name} | "
            f"{r.expected} | {r.actual} |"
        )

    notes = [r for r in results.results if r.status is not Status.PASS and r.detail]
    if notes:
        lines += ["", "## 5 — Failures and skips, explained", ""]
        for r in notes:
            lines += [f"**{r.test_id} — {r.name}** ({r.status.value})", "", r.detail, ""]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    sys.exit(main())
