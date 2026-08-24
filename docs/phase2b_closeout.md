# Phase 2B — Closeout

**Verdict: 🟡 CONDITIONAL.** 65 tests · 56 pass · **0 HARD failures · 0 SOFT
failures** · 9 skips, all environment-blocked or Phase-5-deferred.

Signed off as the Phase 2B exit condition. Full scale, seed `20260115`,
105,605 orders across 155,000 sessions and a 90-day window.

**`data/truth/_truth.json` is the single quotable source for every derived
figure below.** The spec's prose figures belong to an earlier parameterisation
and no longer describe this dataset (limitation L8). Do not quote the spec.

---

## 1. Calibrated levels

Seven levels. **Every one is a LEVEL — no slope was ever solved for.**

| Level | Solved | Realised | Status |
|---|---:|---:|---|
| `product_price_scalar` | **1.039062** | mean GMV ₹1,001.20 | solved (A36) |
| `conversion_model.intercept_solved` (α₀) | **+0.281250** | conversion 0.6813 | solved |
| `cod_model.intercept_solved` (β₀) | **+0.875000** | COD share 0.6233 | solved |
| `rto_model.intercept_solved` (γ₀) | **−4.687500** | blended RTO 0.1653 | solved |
| `pre_window_cod_model.intercept_solved` (π_cod0) | **+0.515625** | 0.6166 | solved (A11) |
| `pre_window_rto_model.intercept_solved` (π_rto0) | **−3.375000** | 0.1671 | solved (A11) |
| `economics.support_ndr_base_solved` | **9.0000** | NDR mean ₹18.00 | solved (A38) |
| `post_dispatch_shock.noise_sd` | **3.3125** | AUC 0.7717 | **FROZEN** (A37→A38) |

Drift on the final alternating pass: **0.00e+00 on every solved level.**

`noise_sd` was solved once against the GT-05 AUC ceiling, then frozen and placed
under CAL-09. It must not move: CAL-11's selection share sits ~0.014 from its
ceiling and rises monotonically with noise, so this is the number that breaks the
project's central gate first.

### Coefficients: the precise claim

**Zero existing slopes moved. One new coefficient was added, under a flagged spec
gap.** `post_dispatch_shock.seller_sla_dispatch_weight = 0.35` did not exist in
the specification. A33 condition (a) required `attempt_delay_days` to derive from
*both* courier reliability and seller SLA breach; the draft satisfied only the
first, leaving δ₃ multiplying a flag with no relationship to the
`seller_sla_breach_rate` it was meant to represent. The coefficient closes that
gap and is frozen under CAL-09 like every other slope. See **A33-amendment**.

---

## 2. As-built derived figures

| Quantity | As built | Spec prose (superseded) |
|---|---:|---:|
| Naive COD−prepaid gap | **17.73pp** | 19.9pp |
| **AME — the canonical COD effect** (A6) | **9.99pp** | ~13.4pp |
| Selection share of the naive gap | **0.436** | 0.327 |
| **Naive ÷ truth** | **1.77×** | ~1.5× |
| AUC ceiling, `truth.p_rto_precheckout` | **0.7717** | — |
| LK-03 tripwire margin | **+0.0783** | — |
| **p\*** break-even RTO probability, **derived** | **0.2576** | 0.257 |
| Annualised RTO exposure | **₹167.8 Cr** | ₹164.1 Cr |
| Derived annualisation factor | **227.26** | 240 (removed) |
| Mean GMV / mean order_value per order | **₹1,001.20 / ₹919.74** | ₹1,000 / ₹920 |
| Addressable share of RTO cost | **0.6144** | 0.65 |

**The headline finding, in the form it should be written up:** a naive
COD-vs-prepaid crosstab overstates the causal effect of COD by **1.77×** — 17.73pp
against a true 9.99pp. The gap is real, and about **44%** of it is selection.

**p\* is DERIVED** (A38) and Phase 3 must tier against **0.2576**, not the nominal
25.7%. The threshold has to be economically true, not inherited.

**EC-07 at ₹167.8 Cr is within 1.7% of the Phase 1 ₹165 Cr headline**, and it got
there by fixing a censoring bug in the measurement — not by moving a parameter.

---

## 3. Pre-registered priors: three miss, in two directions

Blueprint §4: *"nothing signals genuine analytical work more than a documented
wrong prior."* There are now three, each with a mechanism attached. All are
recorded as **PRIOR vs OBSERVED** in `_truth.json`, never collapsed to the
observed value, so Phase 5 can show the miss.

| Hypothesis | Prior | Observed | Verdict |
|---|---|---:|---|
| **H2** new-customer COD lift | 12–18pp | **22.46pp** | **ABOVE** |
| **H3** prior-RTO lift multiple | 2.0–2.5× | **1.693×** | **BELOW** |
| **H11** COD from payment friction | 8–15% | **5.90%** | **BELOW** |
| H12 achievable AUC ceiling | 0.74–0.79 | 0.7717 | in prior* |

