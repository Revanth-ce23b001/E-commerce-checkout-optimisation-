# Phase 3 — Diagnostic findings

*Status: **A (funnel), B (RTO economics) and C (H1 decomposition) complete.**
D (H2–H6, H11) not started.*

Every figure below comes from PostgreSQL — queried as the restricted `analyst`
role — or from `data/truth/_truth.json`. None comes from spec prose, which
belongs to an earlier parameterisation (limitation L8).

All 48 metrics are asserted identical between SQL and Python by
`scripts/05_crosscheck.py`, which also asserts that `analyst` is denied on
schema `truth` with SQLSTATE 42501. A passing cross-check is a precondition for
anything in this document.

---

## A. The funnel

### A.1 The opening argument

| | |
|---|---:|
| Sessions started | 155,000 |
| Orders placed | 105,605 |
| **Checkout conversion** (orders ÷ sessions) | **68.13%** |
| **Net conversion** (delivered ÷ sessions) | **49.14%** |
| **The leak** | **18.99pp** |

**Nearly one in five checkout sessions produces an order that never becomes
revenue.** Checkout conversion — the metric a checkout team is typically
targeted and bonused on — counts 68 of every 100 sessions as a win. Only 49 of
them end with goods the customer keeps and pays for.

This is the project's opening argument, and it is a measurement argument before
it is an operational one. *Any* intervention judged on checkout conversion can
look successful while destroying value, because the two metrics can move in
opposite directions: taking on a marginal COD order raises the first and lowers
the second.

**One honest correction.** 10,141 orders (9.6%) were shipped but had not
resolved by the window close. They are neither delivered nor returned, so they
depress raw net conversion mechanically. Restated on the resolved population,
net conversion is **56.87%** and the leak is **11.26pp**. Censoring is an
artefact of a 90-day observation window, not a real-world outcome, so the
resolved-basis figure is the one to quote for a steady state — but both are
reported and neither replaces the other (decision A10, limitation L9).

The leak is real on either basis.

### A.1b Which net conversion we quote, and why

Net conversion moves by 7.7pp depending on how the 10,141 censored orders are
handled. On the project's headline funnel metric that is too large a swing to
leave implicit, so every defensible definition is stated with its denominator.

| Definition | Censored treated as | Net conversion | Leak |
|---|---|---:|---:|
| delivered ÷ sessions | failures | 49.14% | 18.99pp |
| (delivered + censored) ÷ sessions | successes | 55.68% | 12.45pp |
| **checkout conv × (1 − blended RTO rate)** | **excluded** | **56.87%** | **11.26pp** |
| shipped ÷ sessions | ignores all failure | 65.41% | — |

**Ruling applied: the resolved-denominator version (56.87% / 11.26pp) is
primary.** It is the only one where every order in both the numerator and the
denominator has a *known* outcome. The other two each assume an answer for the
censored orders — one that they all failed, one that they all succeeded — and
neither assumption is observed.

The 49.14% figure is reported alongside as the **conservative bound**: it is the
worst case, and it is the number to quote when someone asks what the leak could
be if every unresolved order goes wrong.

**This is limitation L9's censoring trap in a different costume.** L9 records it
on the *cost* side, where annualising across censored orders understates the RTO
bill by 15.7%. Here the identical mistake works in the opposite direction — the
same orders, counted as failures, make the funnel look *worse* than it is. One
unresolved-outcome population, two headline metrics, and the bias points opposite
ways in each. That is why the rule is to state the denominator rather than to
memorise a direction.

*Note: the ~62.4% figure raised in review does not correspond to any denominator
convention I can reproduce from this data — the nearest are 55.68% and 65.41%.
Flagging rather than reverse-engineering it.*

### A.2 The funnel, step by step

| Step | Sessions | % of sessions started | Step conversion |
|---|---:|---:|---:|
| Sessions started | 155,000 | 100.00% | — |
| Address completed | 135,625 | 87.50% | 87.50% |
| Payment page reached | 106,852 | 68.94% | 78.79% |
| Orders placed | 105,605 | 68.13% | 98.83% |
| Shipped | 101,391 | 65.41% | 96.01% |
| **Delivered** | **76,166** | **49.14%** | **75.12%** |

