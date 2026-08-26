"""The lever library: eligibility, and the [A] response drawn from config.

Two jobs, both bookkeeping, both kept out of ``simulate.py`` so that the file
doing the arithmetic contains no policy and no assumption.

**Eligibility** is where decision A47 lives. A restrictive lever ranks orders
*within their own geo tier* and then submits to the §8.4 customer-level overlay;
an offer ranks globally and does not. That is not a modelling choice available to
this phase — it is a ruling, enforced in ``src/risk/policy.py``, tested by FA-01,
and re-used here rather than reimplemented.

**Targeting depth is a dimension, not a constant.** Blueprint §10.1 gives each
lever a target segment, and for intervention A it says *"Medium & high risk"* —
but §10.2's own worked example for the same lever targets high risk only, at 17%
of orders. The two disagree, and which one is right is an empirical question
about leakage rather than a matter of preference. So every lever is run at every
depth and the decision table shows all of them; the §10.1 depth is marked, not
assumed.

**Response** converts the config's stated priors into per-order rates. The one
non-trivial conversion is ``prepaid_share_pp``: §10.1 states C and E as
*population* pp moves in prepaid share ("+2-4pp", "+3-6pp"), not as within-COD
switch rates. A +3pp move in a band that is 98.7% COD needs 3.04% of its COD
orders to switch; the same +3pp in a band that is 21% COD needs 14.3%. Reading
the pp figure as a switch rate would understate C and E in the high-risk band by
a factor of five, and overstate them in the low-risk band by the same.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.risk import policy
from src.risk.fairness import apply_overlay
from src.risk.interventions import INTERVENTIONS, ONE_TAP_CONSTRAINT  # noqa: F401

LEVELS = ("lo", "central", "hi")
DEPTHS = ("high_only", "med_high", "all")

# The risk bands each depth is willing to touch. "all" has no entry because it
# uses no score at all — that is exactly what makes it the flat arm.
DEPTH_BANDS = {"high_only": ("HIGH",), "med_high": ("MED", "HIGH")}


@dataclass(frozen=True)
class Lever:
    id: str
    cfg: dict

    @property
    def name(self) -> str:
        return str(self.cfg["name"])

    @property
    def restrictive(self) -> bool:
        return bool(self.cfg["restrictive"])

    @property
    def ranking(self) -> str:
        return str(self.cfg["ranking"])

    @property
    def blueprint_depth(self) -> str:
        """The depth §10.1 specifies for this lever, mapped onto DEPTHS."""
        return {"high": "high_only", "med_high": "med_high",
                "all": "all"}[str(self.cfg["target"])]

    @property
    def applies_to(self) -> str:
        return str(self.cfg["applies_to"])

    def cash(self, key: str) -> float:
        return float(self.cfg.get(key, 0.0) or 0.0)

    def band(self, key: str, level: str) -> float:
        node = self.cfg.get(key)
        if node is None:
            return 0.0
        return float(node[level])


def load(config: dict) -> dict[str, Lever]:
    return {k: Lever(k, v) for k, v in config["levers"].items()}


def classification_check(levers: dict[str, Lever]) -> pd.DataFrame:
    """Assert this file agrees with ``src/risk/interventions.py``.

    Two copies of the A47 classification exist — one in the Phase 4 code that
    FA-01 tests, one in the Phase 5 config that drives this simulation. They are
    allowed to exist separately; they are not allowed to disagree, and a table
    that has to be read is a check that gets skipped, so the caller asserts on
    the mismatch column rather than on a human reading it.
    """
    ruled = {i: (r, b) for i, _n, r, b, _w in INTERVENTIONS}
    rows = []
    for key, lever in levers.items():
        want_restrictive, want_basis = ruled[key]
        got_basis = "per-tier" if lever.ranking == "per_tier" else "global"
        rows.append({
            "id": key,
            "lever": lever.name,
            "phase4_kind": "RESTRICTIVE" if want_restrictive else "offer",
            "phase5_kind": "RESTRICTIVE" if lever.restrictive else "offer",
            "phase4_basis": want_basis,
            "phase5_basis": got_basis,
            "agrees": bool(want_restrictive == lever.restrictive
                           and want_basis == got_basis),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def selection(lever: Lever, frame: pd.DataFrame, tiers: pd.Series,
              score: np.ndarray, boundaries: dict, depth: str) -> np.ndarray:
    """The score-based selection, before the lever's own cohort narrows it.

    Kept separate from :func:`eligible` because §10.1's pp figures are stated
    against the *segment the treatment is shown to*, counting both payment
    methods — see :func:`segment_cod_share` — and intersecting with the cohort
    first would change that denominator.
    """
    if depth == "all":
        return np.ones(len(frame), dtype=bool)
    band = DEPTH_BANDS[depth]
    if lever.restrictive:
        volume = float(pd.Series(np.asarray(tiers)).isin(band).mean())
        return restrictive_set(frame, score, volume)
    cut = boundaries["med_high"] if depth == "high_only" else boundaries["low_med"]
    return np.asarray(score) >= cut


def eligible(lever: Lever, frame: pd.DataFrame, tiers: pd.Series,
             score: np.ndarray, boundaries: dict, depth: str,
             rng: np.random.Generator | None = None,
             matched_to: np.ndarray | None = None) -> np.ndarray:
    """Which orders this lever actually touches, at one targeting depth.

    ``high_only``   the value-negative band alone — score at or above p-star for
                    an offer, the same *volume* ranked per geo tier for a
                    restriction.
    ``med_high``    everything above the derived LOW/MED line.
    ``all``         every order the lever can act on. No score, and **no §8.4
                    overlay**: a policy that exempts clean-record customers is
                    already using information, and crediting the flat arm with
                    that protection would make it look better than the thing
                    anyone would actually ship. The flat arm has to be genuinely
                    flat or the comparison is rigged in the targeted arm's
                    favour.
    ``random_matched``  a RANDOM cohort the same size as a given targeted mask.

    The last one exists because ``all`` and a targeted depth differ in *two* ways
    at once — volume and selection — and H10 is a claim about selection alone. A
    flat rollout treating three times the volume is not a worse targeting rule;
    it is a different amount of intervention. ``random_matched`` holds volume
    fixed and varies only whether the score was used.
    """
    cohort = _cohort(lever, frame)
    if depth == "random_matched":
        if rng is None or matched_to is None:
            raise ValueError("random_matched needs an rng and a volume to match")
        pool = np.flatnonzero(cohort)
        k = min(int(matched_to.sum()), pool.size)
        chosen = rng.choice(pool, size=k, replace=False)
        out = np.zeros(len(frame), dtype=bool)
        out[chosen] = True
        return out
    return cohort & selection(lever, frame, tiers, score, boundaries, depth)


def _cohort(lever: Lever, frame: pd.DataFrame) -> np.ndarray:
    """Everyone the lever is SHOWN to — which is not everyone it can move.

    The distinction is the whole of intervention A. A ₹30 prepaid incentive is
    displayed at the payment step to every order in the cohort, and every order
    that ends up prepaid collects it — including the ones that were going to pay
    online anyway. That leakage is §10.2's entire argument against a flat offer,
    and a cohort defined as "COD orders only" would delete it by construction and
    make every incentive arm look better than it is.

    A lever that pays nobody anything has no leakage to model, so its cohort is
    just the orders it can act on.
    """
    if lever.applies_to == "failures":
        # F can only move orders that exist BECAUSE a payment failed.
        return frame["paid_via_switch"].to_numpy(bool)
    if lever.cash("incentive_rupees") > 0:
        return np.ones(len(frame), dtype=bool)
    return (frame["payment_method"] == "COD").to_numpy()


def switchable(lever: Lever, frame: pd.DataFrame) -> np.ndarray:
    """Which orders in the cohort can actually move to prepaid.

    Only a COD order can switch, and §10.1 restricts A to customers who have an
    instrument to switch *to*: an incentive to pay online buys nothing from
    someone who cannot pay online. The gate matters more than it looks. Only
    about a third of COD orders in this window carry a saved instrument, and they
    are not a random third — ``has_saved_prepaid_instrument`` sits at −0.60 in
    the COD-choice model, so holding one makes COD less likely in the first
    place. A's reachable population is therefore both small and skewed toward
    the low-risk end of whatever segment it is aimed at.
    """
    mask = (frame["payment_method"] == "COD").to_numpy().copy()
    if lever.applies_to == "failures":
        mask &= frame["paid_via_switch"].to_numpy(bool)
    if lever.cfg.get("require_saved_instrument"):
        mask &= frame["has_saved_prepaid_instrument"].to_numpy(bool)
    return mask


def restrictive_set(frame: pd.DataFrame, score: np.ndarray,
                    volume: float) -> np.ndarray:
    """A47's ruled policy: rank within geo tier, then apply the §8.4 overlay.

    ``volume`` is the share of orders restricted, and it is a separate decision
    from the ruling: A47 ruled on *which* orders a restriction picks, not how
    many. The caller sets it from the risk band the depth is willing to touch, so
    a restriction never treats more orders than the economics say are worth
    treating — it just chooses them per-tier instead of globally.
    """
    flags = policy.per_tier_flags(score, frame["geo_tier"], float(volume))
    overlay = apply_overlay(frame.reset_index(drop=True),
                            pd.Series(np.where(flags, "HIGH", "LOW")))
    return (overlay["tier_final"] == "HIGH").to_numpy()


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


def pp_denominator(frame: pd.DataFrame, lever: Lever, tiers: pd.Series,
                   score: np.ndarray, boundaries: dict) -> float:
    """Convert §10.1's population pp figures into a within-COD switch rate.

    C and E are stated as *population* moves in prepaid share — "+2-4pp",
    "+3-6pp" — and those figures describe what the lever delivers **when
    deployed at the depth §10.1 specifies**. So the denominator is the share of
    ALL orders that are both inside the specified segment and COD:

        switch rate = pp / mean(in_specified_segment AND is_cod)

    Two mistakes this avoids, and they pull in opposite directions.

    Dividing by the *cohort's* COD share — 1.0 for a COD-only cohort — would
    read "+3pp of the population" as "3% of COD orders" and understate both
    levers by the reciprocal of the segment's COD share.

    Re-computing the denominator at each depth would be worse: it would make the
    within-COD switch rate *rise* as the deployment narrows, so a UI change
    applied to fewer people would produce a bigger per-person effect. The rate is
    a property of the intervention, not of how widely it is rolled out, so it is
    computed once at the specified depth and held fixed across depths. Deploying
    narrower then delivers proportionally less, which is the physically sensible
    behaviour.
    """
    is_cod = (frame["payment_method"] == "COD").to_numpy()
    specified = selection(lever, frame, tiers, score, boundaries,
                          lever.blueprint_depth)
    share = float((is_cod & specified).mean())
    return share if share > 0 else float(is_cod.mean())


def response(lever: Lever, cohort_cod_share: float,
             level: str = "central") -> dict:
    """The [A] behavioural rates for this lever, at one point in its band.

    ``switch`` always comes out as a rate on the COD orders the lever can move.
    Where §10.1 states a population pp move instead, ``cohort_cod_share`` is the
    conversion factor — see :func:`pp_denominator`.
    """
    if lever.applies_to == "failures":
        # F's cohort is already defined as the failure-driven COD orders, so the
        # share of failures the fix removes IS the share of that cohort that ends
        # up prepaid. There is no second parameter to assume.
        switch = lever.band("failure_fix_share", level)
        stated_as = "share of failure-driven COD orders the fix removes"
    elif "prepaid_share_pp" in lever.cfg:
        pp = lever.band("prepaid_share_pp", level)
        switch = pp / cohort_cod_share if cohort_cod_share > 0 else 0.0
        stated_as = "population pp -> within-COD rate"
    else:
        switch = lever.band("switch_share", level)
        stated_as = "within-COD rate"

    abandon = lever.band("abandon_relative", level)
    if lever.id == "G":
        # Gating is absolute: an eligible COD order becomes prepaid or does not
        # happen. There is no third branch, so abandonment is not a free
        # parameter — it is whatever switching is not.
        abandon = 1.0 - switch
        stated_as = "gate: switch + abandon = 1 by construction"

    switch = float(min(switch, 1.0))
    if switch + abandon > 1.0:
        abandon = max(0.0, 1.0 - switch)

    return {
        "level": level,
        "switch": switch,
        "abandon": float(abandon),
        "abandon_prepaid": lever.band("abandon_prepaid", level),
        "cod_fee": lever.cash("cod_fee_rupees"),
        "incentive": lever.cash("incentive_rupees"),
        "partial": lever.cash("partial_rupees"),
        "partial_retained": bool(lever.cfg.get("partial_retained_on_rto", True)),
        "dose": lever.band("commitment_dose", level) if "commitment_dose" in lever.cfg else 0.0,
        "failure_fix": lever.band("failure_fix_share", level) if "failure_fix_share" in lever.cfg else 0.0,
        "cohort_cod_share": cohort_cod_share,
        "stated_as": stated_as,
    }
