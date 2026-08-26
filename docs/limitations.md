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

---

## L13 — `attempt_delay_days` is published only on orders that RETURNED

**A projection defect, not a data-generating-process defect.** The distinction
decides the remedy, so it is established with evidence below rather than
asserted.

### What is published

| Population | Orders | with `attempt_delay_days` |
|---|---:|---:|
| Delivered | 76,166 | **0** (0.00%) |
| Returned (RTO) | 15,084 | **15,084** (100.00%) |
| Censored | 10,141 | 0 |
| Cancelled pre-ship | 4,214 | 0 |

It lives on exactly one event type: `DELIVERY_ATTEMPT_FAILED` (45,252 rows, all
populated). `DELIVERED` carries 76,166 rows and zero delays; so does every other
event type.

### Where the generator computes it

`src/generators/simulate.py`, module 16, inside the day loop:

```python
timeline = rto_mod.delivery_timeline(p, order_day, ...)          # ALL ord_pos
shock    = rto_mod.post_dispatch_shock(
    setup["shock_coef"], courier_z, timeline["attempt_delay_days"], ...)
final_logit = pre + shock                                        # ALL ord_pos
drawn = rto_mod.draw_rto(logistic(final_logit), d["u_rto"][ord_pos]) & ship
```

`delivery_timeline` is called on **`ord_pos` — every order in the batch**, not on
an RTO subset. The shock is added for every order. **The outcome is drawn on the
next line, from the probability the delay helped produce.** Line 393 then
collects the delay for all orders: `collected["attempt_delay"][ord_pos] = ...`.

### What δ₂ actually multiplied

Read from the decision-A45 component trace — the audit sample exists for exactly
this kind of question:

| Population | Sampled | `shock.attempt_delay_days` non-zero | Mean term | Implied delay |
|---|---:|---:|---:|---:|
| **Delivered** | 749 | **749 / 749** | +0.4528 | **2.058 days** |
| Returned | 1,044 | 1,044 / 1,044 | +0.6758 | 3.072 days |

**δ₂ = 0.22 multiplied a real, non-zero, per-order delay for every delivered
order.** The variable existed and was used throughout generation. The higher mean
on returned orders (3.07d against 2.06d) is the planted causal effect showing up
as a difference in means — which is what a working Stage-2 driver looks like.

### Therefore

| Concern | Verdict |
|---|---|
| δ₂ multiplied an outcome-conditional variable (circular) | **No.** The delay is computed for all shipped orders and the outcome is drawn afterwards from it. The causal order is delay → shock → probability → draw |
| The shock term is partly leakage-shaped | **No.** `attempt_delay_days` is a post-dispatch fact, is absent from `leakage_guard.safe_feature_whitelist`, and does not appear in `vw_risk_model_input`. The firewall is intact and LK-01 passes |
| **H6 cannot be tested as specified** | **Yes.** This one stands. Realised delay is *published* conditional on the outcome, so no model built on the warehouse can use it without being circular |

### Root cause

The column was hung on an **event type that only exists for failures**. Under
decision A8 it is a property of the order's *first delivery attempt*, and every
shipped order has one — but `build_delivery_events` emits an attempt event only
when the attempt failed. A delivered order's successful first attempt is emitted
as `DELIVERED`, with `with_delay=False`:

```python
emit(observable & delivered, "DELIVERED", days_to_resolve.astype(float))          # no delay
emit(observable & rto, "DELIVERY_ATTEMPT_FAILED", offset, ..., with_delay=True)   # delay
```

The rename away from `delivery_delay_days` was specifically intended to make the
column outcome-independent. The rename happened; the emission did not follow it.

### Fix options, for a ruling

The delay is already collected for every order and is in scope inside
`build_delivery_events` as `attempt_delay = extra["attempt_delay"][idx]`, so no
new quantity has to be computed.

