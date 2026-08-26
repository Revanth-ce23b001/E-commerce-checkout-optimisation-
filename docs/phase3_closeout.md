# Phase 3 — Closeout

**Status: CLOSED.** Sections A, B, C and D of `reports/phase3_findings.md` are
complete, cross-checked, and signed off as the Phase 3 exit condition.

Validation stands at **67 tests · 61 pass · 0 HARD fail · 0 SOFT fail · 6 skip**,
every skip needing a fitted model. Two tests were added during Phase 3 — **DQ-15**
and **DQ-16**, both HARD, both from decision **A46**.

**`data/truth/_truth.json` remains the single quotable source.** Where this
document, the findings report and the spec prose disagree, the file wins.

---

## 1. What Phase 3 produced

| Output | Where |
|---|---|
| The funnel, the leak, and the CM/CSS baseline | §A |
| RTO economics, the waterfall, the measured addressable share | §B |
| H1 — four estimates, the ATT/ATE correction, the identification ceiling | §C |
| H2–H6, H11 against pre-registered priors | §D |
| The consolidated prior scoreboard | §0 |
| Q1–Q13 of Phase 1 §15's SQL library | `sql/analysis/10–12` |
| 48-metric SQL-vs-Python cross-check | `scripts/05_crosscheck.py` |

Three things changed in the *dataset* during Phase 3, all from A46 and none of
them a slope, a level or a business assumption:

- `attempt_delay_days` and `attempt_number = 1` are now emitted on `DELIVERED`
  events. `fct_order` is byte-identical; CAL-11, the AUC ceiling and every EC
  figure are unchanged.
- **DQ-15** asserts `attempt_delay_days` is present on every shipped,
  non-cancelled order **in both `rto_flag` arms separately**. The assertion is not
  "is this column ever populated" — that passed throughout the defect's life — it
  is "is its population independent of the outcome".
- **DQ-16** generalises it: every order-linked column is swept across five outcome
  partitions, and anything flagged that is not declared in
  `dq16_expected_outcome_conditional` fails the suite.

---

## 2. What Phase 4 inherits

- **The AUC ceiling is 0.7717.** A model that beats it has leaked. LK-03's guard
  sits at 0.85.
- **p\* = 0.2576**, derived. Tier against this, and remember it belongs to **M2** —
  it is the expected value of a *COD* order.
- **`vw_risk_model_input` is the only permitted training source.** The
  `hist_*_final`, `clv_estimate` and `analytics_segment` columns on `dim_customer`
  are end-of-window aggregates that include the current order.
- **Two rule baselines.** `pit_risk_tier_rule_based` is M1 (pre-selection, no
  payment method); `order_risk_tier_rule_based` is M2 and is hard-blocked from M1.
  Applying an M1 score to the M2 threshold is a category error.
- **The measured effect sizes M1 has to work with**, all from §0: COD's true AME
  on RTO is **9.99pp**, not the 17.73pp the crosstab shows; behavioural history
  explains more of the RTO gap than payment method does (§C.2); prior-RTO lift is
  **1.693× aggregate but 2.39× at three or more prior RTOs**.
- **~5% of RTO orders (4,956) would have lost money even if delivered** (A42, L10).
  `counterfactual_cm_if_delivered` is stored per order.
- **The DQ-16 allowlist is the contract for any new column.** A model-scoring
  column added in Phase 4 that is populated on one outcome arm and not the other
  will turn the suite red. That is the intended behaviour, not an obstacle.

## 3. What Phase 4 must NOT assume

### 3.1 The AUC gate clears, but not comfortably

The ceiling is **0.7717**, so a fitted M1 should land around **0.74–0.77**. Phase
1 §9.4 gates full risk-based pricing at **0.72**. That clears — with materially
less headroom than the blueprint assumed.

**The reason is traceable and is a cost the project chose to pay.** A37 raised
`post_dispatch_shock.noise_sd` from 0.85 to 3.3125 to bring the ceiling into
GT-05's band. That noise dilutes every pre-checkout signal, and
**`pit_rto_rate_shrunk` is now weaker than its +2.80 design intent** — H3's miss
(1.693× against a 2.0–2.5× prior, §D.2) is the same fact measured from the other
end. Do not plan the feature set as though a +2.80 coefficient is available to
recover.

**If fitted M1 comes in below 0.72, Phase 1's own pre-commitment applies: coarse
tiers only, reported honestly. Do not tune anything to clear the gate.**

### 3.2 THE STAGE-4 BAR — the single most likely leakage vector in Phase 4

`attempt_delay_days` and `delivery_delay_days` are **both Stage-4**.
**Neither may enter M1 or M2.**

Of every way Phase 4 could leak, this is the most probable, because it is the
only one that a *correct* Phase 3 finding actively argues for.

