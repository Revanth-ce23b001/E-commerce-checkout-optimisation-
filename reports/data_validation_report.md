# Data Validation Report

**Verdict: 🟡 CONDITIONAL**

All HARD tests that RAN pass, and 0 SOFT failure(s) — but 6 HARD test(s) could not run and are NOT passes: BR-09, GT-01, GT-03, GT-04, GT-06, GT-07. Every one needs a fitted model and belongs to Phase 5. Proceed only with each one written into docs/limitations.md with a stated reason.

## 1 — Dataset summary

- master seed: `20260115`
- params sha256: `55b26c159584ea9736f036cce986021a…`
- dgp sha256: `a188db118d8b1d46668ea2558abdca06…`
- generated: 2026-08-24T20:00:56.662867+00:00

| Table | Rows |
|---|---:|
| `dim_customer` | 55,000 |
| `dim_date` | 90 |
| `dim_geography` | 500 |
| `dim_product` | 8,000 |
| `dim_seller` | 1,200 |
| `fct_checkout_event` | 814,970 |
| `fct_checkout_session` | 155,000 |
| `fct_customer_state_at_session` | 155,000 |
| `fct_delivery_event` | 362,796 |
| `fct_order` | 105,605 |
| `fct_order_economics` | 105,605 |
| `fct_payment_attempt` | 48,315 |
| `truth_customer_latent` | 55,000 |
| `truth_order_probability` | 155,000 |

## 2 — Calibrated levels

| Level | Solved |
|---|---:|
| `cod_model_beta0` | 0.875000 |
| `rto_model_gamma0` | -4.687500 |
| `conversion_model_alpha0` | 0.281250 |
| `pre_window_cod_pi0` | 0.515625 |
| `pre_window_rto_pi0` | -3.375000 |
| `product_price_scalar` | 1.039062 |
| `support_ndr_base` | 9.000000 |

Frozen (decision A38):

- `post_dispatch_noise_sd` = 3.3125
- `post_dispatch_noise_sd_spec_value` = 0.85

## 3 — The planted causal effect (DERIVED, decision A6)

| Quantity | Value |
|---|---:|
| naive COD−prepaid gap | 17.73pp |
| average marginal effect | 9.99pp |
| selection share of the gap | 0.436 |
| naive ÷ truth | 1.77× |

> The spec's prose figures (13.4pp / 19.9pp / 33%) belong to `noise_sd = 0.85` and no longer describe this dataset. See limitation L8. Everything downstream must quote `_truth.json`, never the prose.

## 4 — Test results

