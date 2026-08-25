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

---

## L8 — `post_dispatch_shock.noise_sd` was specified at 0.85 and calibrated to ~3.3

**What.** Phase 2A §13.2 specified `noise_sd = 0.85` and annotated it
*"★ the AUC ceiling lever ★"*. Empirical calibration against its declared
purpose — GT-05's AUC ceiling — solved it to **3.3125**.

**Why the specified value was wrong.** At 0.85, three independent quantities miss
their spec targets *together*: the AUC ceiling (0.87 vs 0.74–0.79), prepaid RTO
(2.61% vs 4.1%), and the naive COD−prepaid gap (22.5pp vs the ~19.9pp §8.3
derives). All three land on target at 2.6–3.3. Three independent targets
converging is not coincidence — it says 0.85 was the inconsistent value.

There is a mechanism. Symmetric logit-scale noise applied to a **low** baseline
probability is convex, so it lifts the low arm — prepaid RTO — disproportionately.
Too little noise therefore depresses prepaid RTO *and* widens the gap, which is
exactly the pattern measured.

**What moved as a consequence.** Both are **derived** quantities under decision
A6, so these are restatements rather than violations:

| | at `noise_sd` 0.85 | at 3.3125 |
|---|---:|---:|
| naive COD−prepaid gap | 22.51pp | **17.64pp** |
| AME (canonical COD effect) | 14.70pp | **10.05pp** |
| CAL-11 selection share | 0.347 | **0.430** |
| AUC ceiling | 0.8745 | **0.7700** |

The spec's §8.3 narrative quotes a 19.9pp naive gap and a ~13.4pp effect. Those
figures belong to the un-calibrated shock and no longer describe the dataset.
Any write-up must quote the measured values.

**The one to watch.** CAL-11's selection share sits at **0.430** against a
[0.25, 0.45] ceiling. It is inside, but the margin is 0.02. If a later change
raises the noise further, CAL-11 is the test that will fail first — and CAL-11
failing means the dataset stops supporting the case study, so it should be
re-checked after any change to the RTO model.

**Decision.** A37.

---

## L9 — Annualising a 90-day window must exclude censored orders

**What.** The annualised RTO exposure must be computed as
`(RTO ÷ resolved orders) × mean RTO cost × population`, **not** as
`total RTO cost × population ÷ total orders`.

**Why it matters — this is a real trap, and it cost ₹22 Cr.** In a 90-day window
with 4–25 day outcome resolution, **9.5%** of orders are censored: a day-88 order
cannot resolve before the window closes. A further **4.0%** cancel pre-ship and
never generate an RTO at all.

The naive formula treats a censored order as a **zero-cost** order. It is not — it
is a real future RTO nobody can see yet. The deflation factor is exactly
105,597 ÷ 91,363 = **1.1558**:

| | |
|---|---:|
| all-order denominator (wrong) | ₹142.7 Cr |
| **resolved denominator (correct)** | **₹164.9 Cr** |
| spec §12.4 arithmetic | ₹164.7 Cr |

**Why this is a finding and not just a bug.** It is exactly the maturation bias
blueprint §11 predicts and DQ-14 exists to demonstrate rather than assert. An
analyst who annualises a partial window naively will **under-size the opportunity
by about a sixth** — and will do it invisibly, because nothing in the arithmetic
looks wrong. The dataset contains the censoring precisely so that this can be
shown rather than argued.

**Decision.** A41.

---

## L10 — About 5% of RTO orders would have lost money even if delivered

**What.** `counterfactual_cm_if_delivered` is negative for **4,956** RTO orders —
roughly 5% of the RTO population. At low order values, fixed freight and packaging
exceed the available margin.

**Why it matters for Phase 3.** For those orders the RTO did not destroy value;
delivery would have. No payment-reliability or address-quality intervention makes
an unprofitable order profitable, so the intervention set needs a **"don't take
this order"** tier alongside the payment and address levers.