The largest single drop *inside checkout* is address → payment page: 28,773
sessions, 18.6% of all sessions. But the largest drop **anywhere in the funnel**
is the last one — 25,225 shipped orders that never became a delivery. That step
loses more sessions than the address step and the payment step combined, and it
is the only one that is invisible to a checkout dashboard.

### A.3 Where sessions die

| Abandon step | Sessions | % of sessions | % of abandons |
|---|---:|---:|---:|
| *Converted* | 105,605 | 68.13% | — |
| PAYMENT_PAGE | 28,773 | 18.56% | 58.25% |
| ADDRESS | 19,375 | 12.50% | 39.22% |
| PAYMENT_FAILURE | 1,247 | 0.80% | 2.52% |
| FEE_REVEAL | 0 | 0.00% | 0.00% |

`FEE_REVEAL` is empty by construction: no shipping fee is charged in the
baseline, so the branch has nothing to fire on. It is the diagnosis waiting for
a fee intervention, not a dead code path (decision A25) — and it is the metric
that would carry the guardrail if Phase 5 ships a COD fee.

`PAYMENT_FAILURE` abandonment is small — 1,247 sessions, 0.8% — but it is the
only line in this table that is unambiguously a **defect** rather than a
**preference**. Fixing it costs no conversion and no margin. See A.4.

### A.4 Payment reliability

| | |
|---|---:|
| Sessions attempting prepaid | 44,907 |
| Total attempts | 48,870 |
| Prepaid orders | 39,778 |
| **Prepaid success rate** | **88.58%** |
| Switched to COD after a failure | 3,882 |
| **COD orders originating in a payment failure** | **5.90%** |
| Sessions abandoned at payment failure | 1,247 |

COD has no payment-success analogue, so these are prepaid-only figures and must
never be blended with COD (Phase 1 §5.3).

**H11 lands below its pre-registered prior** — 5.90% against 8–15%. Full
treatment belongs in section D, but the number is produced here because it is a
funnel measurement. The mechanism is recorded in decision A35: the payment-failure
parameters were set from plausible external gateway ranges, and the 8–15% prior
was an independent guess about the outcome. The two were never reconciled.

The product implication survives the miss. 3,882 COD orders per window are
*manufactured by a reliability defect* rather than chosen, and a further 1,247
sessions are lost outright. Payment reliability remains the only intervention in
the library with **zero conversion risk and zero margin cost** — it is just
smaller than Phase 1 hoped.

### A.5 The CM/CSS baseline

| Metric | Value |
|---|---:|
| **Contribution margin per checkout session started** | **₹19.21** |
| Contribution margin per delivered order | ₹114.27 |
| RTO drag per session started | ₹41.16 |

This is the north-star baseline every Phase 5 intervention will be scored
against.

The third line is the one to sit with. **RTO destroys ₹41.16 per session while
the business earns ₹19.21 per session.** The failure drag is more than twice the
realised margin. Put the other way: absent RTO cost entirely, CM/CSS would be
roughly ₹60 — so the current state retains about a third of the margin the
funnel generates.

---

## B. RTO economics

### B.1 The split

| | Resolved orders | Share | RTO orders | **RTO rate** | Economic cost / RTO | Annual exposure |
|---|---:|---:|---:|---:|---:|---:|
| COD | 56,216 | 61.61% | 13,120 | **23.34%** | ₹423.54 | **₹146.15 Cr** |
| Prepaid | 35,034 | 38.39% | 1,964 | **5.61%** | ₹418.81 | **₹21.63 Cr** |
| **Blended** | **91,250** | 100% | **15,084** | **16.53%** | ₹422.92 | **₹167.79 Cr** |

Denominator is **shipped AND NOT censored** throughout. Using orders placed
would let an intervention that raises pre-ship cancellations *appear* to cut RTO
while doing nothing (Phase 1 §5.3, the definitional trap).

The raw COD–prepaid gap is **17.73pp**. Section C exists to establish how much
of that is causal and how much is selection. **Nothing in this section should be
read as a causal claim about payment method.**

### B.2 Annualisation — derived, not assumed

| | |
|---|---:|
| Sample orders | 105,605 |
| Population annual orders | 24,000,000 |
| **Annualisation factor, derived** | **227.26** |
| Phase 1 §7.2 stated factor | 240 |
| Difference | −12.74 |

Phase 1's ×240 silently encoded a 100,000-order sample. The factor is derived as
24,000,000 ÷ actual orders, so changing the session knob cannot move the headline
for a reason that is not a business reason (decision A32, test EC-08).