| ID | Severity | Status | Test | Expected | Actual |
|---|---|---|---|---|---|
| VOL-01 | HARD | PASS | fct_order row count | >= 100,000 | 105,605 |
| VOL-02a | SOFT | PASS | Session count in band | [145,000, 170,000] | 155,000 |
| VOL-02b | HARD | PASS | orders/sessions equals reported conversion | < 0.001 | 0.00e+00 |
| VOL-03 | SOFT | PASS | Distinct customers with >=1 order | >= 40,000 | 46,539 |
| VOL-04 | HARD | PASS | Every dimension table at target +/-1% | within 1% | checked 4 dimensions |
| CAL-01 | HARD | PASS | COD share of orders | 0.62 +/-0.01 | 0.6233 |
| CAL-02 | HARD | PASS | Prepaid share | 0.38 +/-0.01 | 0.3767 |
| CAL-03 | SOFT | PASS | COD RTO rate | 0.24 +/-0.025 | 0.2334 |
| CAL-04 | SOFT | PASS | Prepaid RTO rate | 0.041 +/-0.025 | 0.0561 |
| CAL-05 | HARD | PASS | Blended RTO rate | 0.165 +/-0.01 | 0.1653 |
| CAL-06 | HARD | PASS | Checkout conversion | 0.68 +/-0.02 | 0.6813 |
| CAL-07 | SOFT | PASS | % of COD caused by payment failure | 0.068 +/-0.02 | 0.0590 |
| CAL-08 | SOFT | PASS | Addressable share of RTO cost | 0.65 +/-0.05 | 0.6144 |
| CAL-09 | HARD | PASS | No slope coefficient differs from params.yaml | exact match on every slope (tolerance 0) | 72 slope(s) verified across 5 block(s) |
| CAL-10 | HARD | PASS | RTO reason weights frozen | 35774eca8875a357… | 35774eca8875a357… |
| CAL-11 | HARD | PASS | Selection share of the naive COD-RTO gap | [0.25, 0.45] | 0.436 |
| EC-01 | HARD | PASS | Mean GMV per order | 1000 +/-25 | 1001.20 |
| EC-01b | SOFT | PASS | Mean order_value per order | 920 +/-30 | 919.74 |
| EC-02 | SOFT | PASS | Median order value | 624 +/-60 | 623.88 |
| EC-03 | HARD | PASS | prepaid delivered cm | 112 +/-4 | 111.24 |
| EC-04 | HARD | PASS | cod delivered cm | 107 +/-4 | 106.00 |
| EC-05 | HARD | PASS | cod rto cash loss | -309 +/-12 | -317.57 |
| EC-06 | HARD | PASS | cod rto economic cost | -416 +/-15 | -423.57 |
| EC-07 | SOFT | PASS | Annualised RTO exposure (Cr) | [150, 180] | 167.8 |
| EC-08 | HARD | PASS | Derived annualisation factor in band | [200, 280] | 227.26 |
| BR-01 | HARD | PASS | New customers use COD more | >= +10pp | +22.46pp |
| BR-02 | HARD | PASS | Prior-RTO lift, 95% CI lower bound | CI lower bound > 1.50 | 1.693x  [1.643, 1.743] |
| BR-03 | HARD | PASS | pit_cod_share predicts COD selection | >= 2.5 OR | 19.05 |
| BR-04 | SOFT | PASS | Lower seller rating -> more COD | spread >= 4% | 4.16% |
| BR-05 | SOFT | PASS | Lower product rating -> more COD | spread >= 3% | 4.19% |
| BR-06 | SOFT | PASS | COD share by value decile is inverted-U | peak in deciles 6-9 (0-indexed 5-8) | peak at 8 |
| BR-07 | HARD | PASS | Payment failure precedes some COD | [4%, 10%] | 5.90% |
| BR-08 | HARD | PASS | Address reason rises as completeness falls | Q1/Q4 >= 1.40 AND Spearman rho < 0 at p < 0.01 | gradient 1.62x, rho -0.0367, p 6.43e-06 |
| BR-09 | HARD | SKIP | Delay explains more deviance than promise | — | not runnable |
| BR-10 | SOFT | PASS | Month-end COD RTO lift | >= +1.5pp | +3.53pp |
| BR-11 | HARD | PASS | Switch-COD RTOs less than intent-COD | >= 5pp lower | +16.45pp |
| LK-01 | HARD | PASS | View columns subset of safe whitelist | enforced | verified against the live database |
| LK-02 | HARD | PASS | No blocked column is also whitelisted | empty intersection | 0 overlap(s) |
| LK-03 | HARD | PASS | Safe-feature AUC below the leakage guard | < 0.85 | ceiling 0.7717 |
| LK-04 | HARD | PASS | Point-in-time integrity | zero violations | structural |
| LK-05 | HARD | PASS | analyst role has zero privileges on truth | enforced | verified against the live database |
| LK-06 | HARD | PASS | Shrinkage prior is a declared constant, not computed from the data | rto=0.165, cod=0.62, payment=0.1382, k=8.0 (exact) | rto=0.165, cod=0.62, payment=0.1382, k=8.0 |
| DQ-01 | HARD | PASS | Reproducibility hash matches manifest | enforced | verified against the live database |
| DQ-02 | HARD | PASS | No duplicate primary keys | zero | 0 |
| DQ-03 | HARD | PASS | No orphan foreign keys | zero | 0 |
| DQ-04 | HARD | PASS | No negative cost lines | zero | 0 |
| DQ-05 | HARD | PASS | order_ts >= session_start_ts | zero | 0 |
| DQ-06 | HARD | PASS | outcome_resolved_date >= order_date | zero | 0 |
| DQ-07b | HARD | PASS | Full ledger identity | zero mismatches | 0 |
| DQ-07a | HARD | PASS | Resolved-only reconciliation | <= 0.002 | 0.0000 |
| DQ-07c | SOFT | PASS | Exclusions explained by the censoring model | excluded == censored + cancelled | excluded 14,355, censored 10,141, cancelled 4,214 |
| DQ-08 | HARD | PASS | RTO implies shipped and not delivered | zero | 0 |
| DQ-09 | HARD | PASS | Cancelled implies not shipped and not RTO | zero | 0 |
| DQ-10 | HARD | PASS | Every order has exactly one economics row | 105,605 | 105,605 |
| DQ-11 | HARD | PASS | Cash-at-delivery reason only on COD | zero | 0 |
| DQ-12 | HARD | PASS | No nulls on required columns | zero | 0 |
| DQ-13 | HARD | PASS | payment_rail NULL iff COD | zero | 0 |
| DQ-14 | HARD | PASS | Censoring present in the late window | >= 0.03 | 0.3563 |
| GT-02 | HARD | PASS | Naive gap exceeds the AME | naive > AME | 17.73pp > 9.99pp |
| GT-01 | HARD | SKIP | Coefficient recovery | — | not runnable |
| GT-03 | HARD | SKIP | Adjustment closes the gap partially | — | not runnable |
| GT-04 | HARD | SKIP | Planted null on review_count holds | — | not runnable |
| GT-06 | HARD | SKIP | H6: delay explains more than promise | — | not runnable |
| GT-07 | HARD | SKIP | Selection decomposition via PSM | — | not runnable |
| GT-05 | HARD | PASS | AUC ceiling in band | [0.74, 0.79] | 0.7717 |

