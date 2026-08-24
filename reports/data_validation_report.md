# Data Validation Report

**Verdict: 🔴 NOT READY**

3 HARD failure(s). Do not proceed. Fix the generator, or escalate the assumption that cannot be satisfied.

## 1 — Dataset summary

- master seed: `20260115`
- params sha256: `a7203c7f296f82836ae5472348df0608…`
- dgp sha256: `24515e3515d079f8308c2dd5504a60d5…`
- generated: 2026-08-24T16:56:20.310008+00:00

| Table | Rows |
|---|---:|
| `dim_customer` | 55,000 |
| `dim_date` | 90 |
| `dim_geography` | 500 |
| `dim_product` | 8,000 |
| `dim_seller` | 1,200 |
| `fct_checkout_event` | 770,207 |
| `fct_checkout_session` | 155,000 |
| `fct_customer_state_at_session` | 155,000 |
| `fct_order` | 105,626 |
| `fct_order_economics` | 105,626 |
| `fct_payment_attempt` | 48,495 |
| `truth_customer_latent` | 55,000 |
| `truth_order_probability` | 147,059 |

## 2 — Calibrated levels

| Level | Solved |
|---|---:|
| `cod_model_beta0` | -0.250000 |
| `rto_model_gamma0` | -5.250000 |
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
| naive COD−prepaid gap | 17.65pp |
| average marginal effect | 10.05pp |
| selection share of the gap | 0.430 |
| naive ÷ truth | 1.76× |

> The spec's prose figures (13.4pp / 19.9pp / 33%) belong to `noise_sd = 0.85` and no longer describe this dataset. See limitation L8. Everything downstream must quote `_truth.json`, never the prose.

## 4 — Test results