| Option | Change | Consequence |
|---|---|---|
| **(a)** Pass `with_delay=True` on the `DELIVERED` emit | one flag | Populates 76,166 existing rows. The DDL comment "NULL on non-attempt events" still holds — a successful delivery *is* an attempt |
| **(b)** Emit a `DELIVERY_ATTEMPT_SUCCEEDED` event for delivered orders | new event type | +76,166 rows; changes the event enum and the DQ event-sequence expectations |
| **(c)** Move `attempt_delay_days` onto `fct_order` | schema move | Contradicts A8's own placement ruling and spec §3.11 |

**Regeneration cost is low and DQ-01 survives.** `fct_delivery_event` is a leaf
projection; nothing downstream reads it. No order-grain value changes, so
`fct_order`'s content hash stays byte-identical and the DQ-01 baseline does not
need re-establishing. The reload and the 68-assertion cross-check would re-run.

**Not fixed here.** This is a Phase 2 change to a published table and needs a
ruling, not an analyst's edit. H6 stays **CANNOT SETTLE** in
`reports/phase3_findings.md` §D.5 until it is made.

### Class

An **A44-class finding**: a decision true of the code and false of the data,
found only when something tried to use the column. The 42 data-validation tests
cannot catch it — a column that is NULL on a principled subset violates no rule
they encode, and `attempt_delay_days` *is* legitimately NULL on non-attempt
events. The check that would catch it is not "is this column ever populated" but
"**is this column's population independent of the outcome**".

**What is not lost.** The promise side of H6 is fully testable and is measured
(§D.5): +1.8pp of RTO per extra promised day at the honest specification, with
most of the apparent promise effect turning out to be geography.

---

## L14 — Planted coefficient MAGNITUDES are not recoverable; signs and ranking are

**Accepted by decision A49.** GT-01's magnitude clause is retired as a grading
criterion and recorded here instead. GT-01 is now graded on its sign clause,
which passes cleanly.

**What.** A logistic regression of `rto_flag` on the reconstructed safe-feature
design matrix recovers **0 sign flips across 13 Strong/Moderate relationships**
and **8.0% of 25 testable coefficients inside the fitted 95% CI**, against the
original clause's ≥80%.

**Why — and it is a mechanism, not a defect.** Decision **A37** raised
`post_dispatch_shock.noise_sd` from **0.85 to 3.3125** and **A38** froze it. The
generator draws

```
logit(p) = Xβ + ε,     ε ~ N(0, 3.3125²)
```

A model fitted on `X` alone cannot see `ε`. It converges not on `β` but on
`β / sqrt(1 + 3σ²/π²)` — textbook latent-noise attenuation. Predicted factor
**0.480**, against **0.906** at the σ = 0.85 the clause was written for. GT-01's
80% threshold is a σ = 0.85 figure, the same vintage as **L8**'s retired
13.4pp / 19.9pp / 33%.

### The attenuation factor, and the thing the mean hides

Across the 13 Strong/Moderate relationships:

| Statistic | Value |
|---|---|
| Mean recovered ÷ planted | **1.011** |
| Median | 0.741 |
| SD | 0.587 |
| **Coefficient of variation** | **0.581** |
| Range | 0.200 – 2.018 |
| Attenuated (ratio < 1) | 7 terms |
| Inflated (ratio ≥ 1) | 6 terms |

**The attenuation is NOT uniform, and the mean of 1.011 is the most misleading
number on this page.** It reads as "no attenuation at all". What it actually
averages is two opposing effects:

* **Seven terms are attenuated toward zero**, as predicted — `seller_rating`
  0.20, `serviceability_z` 0.37, `address_completeness` 0.43, `is_new_customer`
  0.58, `is_cod` 0.68, `month_end_x_cod` 0.74, `log1p_orders_delivered` 0.74.
