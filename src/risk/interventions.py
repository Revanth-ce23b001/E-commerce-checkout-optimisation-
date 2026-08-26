"""Condition 1 of the A47 ruling — which levers are rationed by geography.

Split out of ``policy.py`` because it is a *classification*, not a measurement:
every other thing in that module computes a number from a score, and this one
decides which interventions the number is allowed to gate. Keeping them apart
means a future PRD can move an intervention between the two lists without going
anywhere near the arithmetic.

The ruling's phrasing is the whole test: **carrots are not rationed by geography;
sticks are.** A restrictive lever removes or prices an option the customer
already had. Everything else is offered, and an offer withheld from a Metro
customer is not the harm §8.4 was written to prevent.

**E was reclassified (decision A48).** It was first listed as an offer on the
grounds that reordering removes nothing. That was wrong. E does not merely
emphasise some options, it **de-emphasises others** — and for a payment method
chosen by 62% of orders largely out of habit, salience *is* the option. A lever
that can move COD share by 3-6pp through position alone is exercising the same
power as a fee, without the disclosure a fee would require. It ranks per-tier,
and it carries ``ONE_TAP_CONSTRAINT`` as a hard product floor.
"""

from __future__ import annotations

import pandas as pd


# Blueprint §10.1's six interventions, plus COD gating. Classified on the single
# question in the module docstring: does this lever take something away?
INTERVENTIONS = (
    # (id, name, restrictive, threshold basis, note)
    ("A", "Prepaid incentive", False, "global",
     "a discount. Rationing it by geography would withhold money from Metro "
     "customers who qualify on risk, which is not what §8.4 protects against."),
    ("B", "COD fee", True, "per-tier",
     "prices an option the customer already had. The archetypal stick."),
    ("C", "Trust-building checkout", False, "global",
     "messaging. Costs the customer nothing and removes nothing."),
    ("D", "Partial payment", True, "per-tier",
     "conditions COD on an upfront payment. A restriction with a softer edge, "
     "not a different kind of thing."),
    ("E", "Smart payment recommendation", True, "per-tier",
     "reorders and DE-EMPHASISES. Ruled restrictive: de-emphasis is a "
     "withdrawal of salience, and salience is what a default is made of. "
     "Carries the one-tap constraint below."),
    ("F", "Payment-reliability routing", False, "global",
     "fixes a broken rail. §10.3 calls it the only intervention with no downside "
     "on any axis."),
    ("G", "COD gating (COD withdrawn)", True, "per-tier",
     "the hardest form of the restriction. Not a lettered §10.1 lever; named "
     "here because the ruling names it."),
)


# A hard product constraint attached to E, carried into the Phase 5 PRD as
# non-negotiable. It is here rather than in the PRD alone because the code that
# assigns E is here: a constraint that lives only in a document is a constraint
# that gets lost at the next handover.
ONE_TAP_CONSTRAINT = (
    "COD must remain reachable in ONE TAP in every variant of E. Reordering and "
    "de-emphasis are permitted; an extra tap, a hidden menu, a collapsed "
    "accordion or a confirmation interstitial is not. §10.1 already draws this "
    "line — 'emphasis is acceptable; hiding or burying COD is not' — and E is "
    "the only lever that can cross it without any copy changing."
)


def intervention_table() -> pd.DataFrame:
    return pd.DataFrame(
        [{"id": i, "intervention": n, "kind": "RESTRICTIVE" if r else "offer",
          "threshold basis": b, "why": w} for i, n, r, b, w in INTERVENTIONS])


def restrictive_ids() -> tuple:
    """The levers that rank per-tier. Read by the report and by the tests."""
    return tuple(i for i, _n, r, _b, _w in INTERVENTIONS if r)