H6 makes delay look like the most attractive feature in the warehouse — it is the
single largest explanatory factor for RTO in the dataset, at **15.4×** the
promise. **That is the reason this bar will be tempting, not a reason it is
permitted.**

It is also determined **after dispatch**. A model containing it does not predict
RTO; it observes one. That is Phase 1 §9.2's `actual_delivery_days` exclusion in a
new costume, and the fact that A46 made the column *available on both outcome
arms* changes its honesty, not its stage. **A46 fixed a projection defect. It did
not promote a Stage-4 fact to Stage-2.**

The only checkout-time proxy is `estimated_delivery_days`, which is Stage-2 and
legitimately usable — and which carries **6.5%** of the explained deviance, mostly
as a restatement of the delivery address.

### 3.3 The rest

- **Not** that the naive COD–RTO gap is a causal estimate. It is 1.77× the truth.
- **Not** that the regression's 10.67pp is "the right answer" — it lands 0.68pp
  above a truth it structurally cannot reach (§C.3, §C.6).
- **Not** that GT-03 passes — *as of Phase 3*. Its ordering condition passed; the
  closure clause failed, and the ruling on re-anchoring it was left open.
  **Superseded:** rulings A50 and A51 measured the mechanism, raised the ceiling
  to 75% on it, and recorded **L15**. GT-03 now passes.
- **Not** that 13.4pp / 19.9pp / 33% describe this dataset (L8).
- **Not** that the annualisation factor is 240. It is derived, currently 227.26.
- **Not** that censored orders are zero-cost (L9), or that `pit_avg_order_value`
  is dense (L2).
- **Not** that 65% of RTO cost is addressable. Measured, it is **61.44%**.

---

## 4. Carried into Phase 5 — one explicit exclusion

The intervention library inherits a **removal**, recorded here so that nobody
re-proposes it in six weeks without meeting the evidence.

> ### ⛔ EXCLUDED: "shorten the delivery promise"
>
> **Do not build a checkout intervention that shortens, tightens or re-frames the
> delivery promise in order to reduce RTO.**
>
> **Evidence (§D.5, H6, n = 91,250).** In one model with geography controlled,
> realised delay contributes **1,047.0** of deviance and the promise **68.0** —
> realised delay explains **15.4×** what the promise explains.
>
> **Mechanism, which is the load-bearing half.** The two *coefficients* differ by
> only 1.7× (+0.1267 against +0.0754). The promise is **83% determined by the
> destination's base transit time** and takes ten integer values, so it has almost
> no independent variation left to act through. A lever is worth its coefficient
> times the range you can actually move it over, and the promise fails on the
> second factor. Coefficient and deviance answer different questions; only the
> second one tells you whether to build the feature.
>
> **Why the naive version of this analysis would have shipped it.** Promise alone
> gives +0.3191 (+4.88pp per promised day) and looks overwhelming; over-controlled
> on base transit time it runs *backwards* at −0.0461. The honest specification is
> +0.1273 (+1.83pp), and most of even that is geography.
>
> **Where the effect does belong.** As a logistics line.
> `DELIVERY_ATTEMPTED_OUTSIDE_WINDOW` and `COURIER_OPERATIONAL_FAILURE` are already
> classified STRUCTURAL in §B.4 — 38.56% of RTO cost. H6 is the evidence that
> classification was right.
>
> **What would reopen this.** Evidence that a promise can be moved *independently
> of the logistics behind it* without becoming inaccurate — a source of promise
> variation that is not geography. This dataset contains none, and §D.7 records
> that as a question the warehouse cannot settle.

**A negative result is a roadmap decision.** This one removes a plausible,
cheap-sounding, demo-friendly feature on measurement rather than on opinion, and
it bounds what Phase 5 can honestly promise: the largest single explanatory factor
in RTO is not one the checkout team controls.

---

## 5. Methodology carried forward

Phase 3 added a third pattern to decision **A44**'s write-up:

> **The unit of declaration must match the unit of the defect.** A46's fix made
> `attempt_delay_days` legitimately absent on censored orders while requiring it
> present on delivered ones — so a column-level allowlist would have re-excused the
> very defect it was written to catch. The sweep had to declare
> **(column, partition)** pairs.

And the argument for generic detection over targeted fixes: **`attempt_number`
flagged independently on DQ-16's first sweep**, confirming a judgement call made
inside A46's fix before the sweep existed. A targeted fix is only ever as complete
as the person writing it.

## 6. Still open

| Item | Status |
|---|---|
| GT-03's closure clause — re-anchor or record as failing | **RESOLVED** by A50 (measure the mechanism) then A51 (ceiling → 75%, limitation L15) |
| BR-09, GT-01/03/04/06/07 | **RESOLVED** — all six run in Phase 4; all six pass |
| `docs/data_generating_process.md` | Still absent |
| ₹30.93 Cr recoverable | Placeholder until Phase 6 measures an ATE |