**A second and larger correction sits underneath it.** Annual exposure is
computed as *cost per resolved order × annual orders*, not as *total cost ×
factor*. Those differ by **15.7%**, because the order count includes 10,141
censored orders carrying no resolved outcome; spreading RTO cost across them
treats an unresolved order as a costless one. Getting this wrong understates the
headline by ₹22 Cr (decision A41, limitation L9).

### B.3 The waterfall

| | | |
|---|---:|---:|
| **Total annual RTO exposure** *[measured]* | **₹167.79 Cr** | |
|  of which cash out the door | ₹125.10 Cr | 74.56% |
|  of which foregone contribution margin | ₹42.69 Cr | 25.44% |
| − Structurally unavoidable *[measured]* | −₹64.70 Cr | 38.56% |
| **= Addressable opportunity** *[measured]* | **₹103.09 Cr** | 61.44% |
| × Intervention efficacy **[A — PLACEHOLDER]** | ×0.30 | |
| **= Recoverable opportunity** **[A — PLACEHOLDER, to be replaced by the measured ATE in Phase 6]** | **₹30.93 Cr** | |

> ### ⚠ ₹30.93 Cr is the least defensible number in this project
>
> It rests entirely on a **30% intervention efficacy assumption for which there
> is no evidence at all** — not a weak estimate, not a benchmark, an unevidenced
> placeholder carried from Phase 1 §7.2. Phase 1 §7.4 is explicit that it "must
> come from the experiment, not assumption."
>
> Every line above the addressable opportunity is measured. This one is not.
> Halving the assumption halves the number; nothing in this dataset would
> contradict either value. **Do not put it in front of a finance partner until
> Phase 6 has replaced it with a measured ATE.**

**EC-07 cross-check.** ₹6,379,369.34 total RTO economic cost ÷ 91,250 resolved
orders = ₹69.91 per resolved order × 24,000,000 = **₹167.79 Cr**. Reconciles
from first principles, and sits inside EC-07's 150–180 Cr band.

Two things changed relative to Phase 1's version of this waterfall.

**The addressable share is now measured, and it is lower than assumed.** Phase 1
§7.2 assumed 65% addressable. Measured on cost it is **61.44%** — close enough
that the funding case survives, and far enough that quoting 65% would have
overstated the addressable pool by ₹6 Cr. §7.4 set the failure threshold at
"unavoidable > 50%", and at 38.56% structural we clear it comfortably.

**The efficacy line is still an assumption and is labelled as one.** Everything
below the addressable line is assumption-dependent. Only the Phase 6 experiment
can replace 30% with a measured ATE. The waterfall is deliberately built so a
challenge can be aimed at the right layer: the top is arithmetic, the middle is
now measured, the bottom is a placeholder.

**Never quote ₹167.79 Cr as fundable value.** It is exposure — Phase 1 §7.1's
number 3. The resume bullet's "₹165 Cr" is this line, and the honest sequence is
to say so and then immediately show the funnel down to ₹30.93 Cr.

### B.4 Addressable vs structural — the full decomposition

Phase 1 §7.2 **assumed** 65% addressable. Measured on cost it is **61.44%**. That
is a 3.56pp miss on a number that feeds straight into the waterfall, and it is
logged below as a fourth missed prior alongside H2, H3 and H11. The measured
value replaces the assumption everywhere; 65% is not restated.

All ten reasons, both splits, ordered by how far the cost share runs ahead of the
count share:

| Class | Reason | Orders | Count share | Cost | **Cost share** | Cost − count | Avg cost |
|---|---|---:|---:|---:|---:|---:|---:|
| ADDRESSABLE | **INSUFFICIENT_CASH_AT_DELIVERY** | 1,531 | 10.15% | ₹7,45,397 | **11.68%** | **+1.53pp** | **₹486.87** |
| ADDRESSABLE | ADDRESS_INCORRECT_INCOMPLETE | 584 | 3.87% | ₹2,47,839 | 3.89% | +0.01pp | ₹424.38 |
| STRUCTURAL | DELIVERY_ATTEMPTED_OUTSIDE_WINDOW | 982 | 6.51% | ₹4,13,154 | 6.48% | −0.03pp | ₹420.73 |
| STRUCTURAL | OTHER_UNCLASSIFIED | 376 | 2.49% | ₹1,55,803 | 2.44% | −0.05pp | ₹414.37 |
| STRUCTURAL | CUSTOMER_UNAVAILABLE_GENUINE | 1,127 | 7.47% | ₹4,70,203 | 7.37% | −0.10pp | ₹417.22 |
| STRUCTURAL | PINCODE_SERVICEABILITY_FAILURE | 1,845 | 12.23% | ₹7,70,731 | 12.08% | −0.15pp | ₹417.74 |
| ADDRESSABLE | NEVER_ORDERED_LOW_INTENT | 941 | 6.24% | ₹3,87,585 | 6.08% | −0.16pp | ₹411.89 |
| ADDRESSABLE | CUSTOMER_REFUSED_CHANGED_MIND | 3,608 | 23.92% | ₹15,07,222 | 23.63% | −0.29pp | ₹417.74 |
| STRUCTURAL | COURIER_OPERATIONAL_FAILURE | 1,584 | 10.50% | ₹6,49,992 | 10.19% | −0.31pp | ₹410.35 |
| ADDRESSABLE | CUSTOMER_UNREACHABLE_NO_ANSWER | 2,506 | 16.61% | ₹10,31,444 | 16.17% | −0.45pp | ₹411.59 |
| | **ADDRESSABLE total** | **9,170** | **60.79%** | ₹39,19,487 | **61.44%** | **+0.65pp** | ₹427.42 |
| | **STRUCTURAL total** | **5,914** | **39.21%** | ₹24,59,883 | **38.56%** | −0.65pp | ₹415.94 |

#### Which reasons drive the divergence

**One reason drives all of it.** `INSUFFICIENT_CASH_AT_DELIVERY` is the only line
with a materially positive cost-minus-count gap (+1.53pp). Every one of the other
nine sits between −0.45pp and +0.01pp — statistical texture, not signal. Remove
that single reason and the two splits collapse onto each other.

The nine near-zero rows are the more informative half of the table: they say the
avoidability taxonomy is **almost cost-neutral**. Reason class is essentially
uncorrelated with cost per RTO, with exactly one exception.

#### Correcting two premises in the review

**(a) The cost split came in *above* the count split, not below.** ADDRESSABLE is
61.44% of cost against 60.79% of count — cost share exceeds count share by
+0.65pp. There is no below-the-count-split effect to explain. The 61.44%-versus-65%
comparison is against Phase 1's *assumption*, which is a separate quantity from
the count split and should not be conflated with it.

**(b) `INSUFFICIENT_CASH_AT_DELIVERY` is not the largest single line.** It is the
costliest *per order* at ₹486.87, but fifth by volume at 1,531 orders (10.15%).
The largest lines are `CUSTOMER_REFUSED_CHANGED_MIND` (3,608) and
`CUSTOMER_UNREACHABLE_NO_ANSWER` (2,506). Being expensive per unit and being large
in aggregate are different things, and here they belong to different reasons.

#### Why that one reason is expensive — the mechanism

Two effects compound, and both are structural rather than incidental:

1. **It is COD-exclusive by construction.** 1,531 COD, **0 prepaid** — a prepaid
   order cannot fail for want of cash at the door. Test DQ-11 enforces this. So
   the reason inherits the COD cost base, which is already the dearer of the two
   (₹423.54 versus ₹418.81 per RTO) because of COD handling and failed-attempt
   charges.
2. **It concentrates in expensive orders.** Mean order value on this reason is
   **₹1,164.66**, against ~₹920 across the order book — a 27% premium. This is a
   mechanism, not an artefact: the larger the cash ask at the door, the more
   likely the customer cannot meet it. And because shrink and working-capital
   cost scale with goods value, a dearer order is a dearer failure.

**The product reading is the useful part.** The single most cost-efficient RTO to
prevent is a high-value COD order failing for want of cash — it is addressable,
it is the most expensive failure mode in the book, and the intervention that
addresses it (partial prepayment on high-value COD) is exactly Phase 1's
Intervention D. This decomposition is what promotes that intervention from
plausible to targeted.

#### Logged as a fourth missed prior

| | Assumed | Measured | |
|---|---:|---:|---|
| **Addressable share of RTO cost** (Phase 1 §7.2) | 65% | **61.44%** | **below** |

