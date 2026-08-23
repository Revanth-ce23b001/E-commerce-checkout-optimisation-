# CLAUDE CODE IMPLEMENTATION PROMPT — PHASE 2B

> Copy everything below the line into Claude Code.

---

# TASK: Implement a reproducible synthetic e-commerce marketplace simulator

## 0. READ THIS BEFORE YOU WRITE ANY CODE

**Your first response must NOT contain implementation code and must NOT generate any data.**

Your first response must be exactly four things, in this order:

1. **Repository inspection** — what exists in this directory today (tree, key files, existing config, existing data, git status).
2. **Architecture summary** — a summary of the Phase 2A specification you found, in your own words, proving you read it. Include: the table list, the calibration targets, the two calibrated intercepts, and the leakage policy.
3. **Ambiguities and conflicts** — every place where the spec is unclear, internally inconsistent, or where you would need to make a judgement call. If there are none, say so explicitly.
4. **Implementation plan** — the module-by-module build order with checkpoints, and what you will build in Stage 3 (the small development dataset).

**Then stop and wait for my approval.** Do not proceed to Stage 3 until I reply.

---

## 1. WHAT WE ARE BUILDING AND WHY

We are building a **simulation of an Indian e-commerce marketplace**, because real order-level marketplace data is not publicly available. The simulation will be the dataset for a Product Management case study on checkout optimisation: reducing Return-to-Origin (RTO) while protecting conversion and contribution margin.

The causal chain the simulation must reproduce:

```
customer characteristics (incl. hidden traits)
  → checkout behaviour
  → payment-method choice (COD vs prepaid)
  → order
  → fulfilment
  → delivery or RTO
  → contribution margin
```

**The objective is NOT "generate 100,000 fake rows."**

The objective is: **build a reproducible simulation of a realistic marketplace whose behaviour is rich enough that a competent analyst, working only from the analyst-visible tables, could investigate — and could plausibly get partly wrong — the following questions:**

1. Why do customers choose COD?
2. Is COD associated with higher RTO, and how much of that association is causal versus selection?
3. How much RTO economic exposure exists?
4. Which customers and orders are high risk?
5. What interventions should different risk groups receive?
6. Can RTO be reduced without damaging conversion?
7. Does the intervention improve contribution margin?

**Critical framing:** we are not manufacturing data that proves a conclusion. We are planting a known ground truth — including truths that are *hard to recover* — so that later analysis can be checked against it. A dataset where the obvious analysis produces the obvious right answer is a failed dataset.

---

## 2. SOURCE OF TRUTH

Two documents govern this implementation. Locate and read both **in full** before planning:

- `docs/00_phase1_blueprint.md` — business framing, unit economics, metric definitions, opportunity model
- `docs/01_phase2_data_architecture.md` — **the implementation specification.** This is the primary reference.

If either file is not in the repository, **stop and ask me to provide it.** Do not invent an architecture.

### 2.1 Rules for handling the spec

**You must NOT silently:**
- add, remove, merge, or rename tables
- change any business definition (RTO rate denominator, contribution margin formula, net conversion, north-star metric)
- change any calibration target
- change the ₹165 Cr opportunity framework or the ×240 annualisation factor
- change any economics parameter (₹309 cash loss, ₹416 economic cost, p\* = 25.7%)
- change any model coefficient
- "fix" a failing validation test by adjusting a slope coefficient

**If something is ambiguous, underspecified, or internally inconsistent — flag it and ask.** Do not resolve it yourself and proceed. An unflagged assumption is the single worst failure mode in this task.

**If a calibration target cannot be met with the specified coefficients, that is a finding, not a bug.** Report it. Do not tune slopes to force it.

---

## 3. THE NON-NEGOTIABLE INVARIANTS

Extract these from the spec and treat them as hard constraints. Restate them in your Stage 1 response to confirm you have them right.

### 3.1 Scale and framing
| Invariant | Value |
|---|---|
| Orders in dataset | ≥ 100,000 |
| Checkout sessions | ≈ 147,059 (orders ÷ 0.68 conversion) |
| Simulation window | 90 days |
| Population framing | 24,000,000 orders/year |
| Annualisation factor | **×240** (must equal `sample_to_quarter × quarter_to_year`) |

### 3.2 Calibration targets
| Target | Value | Tolerance |
|---|---|---|
| COD share of orders | 62.0% | ±1.0pp |
| COD RTO rate (shipped denominator) | 24.0% | ±1.5pp |
| Prepaid RTO rate | 4.1% | ±0.8pp |
| Blended RTO rate | 16.5% | ±1.0pp |
| Checkout conversion (orders ÷ sessions) | 68.0% | ±2.0pp |
| **Mean** order value | ₹1,000 | ±₹25 |
| Median order value | ≈ ₹690 | ±₹60 |