* **Six terms are inflated above 1.0** — `pit_rto_rate_shrunk` 1.19, the three
  `geo_tier` contrasts 1.35–1.74, `paid_via_switch` 1.56,
  `seller_sla_breach_rate` 2.02. These are exactly the terms that **proxy the
  omitted latents and `shock.*` terms**, so omitted-variable bias pushes them up
  while noise attenuation pushes everything else down.

The two effects happen to cancel in the mean. Quoting the mean alone would assert
that recovery is unbiased, which is the opposite of what is happening.

### The attenuation is HETEROGENEOUS, so ranking is NOT guaranteed preserved

State this plainly wherever the attenuation is quoted, because the convenient
inference is available and it is invalid here.

**The usual argument — *uniform attenuation is one common rescaling, so the
ordering survives* — does not apply.** At CV 0.581 the attenuation is
heterogeneous, and a heterogeneous rescaling can reorder terms arbitrarily.
Nothing about A37's noise *guarantees* that the recovered ranking matches the
planted one. Whether it does is an empirical question, and it was measured rather
than assumed:

* **Spearman ρ between |planted| and |fitted| across the 13: 0.823.**

**That is high, so the ranking does survive — as a measured fact about this
dataset, not as a property of the mechanism.** It could have come out low; it did
not. Two terms account for most of the disagreement that remains:
`seller_sla_breach_rate` (planted 1.20, fitted 2.42) overtakes `is_cod` (planted
1.60, fitted 1.08), and `seller_rating_centered` (ratio 0.20) sinks below terms
planted smaller than it. Both are on the inflated-proxy side of the split above.

And the claim the risk model actually depends on is not the ranking of
*coefficients* but the ranking of *orders*. That is measured by AUC: **0.7530**
(M1) and **0.7684** (M2) against a **0.7717** achievable ceiling — within 0.4pp
of the best any model could do on this data. **The risk model is fine.**

Re-measure ρ if `post_dispatch_noise_sd` ever moves. It is not implied by the CV
and cannot be carried forward from this run.

### What cannot be tested at all

Eight planted terms are excluded from the denominator because no safe-feature
model can estimate them: three latents (`latent_intent`, `latent_liquidity`,
`latent_trust` — invariant 4 keeps them in a schema `analyst` is denied on) and
five `shock.*` terms (the Stage-4 bar). Named explicitly in
`src/analysis/gt_recovery.py:UNTESTABLE` so the exemption cannot quietly widen.

### Consequence for any future magnitude test

Under σ = 3.3125, **no test comparing a fitted coefficient's CI to an
un-attenuated planted value can pass at this sample size.** GT-04 demonstrated it
independently: planted −0.05, fitted −0.0219, ratio 0.439 against the predicted
0.480; the CI half-width is 0.0136 while the attenuation gap is 0.0281, twice as
wide. Any such test must compare against `planted × expected_attenuation(σ)`, or
test signs, ordering, or one-sided inflation instead.

**This generalisation is what retired GT-04's coverage clause.** Ruling **A50**
restated GT-04 from *"the CI contains −0.05"* to sign + effect-size +
`|fitted| ≤ |planted|`, and it now **passes**. The third clause is one-sided on
purpose: attenuation below the plant is the expected, harmless direction, while a
magnitude *above* the plant is the over-fitting the test exists to catch.

**Decisions.** A37 (the noise), A38 (the freeze), A49 (this acceptance),
A50 (GT-04's second restatement, on this limitation's own generalisation).

---

## L15 — The confounding is weaker than designed; the residual is an optimistic floor

**Accepted by decision A51**, on the diagnostics ruling **A50** ordered. This is
the most important limitation on this page for anyone quoting a causal number out
of this project.

**What.** The observed confounder set recovers **more** of the COD–RTO selection
component than the design intended. The propensity match closes **70.9%** of the
naive-to-AME distance and the logistic ATT closes **66.9%**, against a ceiling
originally set at 65%.

**Why — and it is not leakage.** A50 measured both candidate explanations
directly (`reports/gt03_diagnostics.md`, `make gt03`):