**Mechanism:** Phase 1 assumed the split without a reason taxonomy to measure it
against — §7.2 carries it as `[A]` and §7.4 lists "35% unavoidable" as an
assumption Phase 3 must test by decomposing `return_reason`. This is that test.
The assumption was close but optimistic, and quoting it would have overstated the
addressable pool by **₹5.97 Cr** (₹109.06 Cr against the measured ₹103.09 Cr).

§7.4 set the failure threshold at "unavoidable > 50%, and the project may not
clear the funding bar." At 38.56% structural we clear it with room. **The prior
missed; the conclusion it supported holds.**

### B.5 Which orders are already underwater

p\* = **0.2576**, derived from the realised cost distribution
(`_truth.json → economics_targets.breakeven_rto_probability_derived`), not Phase
1's nominal 25.7% exemplar.

| Geo tier | Method | Resolved | RTO rate | Gap to p\* | Verdict | Mean CM/order |
|---|---|---:|---:|---:|---|---:|
| TIER3 | COD | 16,372 | **36.92%** | **+11.16pp** | **above p\*** | **−₹67.87** |
| TIER2 | COD | 17,766 | 23.56% | −2.20pp | below | +₹8.40 |
| TIER1 | COD | 13,154 | 14.82% | −10.94pp | below | +₹57.95 |
| TIER3 | PREPAID | 4,608 | 14.37% | −11.39pp | below | +₹27.34 |
| METRO | COD | 8,924 | 10.54% | −15.22pp | below | +₹87.44 |
| TIER2 | PREPAID | 7,499 | 7.59% | −18.17pp | below | +₹76.84 |
| TIER1 | PREPAID | 9,921 | 3.91% | −21.85pp | below | +₹107.20 |
| METRO | PREPAID | 13,006 | 2.65% | −23.11pp | below | +₹123.09 |

**Exactly one segment is value-destroying: Tier-3 COD.** 16,372 resolved orders
— 17.9% of the book — at a 36.92% RTO rate and **−₹67.87 mean contribution
margin per order.** Every order in that segment loses money on average.

Two things make this table more than a segment cut.

**It validates p\* as a threshold.** The mean-CM column was computed
independently of p\*, and it crosses zero exactly where the RTO rate crosses
p\*. The one segment above the line has negative margin; every segment below it
is positive; and the ordering is monotone. An economically derived threshold that
predicts realised margin is doing real work.

**Tier-2 COD is the interesting case, not Tier-3.** At 23.56% RTO and +₹8.40 per
order it sits 2.2pp below break-even — profitable, but barely. 17,766 orders are
one bad quarter of courier performance away from being underwater. Tier-3 COD is
an obvious candidate for restriction; Tier-2 COD is where a *risk-based* policy
earns its keep, because a blanket rule would either destroy 17,766 marginally
profitable orders or leave the whole segment untouched.

That is the argument for Phase 4's risk model, and it is now an empirical
argument rather than an assertion.

### B.6 The COD order is worth less than it looks

At the average, a COD order and a prepaid order look comparable at delivery —
₹107 versus ₹112 of contribution margin. After RTO:

| | COD | Prepaid | Blended |
|---|---:|---:|---:|
| RTO rate | 23.34% | 5.61% | 16.53% |
| CM per **delivered** order | ₹109.93 | ~₹112 | ₹114.27 |
| **Mean CM per resolved order** | **₹10.33** | **₹96.09** | ₹43.26 |

**A prepaid order is worth 9.3× a COD order once failure is priced in.** At
delivery the two are nearly indistinguishable — ₹110 against ₹112. It is
entirely the 17.7pp RTO gap that opens the difference.

Phase 1 §7.3 predicted this ratio would be the robust finding while the ₹165 Cr
headline stayed sensitive to assumptions, and named "₹7 versus ₹95" as the
figures. Measured: **₹10.33 versus ₹96.09.** The prepaid side lands almost
exactly; the COD side is 48% higher than predicted, but the *shape* of the claim
survives intact — the headline moves with annual order volume, and this ratio is
measured directly and moves with nothing.

The ₹10.33 is the number to carry into Phase 4. **The average COD order is worth
about a tenth of a prepaid one, and the Tier-3 COD segment is worth less than
nothing.**

---

## C. H1 — how much of the COD–RTO gap is causation?

### C.1 The four estimates