**Mean, not median, is pinned at ₹1,000.** Order value is a right-skewed category-mixture lognormal. Do not make every order ₹1,000.

### 3.3 Economics (at a ₹1,000 order value)
| Line | Value | Tolerance |
|---|---|---|
| Prepaid delivered contribution margin | **+₹112** | ±₹4 |
| COD delivered contribution margin | **+₹107** | ±₹4 |
| COD RTO direct cash loss | **−₹309** | ±₹12 |
| COD RTO total economic cost | **−₹416** | ±₹15 |
| Break-even RTO probability p\* | **25.7%** | ±0.8pp |

### 3.4 The planted causal structure — the most important part of this build
| Quantity | Value |
|---|---|
| `is_cod` coefficient in the RTO logit | **+1.60** (odds ratio ≈ 4.95) |
| True marginal effect of COD on RTO | **≈ 13.4pp** |
| Naive observed COD−prepaid gap | **≈ 19.9pp** |
| Selection share of the naive gap | **≈ 33%** |
| Achievable risk-model AUC ceiling | **0.74 – 0.79** |

The selection component comes from three **hidden latent customer traits** (`latent_trust`, `latent_liquidity`, `latent_intent`) that drive *both* payment choice and RTO. `latent_intent` is the core confounder. These must be genuinely unobservable to the analyst.

The AUC ceiling comes from a **post-dispatch shock** — courier reliability, realised delay, and irreducible noise `ν ~ N(0, 0.85)` — applied *after* the pre-checkout score is computed and stored.

If your model can predict RTO with AUC ≥ 0.85 from analyst-visible pre-checkout features, **something has leaked**. That is validation test LK-03 and it is a hard failure.

### 3.5 Calibration discipline
**Only two numbers may be solved by the calibrator:**
- `cod_model.intercept_solved`
- `rto_model.intercept_solved`

**Every slope coefficient is fixed a priori and immutable.** Validation test CAL-09 asserts that no slope in the run manifest differs from `params.yaml`. Implement CAL-09 early, not last.

---

## 4. TECHNOLOGY STACK

| Use | For |
|---|---|
| **Python 3.11+** | Generation, probability models, simulation, validation, reporting |
| **NumPy** | All randomness — via `SeedSequence` substreams, never a global seed |
| **pandas** | Transformation and assembly |
| **PostgreSQL 14+** | Storage, relational integrity, later SQL analysis |
| **SQLAlchemy + psycopg2** | Loading |
| **PyYAML + jsonschema** | Config load and validation |
| **statsmodels** | Ground-truth recovery tests (logistic regression) |
| **scikit-learn** | Validation benchmarking only (AUC, calibration curve) |
| **pytest** | Unit tests for generator functions |
| **Parquet** | Intermediate storage |

**Do not introduce** deep learning frameworks, orchestration tools (Airflow/Prefect/dbt), ORMs beyond SQLAlchemy Core, or any dependency not on this list without asking.

The code must be readable by a Product Manager who knows basic Python and SQL. Prefer an explicit, boring function over a clever abstraction.

---

## 5. PROJECT STRUCTURE

Build this structure. Explain every directory in `README.md`.

```
ecommerce-checkout-optimization/
├── README.md
├── Makefile                        # make generate | validate | load | all | dev
├── pyproject.toml                  # pinned dependencies
│
├── config/
│   ├── params.yaml                 # THE single source of assumptions
│   ├── params.schema.json          # validates params.yaml on load — fail fast
│   └── scenarios/                  # sensitivity overrides
│       ├── dev_small.yaml          # 5,000 orders, for Stage 3
│       ├── low_scale.yaml
│       ├── high_scale.yaml
│       └── low_rto_cost.yaml
│
├── src/
│   ├── config/
│   │   ├── loader.py               # load, schema-validate, hash params
│   │   └── seeds.py                # SeedSequence substream harness
│   ├── generators/
│   │   ├── dates.py  geography.py  sellers.py  products.py
│   │   ├── customers.py  latents.py  history.py
│   │   ├── sessions.py  state_snapshots.py
│   │   ├── cod_choice.py  payment_attempts.py  conversion.py
│   │   ├── orders.py  cancellations.py
│   │   ├── rto_precheckout.py  delivery.py  rto_outcome.py  rto_reasons.py
│   │   └── rollup.py
│   ├── models/
│   │   ├── logit.py                # shared logit assembly + component tracing
│   │   └── calibrate.py            # bisection wrapper for the two intercepts
│   ├── economics/
│   │   └── contribution_margin.py  # every cost line, one place
│   ├── validation/
│   │   ├── tests_vol.py  tests_cal.py  tests_ec.py  tests_br.py
│   │   ├── tests_lk.py   tests_dq.py   tests_gt.py
│   │   └── report.py
│   └── utils/
│       ├── io.py                   # parquet/CSV writers
│       └── shrinkage.py            # empirical-Bayes helper
│
├── scripts/
│   ├── generate_marketplace.py     # runs the full pipeline
│   ├── validate_marketplace.py
│   ├── load_postgres.py
│   ├── multi_seed_check.py
│   └── run_scenarios.py
│
├── sql/
│   ├── 00_schema_analytics.sql
│   ├── 01_schema_truth.sql         # includes REVOKE statements
│   ├── 02_indexes.sql
│   ├── 03_views_core.sql
│   └── 04_view_risk_model_input.sql   # the leakage firewall
│
├── data/
│   ├── raw/                        # parquet, gitignored
│   ├── processed/
│   ├── truth/_truth.json           # committed — small and important
│   ├── validation/
│   └── manifests/                  # run hashes for reproducibility tests
│
├── tests/                          # pytest — unit tests for GENERATOR CODE
├── notebooks/                      # empty; Phase 3
├── reports/
│   ├── data_validation_report.md
│   └── figures/
└── docs/
    ├── 00_phase1_blueprint.md
    ├── 01_phase2_data_architecture.md
    ├── data_dictionary.md          # generated from live DDL, not hand-written
    ├── data_generating_process.md
    ├── leakage_policy.md
    ├── economics.md
    └── validation.md
```