| Target | Kind | Order-level R² | Customer-level R² |
|---|---|---|---|
| `latent_intent` | latent | 0.151 | 0.161 |
| `latent_trust` | latent | 0.220 | 0.225 |
| `latent_liquidity` | latent | 0.288 | **0.295** |
| `true_cod_propensity` | **choice channel** | 0.819 | **0.853** |

**No latent is substantially reconstructible** — the highest is 0.295, under the
0.35 bar — so "unobservable by construction" holds exactly as written, and
nothing crossed the firewall. `analyst` is still denied on schema `truth` with
SQLSTATE 42501, asserted inside the cross-check.

What *is* reconstructible is **treatment assignment**. Decision **A11** generates
pre-window history from the **same latent slopes** that drive current COD choice,
which makes `pit_cod_share` close to a **sufficient statistic for the propensity
score**. The propensity model's own AUC of **0.835** says the same from the other
side. And in a matching framework, recovering assignment well is most of what an
estimator needs — so the adjustment recovers **assignment well and latent values
poorly**, which is a coherent finding rather than a defect.

The deviance table makes the same point from a third angle: the top three
gap-closers are all COD-choice history and are collectively under 3% of explained
deviance, while `courier_reliability_score` carries **10.6%** of explained
deviance and closes **nothing** — it explains RTO without explaining COD choice.

| Confounder | Deviance share | Closure lost if dropped |
|---|---|---|
| `pit_cod_share` | 1.5% | **+8.06pp** |
| `pit_has_history` | 0.9% | **+5.05pp** |
| `has_saved_prepaid_instrument` | 0.6% | +2.77pp |
| `courier_reliability_score` | **10.6%** | **−0.36pp** |

**What we gave up — and this is the part to carry into any write-up.** A real
marketplace would almost certainly have **less recoverable treatment assignment**
than this simulation does. Real COD choice is driven by transient, unrecorded
things — who is home that week, whether the card was declined at a different
merchant, what a relative advised — none of which lands in a `pit_*` aggregate.
A real propensity model would score well below 0.835, and correspondingly **more
residual confounding would survive adjustment**.

> **So the ~29% irreducible residual measured here is an OPTIMISTIC FLOOR, not a
> realistic estimate.** On real data the unexplained share would very likely be
> larger. Never present 29% as "how much confounding survives adjustment in
> practice"; present it as "how much survived even when assignment was unusually
> easy to model".

**What would change if it were modelled differently.** Severing A11's
history-from-latents — drawing pre-window history from its own noise rather than
from the latent slopes — would drop the choice channel's recoverability, push
closure back under 65%, and make the residual more realistic. It would also
change the data-generating process, invalidate the whole Phase 3 analysis, and
break the property that a customer's history is *consistent* with the latents
that produced them. **It was not done, and A51 records why:** the finding is
coherent, it is documented, and re-generating to make an intuition-set threshold
come out right is the failure mode this project exists to avoid.

**What was NOT done to make this pass.** `pit_cod_share` and `pit_has_history`
were not dropped from the confounder set; the stratified estimate (which controls
for no behavioural history and would have passed at 40.6%) was not promoted to
primary; and the ceiling was raised **on the stated mechanism, not on the
measured 70.9%** — 70.9% and 66.9% both sit inside [20%, 75%] with room, and an
estimate landing on the AME still fails.

**Where it is checked.** `GT-03` (HARD), band in
`src/validation/tests_gt.GT03_CLOSED_BAND`. Diagnostics in
`src/analysis/gt03_diagnostics.py`, regenerated by `make gt03`.

**Related.** [[L14]] (what *is* and is not recoverable about the planted
coefficients), L11 (`true_cod_propensity` is NULL for 6% of customers, so the
0.853 figure is measured on the 94% that have one).

**Decisions.** A11 (history from latents), A50 (the diagnostics), A51 (this
acceptance and the 75% ceiling).