\* H12 is **not** independent confirmation: `noise_sd` was calibrated against this
target and then frozen. It is a solved value. Do not present it as a prediction
the data happened to meet.

**H2 (A43).** Spec §7.2 priced `is_new_customer = +0.70` in isolation and ignored
that a new customer also *escapes* two tenure penalties every established customer
pays — `log1p_orders_delivered` (−0.238) and `log1p_prepaid_success` (−0.260). Net
differential +1.046 × p(1−p) = 0.235 predicts +24.6pp against +22.46pp observed.

**H3 (A40).** Decision A37 raised post-dispatch noise from 0.85 to 3.3125 to bring
the AUC ceiling into GT-05's band. That dilutes every pre-checkout signal,
`pit_rto_rate_shrunk` (+2.80) included. H3 is the price of a defensible accuracy
ceiling and a working leakage tripwire — a trade worth making, and one that should
be stated rather than absorbed.

**H11 (A35).** The payment parameters come from plausible external PG-failure
ranges, not from the prior. Spec §10.3 predicted this. The honest write-up:
*payment-friction-driven COD is real but smaller than hypothesised; still the
first thing to ship because it is free, but it does not reframe the project.*
The sharper finding sits beside it — switch-COD orders RTO **15.7pp less** than
intent-COD, so fixing payment reliability recovers *the better half* of COD.

---

## 4. Specification defects found and resolved

| # | Defect | Resolution |
|---|---|---|
| **A34** | §3.10 and §12.1 both label `order_value` as "the ₹1,000 AOV quantity", contradicting Phase 1 §6.5 (GMV ₹1,000 − 8% = net revenue ₹920) and the ₹2,400 Cr GMV anchor | EC-01 tests mean **GMV**; new EC-01b reports mean `order_value` ≈ ₹920 |
| **A37** | `noise_sd = 0.85` is inconsistent with the spec's own AUC target: at 0.85, three independent quantities miss together (AUC 0.87, prepaid RTO 2.6%, naive gap 22.5pp) and all three land at ~2.6–3.3 | Calibrated against its declared purpose to **3.3125**, then frozen |
| **A38** | §12.2 states cost parameters twice — as formulas and as blended means — and they disagree. Shrink: rates give 9.72%, text says 8.0%. NDR: `18 + 6×(attempts−1)` can never return the registry's ₹18 | Shrink formula wins (text was an arithmetic error); NDR parameter wins (Phase 1 §6.4 is senior). No target restated |

Three more were structural rather than arithmetic: **A7** (three HARD RTO targets
against one degree of freedom), **A31** (VOL-01/VOL-02/CAL-06 jointly infeasible),
**A41** (annualising without excluding censored orders understates by ~15%).

---

## 5. What Phase 3 inherits

**Quote `_truth.json`. Never the spec prose, and never this document's numbers if
they disagree with the file.**

- **p\* = 0.2576**, derived. Tier against this.
- The **AUC ceiling is 0.7717**. A risk model that beats it has leaked. LK-03's
  guard sits at 0.85, a margin of +0.078.
- **`vw_risk_model_input` is the only permitted training source.** The
  `hist_*_final` / `clv_estimate` / `analytics_segment` columns on `dim_customer`
  are end-of-window aggregates that *include the current order*. They look like
  innocent customer attributes. They are Stage-5 leakage.
- **Two rule baselines, not one.** `pit_risk_tier_rule_based` is M1 (pre-selection,
  no payment method); `order_risk_tier_rule_based` is M2 and is hard-blocked from
  M1. Applying an M1 score to the M2 threshold is a category error.
- **~5% of RTO orders (4,956) would have lost money even if delivered** (A42, L10).
  The intervention set needs a *"don't take this order"* tier, not only payment and
  address levers. `counterfactual_cm_if_delivered` is stored per order.

### What Phase 3 must NOT assume

- **Not** that 13.4pp / 19.9pp / 33% describe this dataset. They do not (L8).
- **Not** that the annualisation factor is 240. It is **derived**, currently 227.26,
  and moves with the order count.
- **Not** that censored orders are zero-cost. They are unresolved, and annualising
  over them understates the opportunity by ~15% (L9).
- **Not** that `pit_avg_order_value` is dense. It is NULL until a customer's first
  in-window order (L2).
- **Not** that address quality varies by geography. It is drawn independently, by
  deliberate choice, so the address intervention stays cleanly attributable (L1).
- **Not** that the nine skipped tests passed. Three need PostgreSQL; six need
  fitted models and are Phase 5's job.

---

## 6. Outstanding before Phase 3 opens

| Item | Blocker |
|---|---|
| LK-01, LK-05, DQ-01 | Need a live PostgreSQL. DDL parses 34/34 but was never applied |
| Modules 22–23 (load, report render) | Same |
| BR-09, GT-01/03/04/06/07 | Phase 5 — need fitted models |
| `docs/data_generating_process.md` | Not written; A3, A25 and A26 owe it documentation |

**146 unit tests passing.** Working tree clean at tag `phase2b-complete`.