**`tests/` and `src/validation/` are different things.** `tests/` unit-tests the generator code (does the shrinkage function behave at n=0?). `src/validation/` tests the generated data against business targets. Do not merge them.

---

## 6. CONFIGURATION

Build `config/params.yaml` first, following the structure in the Phase 2A spec §13. It must contain, at minimum:

```yaml
meta:              version, currency, window_days, window_start
scale:             n_customers, n_sellers, n_products, n_geographies,
                   target_orders, checkout_conversion_target,
                   population_annual_orders, annualization_factor
calibration_targets:  every target from §3.2 above, each with its tolerance
distributions:     geo_tier_weights, category_weights,
                   category_mean_order_value, category_ov_sigma,
                   seller_rating, product_rating, review_count,
                   pre_window_orders
latents:           correlation structure, geo-tier shifts
cod_model:         intercept_solved (null), coefficients {...}, noise_sd
rto_model:         intercept_solved (null), coefficients {...},
                   post_dispatch_shock {...}
payment_failure:   rail_mix, first_attempt_failure, retry params,
                   terminal split, failure reasons
rto_reasons:       base_weights, class_map, driver_weights
economics:         every cost parameter
fulfilment:        cancel rates, dispatch lag, max attempts
leakage_guard:     safe_feature_whitelist, hard_blocked
seed:              master, substreams (ordered list)
```

**Rules:**
- **No business assumption may appear as a literal in Python code.** If a number matters, it lives in `params.yaml`. Add a unit test that greps `src/generators/` for suspicious numeric literals.
- `intercept_solved` fields are **machine-written only**. The calibrator writes them and stamps a `calibration_run_id`. A human editing them breaks reproducibility.
- `params.yaml` is SHA-256 hashed on load and the hash goes into every run manifest and into `_truth.json`.

---

## 7. RANDOMNESS AND REPRODUCIBILITY

**Do not use `np.random.seed()`.** Use independent substreams:

```python
from numpy.random import SeedSequence, default_rng

root = SeedSequence(config.seed.master)
names = config.seed.substreams          # ordered, fixed list in params.yaml
children = root.spawn(len(names))
RNG = {name: default_rng(child) for name, child in zip(names, children)}
```

Each generator module draws **only** from its own named substream.

**Why this matters:** changing `n_products` from 8,000 to 9,000 must not shift the customer latents. Without independent substreams, every parameter change reshuffles the whole population and no sensitivity analysis is interpretable.

**Rules:**
- New substreams are appended to the **end** of the list, never inserted in the middle.
- The seed controls *sampling*. `params.yaml` controls the *DGP*. Changing the seed must never change a coefficient; changing a coefficient must never require a new seed.
- Same `master_seed` + same params hash ⇒ byte-identical output. Enforce with test DQ-01, which hashes `fct_order` against a stored manifest.
- The calibration bisection loop must **reuse the same substreams on every iteration**, so only the intercept varies. Otherwise calibration becomes a random walk and will not converge.

---

## 8. GENERATION ORDER AND WHY IT IS FORCED

Implement in exactly this order. In your plan, state the blocking dependency for each step.