Planted truth: **AME 9.99pp**. Naive gap: **17.73pp**. The difference —
**7.74pp** — is selection.

| | Estimate | 95% CI | Controls | Recovers | GT-03 |
|---|---:|---|---|---:|---|
| **1. Raw crosstab** | **17.73pp** | [17.31, 18.16] | none | 0.0% | — |
| **2. Stratified** (tenure × geo, ATT) | **14.59pp** | — | 16 cells | 40.6% | **PASS** |
| **3. Logistic regression** | **10.67pp** | [10.07, 11.28] | 41 confounders | **91.2%** | **FAIL** |
| **4. Propensity matched** | **12.24pp** | [11.81, 12.68] | matched on P(COD\|X) | 70.9% | **FAIL** |
| **Truth (unobservable)** | **9.99pp** | — | — | 100% | — |

Every estimate is correctly **ordered**: `AME < adjusted < naive` holds in all
four cases. Each successive method moves toward the truth. Nothing here is
inverted or pathological.

The raw crosstab reproduces `_truth.json`'s naive gap to six decimal places
(17.7326 against 17.7326), which is the check that the analysis population is
the same one the truth file measured.

### C.2 How much of the 7.74pp selection component each method recovered

| Method | Estimate | Selection removed | Selection remaining | Residual above truth |
|---|---:|---:|---:|---:|
| Raw crosstab | 17.73pp | 0.00pp (0.0%) | 7.74pp (100%) | +7.74pp |
| Stratified (ATT) | 14.59pp | 3.14pp (40.6%) | 4.60pp (59.4%) | +4.60pp |
| Propensity matched | 12.24pp | 5.49pp (70.9%) | 2.25pp (29.1%) | +2.25pp |
| Logistic regression | 10.67pp | 7.06pp (91.2%) | 0.68pp (8.8%) | **+0.68pp** |

**The logistic regression lands 0.68pp above the truth.** It removes 91.2% of the
confounding.

### C.3 GT-03 fails on the full confounder set, and that is the finding

GT-03's rule (decision A6):

```
PASS if  AME < adjusted < naive
AND      (adjusted − AME) / (naive − AME) >= 0.35
```

The ordering condition passes for all four methods. The **magnitude** condition
fails for the regression (0.088) and for the match (0.291). Both recover more of
the selection component than the test was designed to permit.

**This was not tuned, and it will not be.** The obvious way to make GT-03 pass is
to drop `pit_cod_share` from the confounder set. That would be selecting a model
specification to hit a validation target — the exact move CLAUDE.md rule 3 and
decision A7 forbid elsewhere in this project. The full-confounder estimate is
reported as primary and the test is recorded as failing.

#### What actually drives the over-recovery

Adding confounders one block at a time isolates it precisely:

| Confounder set | AME | Recovers | |
|---|---:|---:|---|
| Geo tier only | 14.36pp | 43.5% | ok |
| + order, product, seller, delivery-promise features | 13.85pp | 50.1% | ok |
| **+ tenure and order counts** | **12.02pp** | **73.8%** | **over** |
| **+ `pit_cod_share`** | **10.77pp** | **90.0%** | **over** |
| + `pit_rto_rate_shrunk` (full set) | 10.67pp | 91.2% | over |

Two blocks do nearly all the work, and both are **customer behavioural history**.
Everything about the *order* — value, category, seller, product rating, delivery
promise, address quality, geography — together recovers only 50%. Adding what the
customer has done before takes it to 90%.

The single largest jump is `pit_cod_share`: the customer's own prior COD share
moves recovery from 73.8% to 90.0% on its own.

#### The mechanism, and why it is structural rather than accidental

Decision **A11** generates pre-window history **from the latents**. A customer's
prior COD share and prior RTO rate are downstream of `latent_trust`,
`latent_liquidity` and `latent_intent` by construction.

So `pit_cod_share` is not merely correlated with the unobservable confounder —
it is a **direct observable consequence of it**. Adjusting for a good proxy of an
unobserved confounder removes most of the bias that confounder creates. The
better the proxy, the more comes out. Here the proxy is unusually good, because
the generator built history from the latents with limited noise in between.

**No leakage occurred.** Every feature is on the safe whitelist; LK-01 passes;
the `analyst` role that produced this analysis is denied on schema `truth` with
SQLSTATE 42501, asserted inside the cross-check. Nothing saw the answer. The
recovery is a property of the data-generating process, not a hole in the
firewall.