**Where to find it.** `fct_order_economics.counterfactual_cm_if_delivered` is
stored per order, so the unprofitable-if-delivered population can be sliced
directly without re-deriving anything.

**How it surfaced.** DQ-04 flagged `foregone_cm` as a negative cost line. It is not
a cost line — it is the counterfactual CM, and the test's exclusion list was
incomplete. Fixing the test exposed the result.

**Decision.** A42.

---

## L11 — `true_cod_propensity` is NULL for 6% of customers

`truth.truth_customer_latent.true_cod_propensity` is the customer-level mean
P(COD) across their sessions (spec §3.13). **3,284 of 55,000 customers (6.0%)
open no checkout session inside the 90-day window**, so that mean has no
denominator and the column is NULL for them.

This is the same rule as decision A18 and limitation **L2** (decision **A30**,
`pit_avg_order_value`): **a statistic with no data behind it stays NULL.** The
two cases are the same pattern in two different tables — an average with an empty
denominator, discovered the same way, resolved the same way. Neither is imputed
and neither is dropped; both are declared NULL and documented here.

Imputing the population COD share would have
invented 3,284 fictitious COD-average customers *inside the truth table* — the
one place in the project where a fabricated value cannot be caught by anything
downstream, because the truth table is what everything else is checked against.

The `NOT NULL` originally written on this column was wrong and was removed. Spec
§3.13 declares the type only.

**Consequence for Phase 3+.** Any analysis joining `truth_customer_latent` to a
customer-level population must decide explicitly whether it means *all customers*
or *customers who shopped*. The two differ by 6%, and an inner join silently
picks the second.

---

## L12 — The per-term logit traces cover a 2,000-session audit sample, not the whole table

`truth_order_probability.logit_cod_components` and `logit_rto_components` are
declared JSONB in the schema (spec §3.13: "every additive term in the logit, by
name"). They are populated for **1,995 of 155,000 sessions** (1.29%) — a
documented stratified audit sample — and NULL for the rest. Decision **A45**.

**Why not all of them.** These columns exist to make GT-01 auditable: a Phase 5
reviewer opens *one* order and reads every additive term that produced its
probability. That is a lookup, never a scan. Populating all 155,000 sessions
costs roughly **190 MB of JSONB for a diagnostic that is never run in bulk**, so
full population was rejected on cost against zero analytical gain.

**Why not zero of them.** Because the alternative on offer was shipping a
declared-but-empty column indefinitely, which is the worst of the three options —
it looks like data and is not.

**The sample.** 500 each of: a random draw across all sessions; COD orders that
RTO'd; prepaid orders that RTO'd; and top-decile-`p_rto_precheckout` orders that
delivered. The last three are the cases anyone would actually inspect. Strata
overlap is de-duplicated, so the realised count is 1,995 rather than exactly
2,000, and is recorded in `data/truth/_truth.json` under `component_trace_sample`
rather than assumed. 1,836 of them carry an RTO trace; the remainder produced no
order, so there is no RTO logit to decompose.

**The NULLs are stated, not ambiguous.** `components_populated` (BOOLEAN NOT
NULL) says per row whether a trace should be there, and two CHECK constraints
tie the flag to the data so it cannot drift from it. Without that column, "no
components" and "not sampled" would be indistinguishable — the exact defect class
decision A44 was written about.

**Consequence for Phase 3+.** Any query that joins to these columns must filter
on `components_populated`, or accept that ~98.7% of rows return NULL. They are a
diagnostic convenience — explaining a single order's score — and are an input to
no test. Every coefficient is still recorded, by name and by block, in the
runtime `CoefficientLedger` and persisted into `_truth.json` (test CAL-09);
what the unsampled rows lack is only the *per-row* decomposition.

**Fidelity.** The trace is reconstructed after the day loop, because three of the
four strata are defined by outcomes. The generator re-derives each sampled
probability from its own trace and refuses to store one that disagrees; the
realised worst error is 8.88e-16.