| # | Module | Blocking dependency |
|---|---|---|
| 01 | Load + validate config, spawn seed substreams | — |
| 02 | `dim_date` | config |
| 03 | `dim_geography` | config |
| 04 | `dim_seller` | config |
| 05 | `dim_product` | 04 — products belong to sellers |
| 06 | `dim_customer` (pre-history) + `truth_customer_latent` | 03 — customers need a home geography |
| 07 | Customer pre-window history | **06 — history must be generated FROM the latents.** This is what creates the confounding |
| 08 | `fct_checkout_session` | 02, 05, 07 |
| 09 | `fct_customer_state_at_session` | **08 — strict chronological pass** |
| 10 | COD intent + `truth.p_cod_intent` | 09 — needs point-in-time features |
| 11 | `fct_payment_attempt` | 10 |
| 12 | Conversion / abandonment / `abandon_step` | **11 — some abandonment is caused by payment failure** |
| 13 | `fct_order` | 12 |
| 14 | Pre-ship cancellations, `is_shipped` | **13 — must precede RTO; it is the RTO-rate denominator** |
| 15 | Pre-checkout RTO score → `truth.p_rto_precheckout` | 14 |
| 16 | `fct_delivery_event` + post-dispatch shock → `truth.p_rto_final` | **15 — the pre-checkout score must be frozen before Stage-4 info exists** |
| 17 | RTO Bernoulli draw, `is_delivered`, `actual_delivery_days`, `delivery_attempts` | 16 |
| 18 | `rto_reason`, `rto_reason_class`, `ndr_code` | 17 |
| 19 | `fct_order_economics` | **18 — costs are outcome-conditional** |
| 20 | `dim_customer` roll-up (`hist_*_final`, `clv_estimate`) | 19 |
| 21 | Write `_truth.json` and truth tables | 20 |
| 22 | Load PostgreSQL, create views, REVOKE truth schema | 21 |
| 23 | Run validation, emit report | 22 |

---

## 9. MODULE REQUIREMENTS

### 9.1 Geography (03)
Metro / Tier-1 / Tier-2 / Tier-3 clusters with `serviceability_score`, `courier_reliability_score`, `base_delivery_days`, `forward_freight_base`, and `cod_cultural_index`.

**`cod_cultural_index` is deliberately separate from trust.** It gives geography a path to COD that runs through local norms rather than distrust. Without it, the analysis trivially concludes "Tier-3 = low trust," which is the lazy answer the project exists to avoid. Geography must **not** be a deterministic proxy for customer quality.

### 9.2 Sellers (04)
Rating, rating count, tenure, `seller_sla_breach_rate`, `seller_cancellation_rate`, fulfilment model, derived tier. Seller quality influences customer trust and RTO probability but must **never** determine outcomes deterministically. Include noise.

### 9.3 Products (05)
Category, sub-category, list price, rating, review count, base discount, `cogs_ratio`, weight band, category-specific `shrink_rate`, returnable flag.

Price is a **right-skewed category-mixture lognormal** calibrated so the population mean order value lands at ₹1,000 with a median near ₹690.

### 9.4 Customers and latents (06)
Generate four latent traits per customer with the specified correlation structure:
- `latent_trust` — belief that online payment produces correct goods
- `latent_liquidity` — cash/credit access (correlated with geo tier)
- `latent_intent` — **low-commitment / free-optionality trait. The core confounder.**
- `latent_price_sensitivity`

These go **only** into `truth_customer_latent`. They must never appear in any analyst-visible table, view, or exported file.

Also generate: signup date, tenure, home geography, age bucket, acquisition channel, `has_saved_prepaid_instrument`.

### 9.5 Customer history (07)
Generate pre-window history **from the latents**, so the confounding is causal rather than fitted.

**Consistency constraints — enforce, do not hope:**
- A customer cannot have more orders than their tenure plausibly allows. Cap pre-window orders by `tenure_days`.
- `pre_window_delivered + pre_window_rto_count ≤ pre_window_orders`
- `pre_window_prepaid_success ≤ pre_window_orders − pre_window_cod_orders`
- Zero-inflate: a realistic share of customers have zero prior orders.

Write a unit test for each constraint.

### 9.6 Sessions (08)
Generate checkout **sessions separately from orders**. This is critical: the north-star metric is *contribution margin per checkout session started*, so sessions that never convert are part of the denominator and must exist as rows.

Each session gets: timestamp, device, candidate product, cart size, cart value, delivery geography, `estimated_delivery_days`, `address_completeness_score`.

Session volume follows the `dim_date` demand index (weekly seasonality + month-end effect).

### 9.7 Point-in-time state (09) — THE MOST IMPORTANT MODULE

For every session, snapshot what was known about that customer **at that exact timestamp**: prior orders, prior delivered, prior RTO count, raw and empirical-Bayes-shrunk RTO rate, COD share, prepaid success count, payment failure rate, recency, average order value, plus the derived `pit_is_new_customer`, `pit_has_clean_record`, and `pit_risk_tier_rule_based`.

