# GT-03 diagnostics — measured, not fixed

Population: `data/processed/h1_population.parquet`, n = 91,250 (shipped AND NOT censored).
AME **9.99pp**, naive **17.73pp**, selection component **7.74pp**. GT-03's closure ceiling is **65%**.

GT-03 is **not restated and not waived**. This report answers the three questions the ruling asked before ruling further. No parameter, threshold, feature set or test changed.

---

## 1. Refit without `pit_rto_rate_shrunk`

Dropped columns: `pit_rto_rate_shrunk`.

| spec | n_confounders | logit_att_pp | logit_ate_pp | closed_att | closed_ate | cod_coefficient | pseudo_r2 |
|---|---|---|---|---|---|---|---|
| full confounder set | 41 | 12.56 | 10.67 | 66.9% | 91.2% | 1.0023 | 0.1493 |
| minus pit_rto_rate_shrunk | 40 | 12.66 | 10.77 | 65.5% | 90.0% | 1.0109 | 0.1478 |

Propensity matching (GT-03's PRIMARY estimate, ATT):

| Specification | Estimate | Closes | PS AUC | Unmatched COD |
|---|---|---|---|---|
| full confounder set | 12.24pp | 70.9% | 0.8354 | 0.9% |
| minus `pit_rto_rate_shrunk` | 12.76pp | 64.3% | 0.8347 | 0.9% |

---

## 2. Are the latents reconstructible from safe features?

R² of an OLS regression of each latent on the safe feature set. Order-level is what an adjustment exploits; customer-level is the honest per-person figure and is what any 'unobservable by construction' claim should be qualified against. Threshold for 'substantially reconstructible': **R² > 0.35**.

| scope | target | kind | n_features | order_r2 | n_customers | customer_r2 |
|---|---|---|---|---|---|---|
| GT-03 confounder set | latent_intent | latent | 41 | 0.1340 | 44007 | 0.1405 |
| GT-03 confounder set | latent_trust | latent | 41 | 0.1842 | 44007 | 0.1887 |
| GT-03 confounder set | latent_liquidity | latent | 41 | 0.2656 | 44007 | 0.2709 |
| GT-03 confounder set | true_cod_propensity | choice channel | 41 | 0.7655 | 44007 | 0.8125 |
| maximal safe set | latent_intent | latent | 58 | 0.1514 | 44007 | 0.1609 |
| maximal safe set | latent_trust | latent | 58 | 0.2198 | 44007 | 0.2246 |
| maximal safe set | latent_liquidity | latent | 58 | 0.2883 | 44007 | 0.2948 |
| maximal safe set | true_cod_propensity | choice channel | 58 | 0.8188 | 44007 | 0.8526 |

Highest R² on any LATENT: **0.2883** order-level, **0.2948** customer-level (`latent_liquidity`). Threshold 0.35 — NOT exceeded on any latent, at either level.

`true_cod_propensity` is not a latent. It is the composite the three latents drive — the CHOICE channel — and it reaches **0.8526** customer-level R². A latent can be unrecoverable while the choice it produces is highly predictable, and the two are different findings that look identical in a closure figure.

---

## 3. Which confounders close the gap

Deviance contribution is the likelihood-ratio statistic for dropping the block from the full RTO model — the definition H6 and BR-09 use. `closure_lost_pp` is how many percentage points of GT-03's closure disappear when the block is dropped, so a term can rank high on deviance and near-zero on closure if it is uncorrelated with COD choice.

Full model closes **66.9%**; with no confounders at all the estimate is **17.73pp**, closing **0.0%**. Total explained deviance **6611.6**.

Top 10 by deviance contribution:

| confounder | n_columns | deviance_contribution | share_of_explained | att_without_pp | closed_without | closure_lost_pp |
|---|---|---|---|---|---|---|
| courier_reliability_score | 1 | 698.4 | 10.6% | 12.53 | 67.2% | -0.36 |
| seller_sla_breach_rate | 1 | 193.9 | 2.9% | 12.55 | 66.9% | -0.06 |
| pit_rto_rate_shrunk | 1 | 123.6 | 1.9% | 12.66 | 65.5% | +1.34 |
| pit_cod_share | 1 | 99.1 | 1.5% | 13.18 | 58.8% | +8.06 |
| geo_tier | 3 | 92.0 | 1.4% | 12.65 | 65.7% | +1.16 |
| address_completeness_score | 1 | 72.8 | 1.1% | 12.56 | 66.8% | +0.09 |
| category | 5 | 61.2 | 0.9% | 12.63 | 65.9% | +0.99 |
| pit_has_history | 1 | 60.5 | 0.9% | 12.95 | 61.8% | +5.05 |
| pit_is_new_customer | 1 | 55.7 | 0.8% | 12.62 | 66.1% | +0.80 |
| pit_payment_failure_rate | 2 | 50.9 | 0.8% | 12.52 | 67.3% | -0.44 |

Same blocks reordered by closure lost:

| confounder | n_columns | deviance_contribution | share_of_explained | att_without_pp | closed_without | closure_lost_pp |
|---|---|---|---|---|---|---|
| pit_cod_share | 1 | 99.1 | 1.5% | 13.18 | 58.8% | +8.06 |
| pit_has_history | 1 | 60.5 | 0.9% | 12.95 | 61.8% | +5.05 |
| has_saved_prepaid_instrument | 1 | 36.5 | 0.6% | 12.77 | 64.1% | +2.77 |
| pit_rto_rate_shrunk | 1 | 123.6 | 1.9% | 12.66 | 65.5% | +1.34 |
| geo_tier | 3 | 92.0 | 1.4% | 12.65 | 65.7% | +1.16 |
| category | 5 | 61.2 | 0.9% | 12.63 | 65.9% | +0.99 |
| pit_is_new_customer | 1 | 55.7 | 0.8% | 12.62 | 66.1% | +0.80 |
| review_count | 1 | 16.6 | 0.3% | 12.60 | 66.3% | +0.57 |
| product_rating | 1 | 18.8 | 0.3% | 12.60 | 66.3% | +0.57 |
| order_value | 1 | 4.7 | 0.1% | 12.59 | 66.5% | +0.41 |
