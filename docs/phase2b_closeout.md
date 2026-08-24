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

## 3. Pre-registered priors: two independent misses, plus one shared consequence

Blueprint §4: *"nothing signals genuine analytical work more than a documented
wrong prior."* Three rows below sit outside their prior, but they are **not three
findings.** Counting them as three overstates the evidence.

**H2 and H11 are genuinely independent misses.** They have unrelated mechanisms
— an omitted tenure offset in the COD logit, and externally-sourced payment-failure
parameters — and neither is downstream of the other.

**H3 and the narrowed naive gap are ONE mechanism with two consequences.** Both
are the A37 noise recalibration: raising post-dispatch `noise_sd` from 0.85 to
3.3125 dilutes every pre-checkout signal, which simultaneously (a) compresses the
prior-RTO lift multiple to 1.693×, and (b) narrows the naive COD−prepaid gap to
17.73pp. Reporting them as two separate surprises would double-count a single
recalibration. State it as one root cause, two consequences.

All are recorded as **PRIOR vs OBSERVED** in `_truth.json`, never collapsed to the
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

**H3 (A40) — not an independent miss.** Decision A37 raised post-dispatch noise
from 0.85 to 3.3125 to bring the AUC ceiling into GT-05's band. That dilutes every
pre-checkout signal, `pit_rto_rate_shrunk` (+2.80) included. The narrowed naive gap
(17.73pp vs a 19.9pp prior) is the **same mechanism, not a second finding** — one
recalibration, two consequences. H3 is the price of a defensible accuracy ceiling
and a working leakage tripwire — a trade worth making, and one that should be
stated rather than absorbed, but stated **once**.

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
- **Not** that the AUC gate clears comfortably. The ceiling is **0.7717**, so a
  fitted M1 should land around **0.74–0.77**. Phase 1 §9.4 gates full risk-based
  pricing at **0.72**. That clears — but with materially less headroom than the
  blueprint assumed, because A37's noise increase left `pit_rto_rate_shrunk`
  weaker than its +2.80 design intent. **If fitted M1 comes in below 0.72, Phase
  1's own pre-commitment applies: coarse tiers only, reported honestly. Do not
  tune anything to clear the gate.**
- **Not** that the six skipped tests passed. They need fitted models and are
  Phase 5's job.

---

## 6. Database verification (tag `phase2b-verified`)

PostgreSQL 16.15 in Docker. The DDL had parsed cleanly for weeks; **applying it
to real rows for the first time found six defects that neither the 42
data-validation tests nor the 146 unit tests had caught** (decision A44). Text
that has never executed is not a constraint.

| Check | Result |
|---|---|
| 14 tables loaded | 2,022,081 rows; **every parquet count matches the server count exactly** |
| **LK-01** | PASS — all 52 `vw_risk_model_input` columns on the safe whitelist |
| **LK-05** | PASS — connected **as the `analyst` role**; both `truth` tables refused with **SQLSTATE 42501**; 0 PUBLIC grants on schema `truth`, 0 role memberships, 0 stray `truth_*` tables outside the schema; and `analytics.fct_order` **is** readable (105,605 rows) |
| **DQ-01** | PASS — manifest written, dataset regenerated from the same seed, `fct_order` content hash identical (`49b066f0…`) |
| **FK constraints** | PASS — 21 foreign keys anti-joined against the loaded rows, **0 orphans** |
| **CHECK constraints** | PASS — 102 predicates re-evaluated row by row, **0 violations** |

Two notes on method, because both checks were nearly worthless in their obvious
form:

- **LK-05 was not verified from `pg_catalog`.** A catalogue query shows what the
  DDL *intended*. Only a denied `SELECT` from a real login shows what is
  *enforced*, and the check also covers the two things catalogue inspection
  misses entirely: `PUBLIC` grants and role inheritance via `pg_auth_members`.
  The belt-and-braces read of `analytics.fct_order` is there because a role that
  can read nothing is a broken role, not a working boundary.
- **The constraints were not verified with `VALIDATE CONSTRAINT`.** A constraint
  created normally is already marked valid, so `ALTER TABLE … VALIDATE
  CONSTRAINT` returns success without reading a row. Each constraint is instead
  turned back into a query — an anti-join per foreign key, `WHERE (predicate) IS
  FALSE` per check.

**Validation now stands at 65 tests · 59 pass · 0 HARD fail · 0 SOFT fail ·
6 skip.** All six remaining skips are Phase-5-deferred (need fitted models).
Zero are environment-blocked.

`scripts/03_validate.py` reads these results from `reports/database_checks.json`
rather than being told about them, and that file carries the hash of the dataset
it ran against — a stale file reports SKIP, not a fabricated PASS.

---

## 7. Outstanding before Phase 3 opens

| Item | Blocker |
|---|---|
| BR-09, GT-01/03/04/06/07 | Phase 5 — need fitted models |
| `logit_cod_components` / `logit_rto_components` | Declared JSONB, entirely NULL. Registered gap (L12) — **needs a ruling**: populate, sample, or drop from the DDL |
| `docs/data_generating_process.md` | Not written; A3, A25 and A26 owe it documentation |

**146 unit tests passing.** Working tree clean at tag `phase2b-verified`.