**Rules:**
- Process sessions in **strict chronological order**.
- A prior order counts toward `pit_orders_delivered` / `pit_rto_count` **only if its outcome had resolved** before this session's timestamp. Outcomes take 4–25 days. An order placed 3 days ago has not resolved.
- No future information may leak backward. Test LK-04 re-derives every snapshot independently and asserts zero violations.
- Test DQ-07 asserts that each customer's last-session snapshot plus that session's outcome reconciles to `dim_customer.hist_*_final`.

Use empirical-Bayes shrinkage (`k = 8`) for `pit_rto_rate_shrunk`, because raw rates at n=1 are useless.

### 9.8 COD choice (10) — two-step
```
STEP A — INTENT
  logit P(COD_intent) = β₀ + latent terms + history terms + geo terms
                      + order-value terms (including a squared term for the inverted-U)
                      + trust-proxy terms + logistics terms + category + time + ε
  COD_intent ~ Bernoulli(logistic(·))

STEP B — REALISATION
  COD_intent = 1                    → final method = COD
  COD_intent = 0                    → prepaid attempt sequence (§9.9)
       success                      → PREPAID
       terminal failure → switch    → COD, paid_via_switch = TRUE
       terminal failure → abandon   → no order
```

**The two-step structure is mandatory.** A single draw of COD-or-prepaid cannot distinguish preference from coercion, and the whole H11 analysis depends on that distinction.

**Never assign COD directly.** Every payment method is a Bernoulli draw from a computed probability.

Store the full component breakdown of the logit as JSONB in `truth_order_probability.logit_cod_components`.

### 9.9 Payment attempts (11)
Implement the failure state machine: rail selection → attempt → failure with a reason code → retry decision → retry outcome → terminal action (switch to COD / switch rail / abandon).

Failure probability varies by rail, by the customer's historical failure rate, and by a clustered bank-downtime shock.

This must support answering: *what percentage of COD orders were actually caused by prepaid payment friction rather than COD preference?*

Expected outcome ≈ **6.8% of COD orders** (tolerance ±2pp, SOFT). Do not tune to force a higher number.

### 9.10 Conversion (12)
Abandonment must be *causally connected* to what happened. Set `abandon_step` to one of ADDRESS / PAYMENT_PAGE / PAYMENT_FAILURE / FEE_REVEAL. Sessions that abandoned because of a terminal payment failure must be labelled PAYMENT_FAILURE — this is only possible because module 11 ran first.

### 9.11 Orders and cancellations (13–14)
Materialise converted sessions as orders. Maintain referential integrity — no orphans.

Then generate pre-ship cancellations (customer / seller / system actors) and set `is_shipped`.

**`is_shipped` is the RTO-rate denominator.** Cancelled orders are removed from the RTO population *before* the RTO draw. Getting this wrong makes every RTO rate in the project wrong.

### 9.12 RTO — two stages (15–17)

```
STAGE 1 — PRE-CHECKOUT SCORE (everything knowable at the payment step)
  logit_pre = γ₀ + payment terms + history terms + hidden latent terms
            + geo terms + order terms + product terms + seller terms
            + logistics terms + month-end × COD interaction
  → store as truth.p_rto_precheckout   ★ the AUC ceiling for any risk model ★

STAGE 2 — POST-DISPATCH SHOCK (exists only after the parcel moves)
  shock = δ₁·(courier unreliability) + δ₂·(realised delay days)
        + δ₃·(seller dispatched late) + ν,   ν ~ N(0, 0.85)
  logit_final = logit_pre + shock
  → store as truth.p_rto_final

STAGE 3 — DRAW
  rto_flag ~ Bernoulli(p_rto_final)      [shipped orders only]
```

**Never write `if payment_method == 'COD': rto = True`.** COD enters as one coefficient among ~25. Every outcome is a Bernoulli draw.

**The pre-checkout score must be computed and stored before any Stage-4 information exists.** This is what makes the AUC ceiling structural and honest.

Store the full logit component breakdown as JSONB.

### 9.13 Two risk models must both be supportable (data only — do not train them)
- **M1 — pre-selection:** trained without `payment_method`. Decides which payment options and trust signals to show.
- **M2 — post-selection:** may use `payment_method` and `paid_via_switch`. Decides whether a COD fee or partial payment applies.

The generated data must support both. The break-even threshold p\* = 25.7% belongs to **M2**, since it is derived from the expected value of a COD order.

**Do not build, train, or tune either model in this phase.** Only the validation harness may fit a logistic regression, and only to run the ground-truth recovery and leakage tests.

### 9.14 RTO reasons (18)
Ten reasons, generated **conditionally** via a softmax over driver-weighted scores — not sampled from a fixed table. The addressable/structural split must **emerge** and then be validated, because hard-coding it would make the downstream avoidability waterfall circular.