#### Two readings, and which one I hold

**Reading 1 — GT-03's band is calibrated to a weaker proxy than this dataset
has.** The 0.35 floor was set by decision A6 without a fitted model to measure
against; it encodes an expectation about how recoverable the confounding would
be. That expectation was too pessimistic for a DGP where history is generated
from latents. On this reading the test needs re-anchoring, exactly as A6
re-anchored it once before when γ₀ moved.

**Reading 2 — the residual is small in absolute terms and the test is doing its
job.** 0.68pp of unrecoverable bias on a 9.99pp effect is a 6.8% overstatement.
GT-03 exists to prove the truth is *not fully recoverable*, and it is not: the
estimate does not reach 9.99pp, and it never will, because no amount of
observable history reconstructs `latent_intent` exactly.

**I hold Reading 1 and flag it for a ruling.** The ordering condition — the part
that actually detects leakage — passes cleanly. The magnitude floor is a
judgement about proxy strength that this dataset falsifies. **But re-anchoring a
test after seeing the result it failed is precisely the move that needs someone
other than the person who ran it to approve.** Recorded as an open item, not
resolved.

### C.4 Why the propensity match recovers *less* than the regression

12.24pp against 10.67pp. This is not a defect; the two answer slightly different
questions.

| | |
|---|---:|
| Matched pairs | 55,695 |
| COD orders with no match inside the caliper | 521 (0.93%) |
| Propensity model AUC | 0.8354 |

**The 0.8354 is itself a finding.** A model using only observable pre-checkout
features separates COD from prepaid customers well. Payment method is close to
predictable from who the customer is and what they are buying — which is the
selection problem stated as a number.

Matching only compares COD orders to prepaid orders that plausibly *could have
been* COD, and drops the rest instead of extrapolating. It does not impose the
regression's functional form, so it cannot use the linear structure to project
into regions of the covariate space where the data is thin. It recovers less
because it assumes less.

Common support is good — 99.07% of COD orders found a twin — so the estimand
difference is small and the two bracket the truth from the same side.

### C.5 Where the estimate lands depends on a weighting choice worth 3.7pp

The stratified estimate is not one number:

| Standardised to | Estimand | Estimate |
|---|---|---:|
| **COD distribution** | **ATT — effect on the treated** | **14.59pp** |
| Pooled | ATE | 13.16pp |
| Prepaid distribution | ATU | 10.86pp |

**ATT is the right one here**, because the truth file's AME is computed over the
shipped COD population (decision A6) and the policy question is about the COD
orders we currently take.

This is worth flagging because **the wrong choice looks better.** The
prepaid-weighted figure, 10.86pp, sits far closer to the true 9.99pp — and would
have been reported as an impressively accurate adjustment. It is answering the
mirror-image question (what would happen if prepaid customers went COD) and its
apparent accuracy is a coincidence. I made this error in the first draft of the
analysis and caught it by asking which population the truth file's AME was
averaged over.

### C.6 No method can fully recover the truth, and that is the honest ceiling

`latent_trust`, `latent_liquidity` and `latent_intent` are held in a PostgreSQL
schema the analyst role has no privileges on. They drive both the choice to pay
cash and the tendency not to take delivery. **They are unobservable by
construction, and no query in this library can reach them.**

That is not a limitation of this dataset. It is a faithful model of the real
situation: no e-commerce warehouse contains a column for a customer's
willingness to abandon a parcel at the door. Whatever an analyst controls for,
some of the COD–RTO gap will always be the customer rather than the payment
method, and **no observational method can say how much.**

The best estimate here still overstates the true effect by **6.8%**, and that is
with a favourable proxy structure and 41 confounders. An analyst who did not have
the truth file would have no way to know the residual was 0.68pp rather than
5pp — the data contains no signal that would tell them.

**The honest ceiling on any real-world causal claim about payment method is
therefore: "a strong association, robust to every observed confounder, whose
causal share cannot be identified from observational data."** Phase 1's H1 entry
predicted exactly this limitation, and it survives contact with the data.

### C.7 What this means for the product

**H1's pre-registered prior is met, for the raw form and the adjusted form.**