| ID | Severity | Status | Test | Expected | Actual |
|---|---|---|---|---|---|
| VOL-01 | HARD | PASS | fct_order row count | >= 100,000 | 105,626 |
| VOL-02a | SOFT | PASS | Session count in band | [145,000, 170,000] | 155,000 |
| VOL-02b | HARD | PASS | orders/sessions equals reported conversion | < 0.001 | 0.00e+00 |
| VOL-03 | SOFT | PASS | Distinct customers with >=1 order | >= 40,000 | 46,522 |
| VOL-04 | HARD | PASS | Every dimension table at target +/-1% | within 1% | checked 4 dimensions |
| CAL-01 | HARD | PASS | COD share of orders | 0.62 +/-0.01 | 0.6220 |
| CAL-02 | HARD | PASS | Prepaid share | 0.38 +/-0.01 | 0.3780 |
| CAL-03 | SOFT | PASS | COD RTO rate | 0.24 +/-0.025 | 0.2339 |
| CAL-04 | SOFT | PASS | Prepaid RTO rate | 0.041 +/-0.025 | 0.0575 |
| CAL-05 | HARD | PASS | Blended RTO rate | 0.165 +/-0.01 | 0.1656 |
| CAL-06 | HARD | PASS | Checkout conversion | 0.68 +/-0.02 | 0.6815 |
| CAL-07 | SOFT | PASS | % of COD caused by payment failure | 0.068 +/-0.02 | 0.0592 |
| CAL-08 | SOFT | PASS | Addressable share of RTO cost | 0.65 +/-0.05 | 0.6126 |
| CAL-09 | HARD | PASS | No slope coefficient differs from params.yaml | exact match on every slope (tolerance 0) | 71 slope(s) verified across 5 block(s) |
| CAL-10 | HARD | PASS | RTO reason weights frozen | 35774eca8875a357… | 35774eca8875a357… |
| CAL-11 | HARD | PASS | Selection share of the naive COD-RTO gap | [0.25, 0.45] | 0.430 |
| EC-01 | HARD | PASS | Mean GMV per order | 1000 +/-25 | 1001.08 |
| EC-01b | SOFT | PASS | Mean order_value per order | 920 +/-30 | 919.60 |
| EC-02 | SOFT | FAIL | Median order value | 690 +/-60 | 624.00 |
| EC-03 | HARD | PASS | prepaid delivered cm | 112 +/-4 | 111.24 |
| EC-04 | HARD | PASS | cod delivered cm | 107 +/-4 | 106.00 |
| EC-05 | HARD | PASS | cod rto cash loss | -309 +/-12 | -317.57 |
| EC-06 | HARD | PASS | cod rto economic cost | -416 +/-15 | -423.57 |
| EC-07 | SOFT | FAIL | Annualised RTO exposure (Cr) | [150, 180] | 145.4 |
| EC-08 | HARD | PASS | Derived annualisation factor in band | [200, 280] | 227.22 |
| BR-01 | HARD | FAIL | New customers use COD more | >= +10pp | +6.96pp |
| BR-02 | HARD | FAIL | Prior-RTO customers RTO more | >= 1.8x | 1.79x |
| BR-03 | HARD | PASS | pit_cod_share predicts COD selection | >= 2.5 OR | 18.56 |
| BR-04 | SOFT | FAIL | Lower seller rating -> more COD | spread >= 4% | 3.95% |
| BR-05 | SOFT | PASS | Lower product rating -> more COD | spread >= 3% | 4.22% |
| BR-06 | SOFT | PASS | COD share by value decile is inverted-U | peak in deciles 6-9 (0-indexed 5-8) | peak at 8 |
| BR-07 | HARD | PASS | Payment failure precedes some COD | [4%, 10%] | 5.92% |
| BR-08 | HARD | FAIL | Address reason rises as completeness falls | monotone across quartiles | 0.051 -> 0.037 -> 0.038 -> 0.031 |
| BR-09 | HARD | SKIP | Delay explains more deviance than promise | — | not runnable |
| BR-10 | SOFT | PASS | Month-end COD RTO lift | >= +1.5pp | +3.40pp |
| BR-11 | HARD | PASS | Switch-COD RTOs less than intent-COD | >= 5pp lower | +15.69pp |
| LK-01 | HARD | SKIP | View columns subset of safe whitelist | — | not runnable |
| LK-02 | HARD | PASS | No blocked column is also whitelisted | empty intersection | 0 overlap(s) |
| LK-03 | HARD | PASS | Safe-feature AUC below the leakage guard | < 0.85 | ceiling 0.7702 |
| LK-04 | HARD | PASS | Point-in-time integrity | zero violations | structural |
| LK-05 | HARD | SKIP | analyst role has zero privileges on truth | — | not runnable |
| LK-06 | HARD | PASS | Shrinkage prior is a declared constant, not computed from the data | prior=0.165, k=8.0 (exact) | prior=0.165, k=8.0 |
| DQ-01 | HARD | SKIP | Reproducibility hash matches manifest | — | not runnable |
| DQ-02 | HARD | PASS | No duplicate primary keys | zero | 0 |
| DQ-03 | HARD | PASS | No orphan foreign keys | zero | 0 |
| DQ-04 | HARD | PASS | No negative cost lines | zero | 0 |
| DQ-05 | HARD | PASS | order_ts >= session_start_ts | zero | 0 |
| DQ-06 | HARD | PASS | outcome_resolved_date >= order_date | zero | 0 |
| DQ-07b | HARD | PASS | Full ledger identity | zero mismatches | 0 |
| DQ-07a | HARD | PASS | Resolved-only reconciliation | <= 0.002 | 0.0000 |
| DQ-07c | SOFT | PASS | Exclusions explained by the censoring model | excluded == censored + cancelled | excluded 14,346, censored 10,132, cancelled 4,214 |
| DQ-08 | HARD | PASS | RTO implies shipped and not delivered | zero | 0 |
| DQ-09 | HARD | PASS | Cancelled implies not shipped and not RTO | zero | 0 |
| DQ-10 | HARD | PASS | Every order has exactly one economics row | 105,626 | 105,626 |
| DQ-11 | HARD | PASS | Cash-at-delivery reason only on COD | zero | 0 |
| DQ-12 | HARD | PASS | No nulls on required columns | zero | 0 |
| DQ-13 | HARD | PASS | payment_rail NULL iff COD | zero | 0 |
| DQ-14 | HARD | PASS | Censoring present in the late window | >= 0.03 | 0.3555 |
| GT-02 | HARD | PASS | Naive gap exceeds the AME | naive > AME | 17.65pp > 10.05pp |
| GT-01 | HARD | SKIP | Coefficient recovery | — | not runnable |
| GT-03 | HARD | SKIP | Adjustment closes the gap partially | — | not runnable |
| GT-04 | HARD | SKIP | Planted null on review_count holds | — | not runnable |
| GT-06 | HARD | SKIP | H6: delay explains more than promise | — | not runnable |
| GT-07 | HARD | SKIP | Selection decomposition via PSM | — | not runnable |
| GT-05 | HARD | PASS | AUC ceiling in band | [0.74, 0.79] | 0.7702 |

## 5 — Failures and skips, explained

**EC-07 — Annualised RTO exposure (Cr)** (FAIL)

Spec 12.4: report, do not tune.

**BR-02 — Prior-RTO customers RTO more** (FAIL)

H3.

**BR-08 — Address reason rises as completeness falls** (FAIL)

Q1 is the WORST addresses, so the share must fall left to right.

**BR-09 — Delay explains more deviance than promise** (SKIP)

Needs a fitted model on attempt_delay_days vs estimated_delivery_days. Phase 5 territory; the data supports it.

**LK-01 — View columns subset of safe whitelist** (SKIP)

Needs a live PostgreSQL to read the view definition. No server on this machine; the DDL parses but was never applied.

**LK-05 — analyst role has zero privileges on truth** (SKIP)

Needs a live PostgreSQL. sql/01_schema_truth.sql contains the REVOKEs and parses, but has never been applied.

**DQ-01 — Reproducibility hash matches manifest** (SKIP)

Needs a stored manifest from a prior run. The seed harness and params hash are in place; the first run has nothing to compare to.

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