Target: ≈65% addressable / ≈35% structural, measured on RTO **cost**, not order count.

**Enforced consistency constraints:**
- `INSUFFICIENT_CASH_AT_DELIVERY` has zero probability on prepaid orders (test DQ-11)
- P(`ADDRESS_INCORRECT_INCOMPLETE`) rises monotonically as `address_completeness_score` falls (test BR-08)
- `NEVER_ORDERED_LOW_INTENT` is suppressed for customers with ≥5 delivered orders

`rto_reason` is a post-outcome variable. It must never enter the risk-model feature set (test LK-03).

### 9.15 Economics (19)
One row per order with every cost line separated. Costs are **outcome-conditional** — implement the incurrence matrix from the spec exactly:

```
net_revenue = (gmv − discount + shipping_fee + cod_fee) IF delivered ELSE 0
cogs        = cogs_ratio × net_revenue IF delivered ELSE 0

always on dispatch:  forward_shipping, packaging
prepaid only:        payment_processing_fee
COD + delivered:     cod_handling_cost
RTO only:            reverse_shipping, reverse_handling, shrink,
                     working_capital, cod_failed_attempt_cost (if COD)
delivered only:      ops_allocation
both (asymmetric):   support_ndr_cost

contribution_margin = net_revenue − cogs − total_variable_cost
rto_cash_loss       = −contribution_margin  IF rto_flag ELSE 0
foregone_cm         = counterfactual_cm_if_delivered  IF rto_flag ELSE 0
rto_economic_cost   = rto_cash_loss + foregone_cm
```

Compute and store `counterfactual_cm_if_delivered` by re-running the same formula with `is_delivered = TRUE`. This makes the opportunity waterfall reproducible directly from the table.

**Reconcile to the spec at a ₹1,000 order:** +₹112 prepaid delivered, +₹107 COD delivered, −₹309 RTO cash, −₹416 RTO economic. These are hard tests (EC-03 … EC-06).

Also report — but do **not** tune — the empirical mean RTO cost across the actual right-skewed order distribution. It will differ from ₹416, and the difference is informative.

---

## 10. THE TWO SCHEMAS AND THE LEAKAGE FIREWALL

Create **two PostgreSQL schemas**:

| Schema | Contents | Access |
|---|---|---|
| `analytics` | The 12 analyst-visible tables | role `analyst` — full SELECT |
| `truth` | `truth_customer_latent`, `truth_order_probability` | role `analyst` — **REVOKE ALL** |

```sql
REVOKE ALL ON SCHEMA truth FROM analyst;
REVOKE ALL ON ALL TABLES IN SCHEMA truth FROM analyst;
```

Leakage protection is a **permissions boundary**, not a code-review convention. Test LK-05 verifies the grants.

### 10.1 The risk-model input view

Create `analytics.vw_risk_model_input` exactly as specified. It selects only whitelisted safe features from `fct_checkout_session`, `fct_customer_state_at_session`, and immutable dimension attributes, filtered to `is_shipped = TRUE`.

It must **not** select: `dim_customer.hist_*_final`, `clv_estimate`, `analytics_segment`, any delivery event field, any economics field, `rto_reason`, `actual_delivery_days`, `delivery_attempts`, `order_status`, `is_delivered`, `ndr_code`.

**The subtlest trap:** `dim_customer.hist_orders_final`, `hist_rto_rate_final`, `hist_cod_share_final` and `clv_estimate` are end-of-window aggregates that include the current order. They look like innocent customer attributes. They are leakage. Keep them in the schema (they are needed for dashboards) and firewall them out of the view.

Test LK-01 asserts the view's column list is a subset of `params.leakage_guard.safe_feature_whitelist`.

### 10.2 Analyst-visible export

Also emit a clean flat file an analyst would realistically receive, containing no latents, no true probabilities, no true coefficients, no future information, and no outcome-derived features beyond the target itself.

---

## 11. POSTGRESQL

- Primary keys on every table; foreign keys enforced across all relationships
- Appropriate types — `NUMERIC` for money (never `FLOAT`), `TIMESTAMP` for events, `JSONB` for logit component traces
- CHECK constraints for the invariants: no negative prices or costs, `order_ts >= session_start_ts`, `outcome_resolved_date >= order_date`, valid enum values
- Indexes on all foreign keys, on `order_date`, `session_start_ts`, `payment_method`, `rto_flag`, and `geo_tier`
- Use `COPY` for bulk load, not row-by-row inserts
- The load script must be idempotent: drop-and-recreate, or truncate-and-reload, with a `--force` guard

---

## 12. VALIDATION — MANDATORY, AUTOMATED, BEFORE ANY SUCCESS CLAIM

