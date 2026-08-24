# Limitations

Known simplifications and deliberate trades in the synthetic dataset. Every entry
names what was given up, why, and what would change if it were modelled.

A limitation recorded here is **not** a defect. A limitation *not* recorded here
is, because it means someone will find it later and have no way to tell whether
it was a choice or an accident.

---

## L1 — `address_completeness_score` is independent of geography

**What.** Address quality is drawn from a single Beta(6.5, 2.2) for every session,
with no dependence on `geo_tier`, `serviceability_score`, or any latent.

**Why.** Two reasons, and the second is the load-bearing one.

1. Spec §3.6 lists no drivers for it.
2. `address_completeness` carries **−1.40** in the RTO logit — the second-largest
   coefficient in the model — and it is the **cheapest intervention** in the whole
   opportunity model. If address quality tracked geography, then "fix the
   addresses" would be statistically indistinguishable from "stop serving Tier-3",
   and the intervention's measured effect would be contaminated by a fairness
   problem it does not actually have. Keeping the two channels separate is what
   makes the address intervention *cleanly attributable*.

**What we gave up.** Realism. Real Tier-3 addresses probably *are* harder to
resolve — unnamed lanes, informal landmarks, inconsistent pincode discipline. The
dataset does not contain that, so any analysis of "where should we deploy address
verification first?" will find no geographic signal, and in reality there would
be one.

**What would change.** Adding a tier→address-quality path would raise Tier-3 RTO
through a second channel, inflate the apparent geographic disparity in the
fairness audit, and make the address intervention look partly like a geography
intervention. The COD/RTO causal structure would be unaffected.

**Where it is checked.** The generation checkpoint reports the realised
correlation between `address_completeness_score` and `geo_tier`, so the property
is *stated and measured* rather than merely intended. Approved as a condition of
decision A28(a).

---

## L2 — `pit_avg_order_value` is NULL until a customer's first in-window order

**What.** The feature builds from in-window orders only. A customer whose entire
history predates the window has NULL, even though they have orders.

**Why.** `dim_customer` (spec §3.5) has no pre-window order-value column, so there
is nothing to average. The two alternatives were both worse: adding a
`pre_window_avg_order_value` column would invent a schema field the spec does not
have, and drawing pre-window order values purely to average them would be an
unflagged assumption feeding a model feature.

**What we gave up.** The feature is sparser than the data dictionary implies —
NULL for most sessions early in the window, filling in as the window progresses.
An analyst reading §3.7 would not expect that.

**What would change.** Nothing in the planted causal structure:
`pit_avg_order_value` carries no coefficient in §7.2 or §8.2. It would matter only
to a risk model that chose to use it, where the missingness is itself informative
and should be handled with the `pit_has_history` indicator rather than imputed —
the same pattern decision A18 applies to `pit_cod_share`.

**Decision.** A30.

---

## L3 — Repeat-visit frequency is not latent-driven

**What.** Sessions are allocated **uniformly at random across customers**, so the
per-customer session count is roughly Poisson. A high-`latent_intent` customer is
no more likely to open checkout than anyone else.

**Note the level carefully.** Sessions are uniform **across customers**, *not*
across days. Daily session volume follows `dim_date.demand_index` — weekday
rhythm, a month-end lift and a salary-week lift. That temporal structure is
load-bearing and is preserved: BR-10 (month-end COD RTO lift) needs month-end to
be a real concentration of traffic, and DQ-14 needs orders to pile up in the late
window so censoring is present to demonstrate maturation bias.

**Why.** No Phase 1 hypothesis involves visit frequency. Making it latent-driven
would open a **second path** from `latent_intent` into the point-in-time features —
high-intent customers would accumulate history faster, so `pit_cod_share` and
`pit_rto_rate_shrunk` would carry extra latent signal that was never planted. That
would strengthen the confounding beyond the specified coefficients and quietly
move CAL-11's selection share.

**What we gave up.** In reality, low-commitment shoppers probably do browse and
abandon more often.

**Decision.** A28(b).

---

## L4 — Single-line orders, and no returns model

**What.** Every order contains one product with a quantity of 1–3. There is no
partial RTO, and no post-delivery returns.

**Why.** Neither appears in any Phase 1 hypothesis, metric, intervention or the
sizing model, and both would roughly double the fulfilment and economics code for
no narrative gain.

**What would change.** Multi-line orders would introduce partial RTO, which splits
freight attribution across lines and would likely *raise* the per-order RTO cost
slightly. A returns model would sit downstream of delivery and would reduce
realised contribution margin on delivered orders — it would not touch the RTO
mechanism, which is a pre-delivery failure.

---

## L5 — No pre-window switch-to-COD

**What.** Pre-window history records COD and prepaid orders, and prepaid payment
failures, but no `paid_via_switch`.

**Why.** The switch is a live in-session behaviour tied to a specific payment
attempt. Synthesising it pre-window would inflate CAL-07's numerator with orders
that never had a session, making the H11 headline look larger than the mechanism
actually produces.

**What would change.** `pit_payment_failure_rate` is unaffected — failures *are*
recorded pre-window. Only the switch attribution is absent, and it is not a
point-in-time feature.

---

## L6 — Day-level resolution granularity in the point-in-time pass

**What.** Under decision A1 the window is simulated day by day. An order's outcome
becomes visible to the point-in-time state only from the **following day**, not
from the moment within the day that it resolved.

**Why, and why it is safe.** The minimum outcome resolution is 4 days, so no order
placed on a given day can resolve that same day. Applying only strictly-earlier
resolutions is therefore conservative: it can only ever *understate* what was
knowable, never overstate it. Understating is safe; overstating is leakage.

**Placements are exact.** Sessions within a day are processed in occurrence-rank
order, so a customer's second session on a day sees the order placed at their
first. This was measured rather than assumed — the checkpoint reports the share of
sessions at rank > 0.

---

## L7 — The distribution values in `params.yaml` are project decisions, not sources

**What.** A large number of distribution parameters — geography scores by tier,
seller and product distributions, demand seasonality, delivery transit, address
quality — are tagged `[A27 PROPOSED]`, `[A28 PROPOSED]` or `[A33 PROPOSED]` in
`config/params.yaml`.

**Why the tags stay after approval.** They are provenance. A tagged value came
from `docs/decision_register.md`, not from the Phase 1 blueprint or the Phase 2
architecture spec. Anyone challenging a number can tell in one glance whether they
are challenging a business assumption or an implementation choice.

**What we gave up.** Nothing measurable — none of these feeds a planted causal
coefficient. The one with real reach is `geography.cod_cultural_index`, which
enters the COD logit at +0.30; its deliberately non-monotone tier ordering is
described in decision A27.