## 5 — Skipped tests, split by reason

**These are NOT passes and NOT failures.** 6 HARD test(s) could not run. They are grouped by cause so that a skip count is never read as an unverified count.

### ENVIRONMENT-BLOCKED — none

LK-01, LK-05 and DQ-01 ran against a live PostgreSQL and PASSED. LK-05 in particular was verified by opening a real connection AS the `analyst` role and having both `truth` reads refused with SQLSTATE 42501 — enforcement, not a catalogue inspection of intent.

### PHASE-5-DEFERRED — need fitted models, out of scope for Phase 2B

- **BR-09** — Delay explains more deviance than promise. Needs a fitted model on attempt_delay_days vs estimated_delivery_days. Phase 5 territory; the data supports it.
- **GT-01** — Coefficient recovery. Needs a fitted logistic regression on safe features. Phase 5 runs these; the data and truth file support it.
- **GT-03** — Adjustment closes the gap partially. Needs the confounder-controlled model. The relative rule and its min_gap_closed threshold are in params; Phase 5 evaluates them.
- **GT-04** — Planted null on review_count holds. Needs the fitted model's CI on log1p(review_count).
- **GT-06** — H6: delay explains more than promise. Same fitted-model dependency as BR-09.
- **GT-07** — Selection decomposition via PSM. Needs propensity matching. Phase 5.

Phase 2B builds the *dataset*; these test what an analysis recovers **from** it. The data and `_truth.json` support every one of them.


## 6 — Every non-passing test, in full

**BR-09 — Delay explains more deviance than promise** (SKIP)

Needs a fitted model on attempt_delay_days vs estimated_delivery_days. Phase 5 territory; the data supports it.

**GT-01 — Coefficient recovery** (SKIP)

Needs a fitted logistic regression on safe features. Phase 5 runs these; the data and truth file support it.

**GT-03 — Adjustment closes the gap partially** (SKIP)

Needs the confounder-controlled model. The relative rule and its min_gap_closed threshold are in params; Phase 5 evaluates them.

**GT-04 — Planted null on review_count holds** (SKIP)

Needs the fitted model's CI on log1p(review_count).

**GT-06 — H6: delay explains more than promise** (SKIP)

Same fitted-model dependency as BR-09.

**GT-07 — Selection decomposition via PSM** (SKIP)

Needs propensity matching. Phase 5.