| | Prior | Measured | |
|---|---|---:|---|
| H1 raw | ≥15pp | **17.73pp** | **PRIOR MET** |
| H1 adjusted | shrinks 30–50%, survives | **shrinks 39.8%** (17.73 → 10.67), survives | **PRIOR MET** |

The adjusted gap shrank by 39.8% — inside the predicted 30–50% band — and did not
collapse. Phase 1's implication table says: *"If adjusted gap remains large →
payment method itself is a lever."* It remains large.

Three decisions follow, and the third is the one that changes the roadmap.

1. **Payment method is a real lever, but it is roughly half the size the raw
   number implies.** Any COD fee or prepaid incentive should be sized against
   ~10.7pp, not 17.7pp. Pricing against the raw gap would over-invest by ~66%.
2. **Customer risk is the larger lever.** The confounder ladder shows behavioural
   history explains more of the RTO gap than payment method does. That is the
   empirical case for Phase 4's risk model, and it now rests on a measurement
   rather than an assertion.
3. **The residual cannot be closed observationally.** Phase 1 §3 names three
   problem-tree branches — customer trust, checkout friction, behavioural
   collateral — with no observational variation to exploit. C is the proof of
   that claim rather than the assertion of it. **The partial-payment experiment
   is the only instrument that would identify the causal share**, and that is
   Phase 6.

---

## Corrections made to this document

Logged rather than silently fixed, because a figure caught before review is
worth more on the record than one that was right first time.

| Section | Was | Is | How it was caught |
|---|---|---|---|
| B.6 | CM per resolved order ₹27.56 COD / ₹99.26 prepaid, ratio 3.6x | **₹10.33 / ₹96.09, ratio 9.3x** | Asserted in a draft before being queried. Caught by computing it against the database rather than carrying the draft figure forward |
| C.5 | Stratified estimate standardised to the **prepaid** distribution (10.86pp) | **COD-weighted ATT, 14.59pp** | The prepaid weighting sat far closer to the true 9.99pp and looked like an accurate adjustment. Caught by asking which population `_truth.json`'s AME is averaged over — the shipped COD orders, making the estimand an effect on the treated |

The corrected ratio is the stronger finding — 9.3x, against Phase 1 §7.3's
predicted "₹7 versus ₹95" — so the error had been working *against* the argument
it appeared in. That is the usual shape: an unverified number is not biased
toward your case, it is just wrong.

---

## What is not settled here

Section B measures *association* between payment method and RTO. It cannot
separate causation from selection, and nothing above should be read as a claim
that switching a customer from COD to prepaid would recover ₹146 Cr. Section C
addresses exactly that, and the honest answer is already known to be partial.

Three problem-tree branches — customer trust, checkout friction, behavioural
collateral — have **no observational variation to exploit** and cannot be settled
by any query in this library (Phase 1 §3). Naming them is the bridge to Phase 6,
not a gap in Phase 3.

---

## Artefacts

| Path | Contains |
|---|---|
| `sql/10_funnel.sql` | Q1–Q4 — funnel, conversions, abandonment, payment reliability |
| `sql/11_economics.sql` | Q5–Q9 — RTO split, annualisation, avoidability, waterfall, p\* segments |
| `src/analysis/funnel.py` | The same metrics in pandas |
| `scripts/05_crosscheck.py` | Asserts SQL == Python on 48 metrics; asserts `analyst` denied on `truth` |
| `notebooks/03_exploratory_analysis.ipynb` | Scale, distributions, the planted truth |
| `notebooks/04_checkout_funnel.ipynb` | Section A |
| `sql/12_hypotheses.sql` | Q11–Q13 — raw crosstab, stratified cells, the three standardisations |
| `src/analysis/h1_decomposition.py` | Section C — all four estimates plus the GT-03 rule |
| `notebooks/05_rto_analysis.ipynb` | Sections B and C |

Q1–Q13 of the Phase 1 §15 "SQL library (13 queries)" are written. The blueprint
names the count but never enumerates the thirteen, so the split across files is a
Phase 3 decision, recorded here rather than inferred. Section D adds no new
queries — H2–H6 reuse Q11–Q13's cell machinery with different cuts.

**Open item for a ruling:** GT-03's magnitude floor (§C.3). The ordering condition
passes; the 0.35 floor fails at 0.088 because customer behavioural history is a
stronger proxy for the latents than decision A6 anticipated. Re-anchoring a test
after seeing it fail needs approval from someone other than whoever ran it.