Implement all 42 tests from the spec across seven families. **HARD** failures block; **SOFT** failures are logged and require written sign-off.

| Family | Count | Covers |
|---|---|---|
| **VOL** | 4 | Row counts, session count, dimension sizes |
| **CAL** | 9 | COD share, RTO rates by method and blended, conversion, addressable share, payment-failure share, **CAL-09: no slope changed** |
| **EC** | 7 | Mean/median order value, the four CM reconciliation lines, annualised exposure |
| **BR** | 11 | Every planted behavioural relationship, each with a **directional test AND an effect-size floor** |
| **LK** | 5 | View whitelist, blocked-column absence, **AUC < 0.85**, point-in-time integrity, DB grants |
| **DQ** | 14 | Reproducibility hash, duplicates, orphans, negatives, date ordering, state consistency, reconciliation, censoring present |
| **GT** | 7 | Coefficient recovery, naive overstatement, partial adjustment, planted null, AUC ceiling, H6 resolution, selection decomposition |

### 12.1 Behavioural tests need effect-size floors, not just significance
At 100K rows everything is significant. Each BR test must assert a **minimum effect size**:
- New customers have ≥ +10pp higher COD share
- Prior-RTO customers have ≥ 1.8× forward RTO lift
- Switch-COD orders RTO at least 5pp **lower** than intent-COD orders
- COD share by order-value decile is **non-monotonic** (inverted-U, peak in deciles 6–9, not 10)
- Delivery *delay* explains more deviance than delivery *promise*

### 12.2 Ground-truth recovery — the point of the whole exercise

| Test | Assertion |
|---|---|
| GT-01 | ≥80% of planted coefficients inside the estimated 95% CI; **no sign flips** on any strong/moderate relationship |
| GT-02 | Naive COD−prepaid gap ∈ [18.5, 21.5]pp and exceeds the planted 13.4pp |
| GT-03 | Adjusted marginal effect ∈ [15, 19]pp — it moves toward 13.4 but **must not reach it**, because the latents are unobservable |
| GT-04 | The planted null (`review_count` on RTO) has a 95% CI containing zero |
| GT-05 | Model AUC within 0.03 of the `truth.p_rto_precheckout` ceiling; ceiling ∈ [0.74, 0.79] |
| GT-06 | Delay explains materially more deviance than promise |
| GT-07 | Propensity-matched COD effect lands between 13.4pp and 19.9pp |

**GT-03 is designed to fail to fully recover the truth.** If the adjusted estimate lands exactly on 13.4pp, a hidden variable has leaked into the analysis. Treat that as a failure, not a success.

### 12.3 Validation report

Emit `reports/data_validation_report.md` with nine sections: dataset summary (including run manifest, seed, params hash, wall time) · distribution checks · target calibration · behavioural relationship checks · economic checks · leakage checks · data-quality checks · ground-truth recovery · final decision.

Every test row must render as:
```
Test:      COD share of orders
Expected:  62.0%  (±1.0pp)
Actual:    61.8%
Delta:     −0.2pp
Status:    PASS
```

Final verdict, printed unmissably:

| Verdict | Condition |
|---|---|
| 🟢 **DATASET READY** | All HARD pass; ≤2 SOFT failures |
| 🟡 **CONDITIONAL** | All HARD pass; 3–5 SOFT failures, each documented in `docs/validation.md` with a reason |
| 🔴 **NOT READY** | Any HARD failure, or >5 SOFT failures |

**Never claim success while a HARD test fails.**

---

## 13. ITERATIVE CALIBRATION — AND WHAT IS FORBIDDEN

If validation fails:

1. Identify which **parameter** caused it
2. Adjust `config/params.yaml`
3. Regenerate
4. Re-validate
5. Repeat

**Absolutely forbidden:**
- Editing generated rows to make a metric pass
- Post-processing the data to hit a target
- Adjusting a **slope** coefficient to satisfy a calibration target (only the two intercepts may move, and only via the automated bisection)
- Adding a fudge factor, clamp, or correction term to force a number

If a target genuinely cannot be reached with the specified slopes, **stop and report it.** That means the assumption set is internally inconsistent, which is a real finding worth surfacing — not a bug to hide.

---

## 14. EXECUTION STAGES — STOP AT EACH GATE

