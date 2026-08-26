"""FA family — fairness. All HARD.

The eighth family, added by decision A47. It exists because the §8.4 geography
audit failed on M1 at every restriction volume tested, was escalated, and was
ruled on — restrictive interventions rank *within* geo tier rather than globally.

**A ruling that is not tested is a ruling that regresses.** The specific way this
one would regress is not exotic: someone adds a feature, refits, and reads the
per-tier thresholding as a reporting convention rather than a policy constraint,
because the score is accurate and the accurate thing is easy to argue for. §8.4
wrote its constraint down before the AUC existed for exactly that reason. FA-01
is the same precaution, one layer down: it makes the *ruling* as hard to talk
your way out of as the original rule.

Why worst-tier over best-tier, not Tier-3 over Metro
----------------------------------------------------
§8.4 names Tier-3 and Metro because they are the extremes it expected. Encoding
those two names in the test would let a policy that happened to concentrate on
TIER2 sail through a check that was watching the wrong pair. The measurement is
therefore max-rate over min-rate across all four tiers, which is the same number
whenever §8.4's expectation holds and a stricter one whenever it does not.

Why it reads a published artefact rather than fitting a model
-------------------------------------------------------------
Same contract as ``reports/database_checks.json``, and for the same reason. This
suite validates the *dataset*; FA-01 is a statement about a fitted model applied
to that dataset, and fitting a model inside the validation run would make
``make validate`` depend on a live PostgreSQL and a two-minute GBM.

``scripts/07_fit_m2.py`` publishes ``reports/fairness_checks.json`` carrying the
hash of the ``fct_order`` it was computed against. If that hash does not match
the dataset being validated right now, the result is STALE and FA-01 reports
**SKIP, never PASS** — a fairness result computed on a previous dataset is not
evidence about this one, and silently accepting it would fabricate exactly the
kind of green light this project exists not to give.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.validation.dataset_hash import order_hash
from src.validation.result import Severity, Status, TestResult

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKS = REPO_ROOT / "reports" / "fairness_checks.json"

# Blueprint §8.4. The limit is the rule; the volumes are the ones the ruled
# policy was designed and reported against, so a change to either has to be a
# decision rather than a drift.
PROXY_RATIO_LIMIT = 2.5
REQUIRED_VOLUMES = (0.05, 0.10, 0.17, 0.25)

_SKIP = ("Needs a fitted M2 and the ruled per-tier policy. Run "
         "`python scripts/07_fit_m2.py`, which publishes "
         "reports/fairness_checks.json.")


def _skip(reason: str) -> TestResult:
    return TestResult(
        test_id="FA-01", name="Restrictive-intervention rate ratio", severity=Severity.HARD,
        status=Status.SKIP, expected=f"<= {PROXY_RATIO_LIMIT}x at every volume",
        actual="not runnable", detail=reason,
    )


def run_fa(results, params, tables, truth, ledger, extra) -> None:
    """FA-01 — restrictive-intervention rate ratio, worst tier vs best tier."""
    results.add(_fa_01(tables))


def _fa_01(tables) -> TestResult:
    if not CHECKS.exists():
        return _skip(_SKIP)

    published = json.loads(CHECKS.read_text(encoding="utf-8"))
    if published.get("fct_order_sha256") != order_hash(tables["fct_order"]):
        return _skip(
            "reports/fairness_checks.json was computed against a DIFFERENT "
            "dataset. Re-run scripts/07_fit_m2.py against this one.")

    check = published.get("checks", {}).get("FA-01")
    if check is None:
        return _skip(_SKIP)

    by_volume = {round(float(row["volume_flagged"]), 4): row
                 for row in check.get("by_volume", [])}
    missing = [v for v in REQUIRED_VOLUMES if round(v, 4) not in by_volume]
    if missing:
        # Not a failure and not a pass. A published result that covered three of
        # the four required volumes would satisfy `passed` while leaving the
        # fourth untested, which is precisely the shape of a check that looks
        # green because it did less work.
        return _skip(f"published result does not cover volume(s) {missing}; "
                     f"FA-01 requires all of {list(REQUIRED_VOLUMES)}.")

    # Re-derive the verdict from the published ratios rather than trusting the
    # `passed` flag. The flag was written by the same script that chose the
    # policy; the ratios are the evidence.
    worst = None
    breaches = []
    for volume in REQUIRED_VOLUMES:
        ratio = by_volume[round(volume, 4)]["ratio"]
        if not isinstance(ratio, (int, float)):
            breaches.append(f"vol {volume:.2f}: {ratio}")
            continue
        worst = ratio if worst is None else max(worst, ratio)
        if ratio > PROXY_RATIO_LIMIT:
            breaches.append(f"vol {volume:.2f}: {ratio:.2f}x")

    actual = ("worst {:.2f}x".format(worst) if worst is not None and not breaches
              else "; ".join(breaches))
    return TestResult(
        test_id="FA-01", name="Restrictive-intervention rate ratio",
        severity=Severity.HARD,
        status=Status.PASS if not breaches else Status.FAIL,
        expected=f"<= {PROXY_RATIO_LIMIT}x at every volume in {list(REQUIRED_VOLUMES)}",
        actual=actual,
        detail=("Decision A47. Restrictive interventions (COD fee, partial "
                "payment, COD gating) rank within geo tier; offers use the "
                "global score. Measured post-overlay, which is what a customer "
                "experiences. Model: {}. {}".format(
                    published.get("model", "unknown"), check.get("detail", ""))),
    )
