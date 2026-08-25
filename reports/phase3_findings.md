# Phase 3 — Diagnostic findings

*Status: **A (funnel) and B (RTO economics) complete and open for review.**
C (H1 decomposition) and D (H2–H6, H11) not started.*

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
| × Intervention efficacy **[ASSUMPTION]** | ×0.30 | |
| **= Recoverable opportunity** *[assumption-dependent]* | **₹30.93 Cr** | |

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

### B.4 Addressable vs structural — the cost split differs from the count split

| Class | RTO orders | Share of count | Total cost | **Share of cost** | Avg cost |
|---|---:|---:|---:|---:|---:|
| ADDRESSABLE | 9,170 | 60.79% | ₹39,19,486.68 | **61.44%** | ₹427.42 |
| STRUCTURAL | 5,914 | 39.21% | ₹24,59,882.66 | **38.56%** | ₹415.94 |

The two splits differ by 0.65pp, because addressable failures are slightly more
expensive on average — `INSUFFICIENT_CASH_AT_DELIVERY` at ₹486.87 per RTO is the
costliest reason in the dataset. The gap is small here, but the discipline
matters: **what gets recovered is cost, not order count**, and the two are only
equal when every reason class costs the same.

Top reasons by volume:

| Reason | Class | Orders | Avg cost |
|---|---|---:|---:|
| CUSTOMER_REFUSED_CHANGED_MIND | ADDRESSABLE | 3,608 | ₹417.74 |
| CUSTOMER_UNREACHABLE_NO_ANSWER | ADDRESSABLE | 2,506 | ₹411.59 |
| PINCODE_SERVICEABILITY_FAILURE | STRUCTURAL | 1,845 | ₹417.74 |
| COURIER_OPERATIONAL_FAILURE | STRUCTURAL | 1,584 | ₹410.35 |
| **INSUFFICIENT_CASH_AT_DELIVERY** | ADDRESSABLE | 1,531 | **₹486.87** |

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

Queries Q10–Q13 of the Phase 1 §15 "SQL library (13 queries)" are unwritten;
they belong to sections C and D. The blueprint names the count but never
enumerates the thirteen, so the split across files is a Phase 3 decision and is
recorded here rather than inferred.