| Stage | Work | Gate |
|---|---|---|
| **1** | Repository inspection, read both spec docs, summarise architecture, list ambiguities, propose plan | **STOP — wait for my approval** |
| **2** | `params.yaml` + `params.schema.json` + both SQL schema files + seed harness. Unit-test that changing `n_products` does not alter customer latents | Report before continuing |
| **3** | **Small development dataset — 5,000 orders** via `config/scenarios/dev_small.yaml`. Full pipeline end to end | Report |
| **4** | Run the full validation suite on the dev dataset | **STOP — show me the report** |
| **5** | Fix issues by adjusting parameters, not data. Re-run 3–4 until dev passes | Report each iteration |
| **6** | Generate the full **100,000+ order** dataset | Report |
| **7** | Full validation suite on the full dataset | Report |
| **8** | Load PostgreSQL, create views, apply REVOKE, verify grants | Report |
| **9** | Final validation report, multi-seed robustness check (5 seeds), sensitivity scenarios, documentation | Final summary |

**Do not generate 100K rows until Stage 4 has passed on the development dataset.** A calibration bug found at 5K costs seconds; found at 100K it costs a full regeneration cycle.

Between stages, give me a short status: what you built, what passed, what failed, what you changed, what's next.

---

## 15. IMPLEMENTATION STYLE

- Clean, modular Python. Functions, not one giant script. No file over ~300 lines.
- Type hints on public function signatures.
- Docstrings that state **what business concept** the function implements and **which spec section** it comes from.
- Business logic (`src/generators/`, `src/models/`, `src/economics/`) strictly separate from I/O (`src/utils/io.py`, `scripts/load_postgres.py`).
- Configuration strictly separate from code. Zero business literals in `src/`.
- Every generation step independently reproducible from its substream.
- `pytest` unit tests for: shrinkage at n=0, the logit assembler, history consistency constraints, the CM formula against the three canonical worked examples (prepaid delivered, COD delivered, COD RTO), and seed independence.
- Avoid unnecessary abstraction. A PM should be able to open `cod_choice.py` and follow it.

---

## 16. DOCUMENTATION

`README.md` — project purpose, architecture overview, directory guide, key assumptions, how to regenerate, how to load PostgreSQL, how to run tests, how to run sensitivity scenarios.

Plus:
- `docs/data_dictionary.md` — **generated from the live DDL**, not hand-maintained
- `docs/data_generating_process.md` — the causal structure, the latents, and why the generation order is forced
- `docs/leakage_policy.md` — the five stages, the safe/blocked registry, the firewall view, the schema permissions
- `docs/economics.md` — every cost line, when it is incurred, the three worked examples, the reconciliation table
- `docs/validation.md` — every test, its rationale, and any signed-off SOFT failures

---

## 17. DO NOT DO THESE THINGS

- Do **not** generate 100K rows before the architecture is verified and the dev dataset passes validation
- Do **not** invent or change a business assumption without asking
- Do **not** use future information to build pre-checkout features
- Do **not** make COD deterministically produce RTO
- Do **not** make the data perfectly predictable — the AUC ceiling of ~0.76 is a requirement, not a defect
- Do **not** make all customers identical or all orders ₹1,000
- Do **not** expose latents, true probabilities, or true coefficients in any analyst-visible table, view, or export
- Do **not** modify generated rows to pass validation
- Do **not** build the risk model, the interventions, or the A/B test — those are Phase 3+
- Do **not** over-engineer the ML layer; scikit-learn is for validation benchmarking only
- Do **not** add dependencies outside the approved stack without asking
- Do **not** declare success with a HARD test failing

---

## 18. DEFINITION OF DONE

- [ ] Reproducible generator: same seed + same params hash ⇒ byte-identical output (DQ-01 passes)
- [ ] `config/params.yaml` holds every business assumption; zero business literals in `src/`
- [ ] PostgreSQL schemas `analytics` and `truth`, with keys, constraints, indexes, and verified REVOKE
- [ ] ≥100,000 orders and ≈147,000 checkout sessions
- [ ] All 12 analytical tables + 2 truth tables populated with referential integrity
- [ ] `data/truth/_truth.json` committed, containing the run manifest, solved intercepts, all planted coefficients, and the hypothesis ground truth
- [ ] Analyst-visible export with no latents and no leakage
- [ ] All 42 validation tests implemented and runnable via `make validate`
- [ ] `reports/data_validation_report.md` showing 🟢 DATASET READY (or documented 🟡)
- [ ] Multi-seed check across 5 seeds, with the spread on every calibration target reported
- [ ] Four sensitivity scenarios runnable via `scripts/run_scenarios.py`
- [ ] README + the five docs files complete
- [ ] `pytest` suite green

---

## 19. YOUR FIRST RESPONSE

Do not write code. Do not generate data. Respond with:

1. **Repository inspection** — what is here now
2. **Architecture summary** — proving you read both spec documents, including the table list, the calibration targets, the two calibrated intercepts, and the leakage policy
3. **Ambiguities and conflicts** — everything unclear or inconsistent, or an explicit statement that there are none
4. **Implementation plan** — module order with checkpoints, and what Stage 3's dev dataset will contain

Then stop and wait for my approval.
