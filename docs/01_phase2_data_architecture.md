# PHASE 2 — DATA ARCHITECTURE & DATA-GENERATING PROCESS

## E-commerce Checkout Optimization: Reducing RTO While Protecting Conversion and Contribution Margin

**Document type:** Technical specification (design only — no implementation)
**Phase:** 2A of 6 · Architecture design
**Status:** For review → gate for Phase 2B (Claude Code implementation)
**Depends on:** `docs/00_phase1_blueprint.md` (locked source of truth)
**Produces:** `config/params.yaml`, PostgreSQL schema, generator spec, validation harness spec

---

## 0. HOW TO READ THIS DOCUMENT

### 0.1 Tagging convention (inherited from Phase 1)

| Tag | Meaning |
|---|---|
| **[F]** | Structural fact — true by construction or arithmetic identity |
| **[A]** | Assumption — a parameter *we chose*; lives in `params.yaml`; must be defensible and sensitivity-tested |
| **[S]** | Simulated — sampled by the generator from a specified distribution |
| **[D]** | Derived — computed deterministically from [S]/[A] values via a stated formula |
| **[H]** | Hidden — generated, recorded in the truth schema, and **never exposed to the analytical layer** |

### 0.2 The one design principle that governs everything below

> **The generator must plant a business truth that a naive analysis would get wrong.**

If the synthetic data is built so that the obvious analysis produces the obvious answer, the project proves nothing. Every architectural choice in this document serves one goal: create a dataset where

- the **raw** COD-vs-prepaid RTO gap is **19.9pp**, but
- the **true planted causal effect** of payment method is **13.3pp**, and
- the remaining **6.6pp (33% of the naive gap) is selection** on latent traits the analyst cannot observe.

That gap between the naive and the adjusted estimate is the deliverable. Everything else is scaffolding.

A second, subtler requirement: the risk model must land at **AUC ≈ 0.75**, not 0.95. This is achieved not by adding meaningless noise but by making a meaningful part of the outcome depend on (a) **latent customer traits the analyst never sees** and (b) a **post-dispatch shock that does not exist at checkout time**. Both are realistic. Both are specified in §8.

### 0.3 Deviations from Phase 1 — flagged, not silent

Phase 1 is the source of truth. Six things needed refinement to make it implementable. Each is listed here so you can reject any of them before Claude Code touches a keyboard.

| # | Phase 1 said | Phase 2 proposes | Why | Impact on ₹165 Cr |
|---|---|---|---|---|
| **D1** | 100K orders = 5% sample of **one month**; annualization ×20 × ×12 = **×240** | 100K orders = **1.667% sample of one quarter (90 days)**; annualization ×60 × ×4 = **×240** | Phase 1 §14K also demanded "90 days of data with weekly seasonality and a month-end liquidity effect" to make RTO censoring real. A one-month window cannot carry a 30-day maturation window *and* show censoring. The quarter framing satisfies both and **the ×240 factor is unchanged** | **None.** 24M annual orders, ₹164.1 Cr, identical |
| **D2** | AOV = ₹1,000 (point value); Open Question #3 floated "median ₹1,000, mean ₹1,450" | **Right-skewed category-mixture lognormal with the population *mean* pinned at ₹1,000** (median ≈ ₹690) | Answers Open Question #3 in the affirmative — we get skew and meaningful value bands — but pins the **mean**, not the median, so every Phase 1 economic figure survives untouched. Pinning the median instead would inflate mean AOV to ~₹1,450 and silently inflate the headline to ~₹230 Cr | **None**, by construction |
| **D3** | H11 prior: 8–15% of COD orders preceded by a payment failure | DGP parameterised to yield **≈6–8%** | The prior should be *testable*, not *guaranteed*. Parameters are set from plausible external PG-failure ranges, not reverse-engineered from the prior. Phase 1 explicitly wants documented wrong priors — this is a candidate | None (H11 is a diagnosis, not an input to the sizing) |
| **D4** | RTO is a single-stage function of pre-checkout features | **Two-stage RTO**: a pre-checkout latent score (what a risk model could ever know) **+** a post-dispatch shock (courier quality, actual delay, delivery-day circumstance) | Without this, a model with all pre-checkout features would hit AUC ~0.92 and the Phase 1 claim "AUC ≈ 0.75, and that's honest" would be false. This makes the accuracy ceiling *structural*, and it is exactly what happens in reality | None |
| **D5** | — | `paid_via_switch_from_failed_prepaid` carries a **negative** RTO coefficient (−0.45) | A customer who *tried* to pay online and was forced to COD by a broken rail has demonstrated intent. Treating them as a typical COD customer would be wrong, and it sharpens the H11 product implication: fixing payment reliability recovers genuinely good orders | None |
| **D6** | Open Question #1: 1P/managed vs 3P | **Confirmed 1P/managed.** Platform owns COGS. The 3P take-rate variant stays as a §6.7 sensitivity, not a generated scenario | Locks the CM formula and the ₹416/RTO figure | None — this *is* the Phase 1 base case |

**If you reject D1 or D2, stop and tell me — everything downstream re-derives from those two.**

---

## 1. DATA ARCHITECTURE

### 1.1 Design philosophy: the minimum set that survives every Phase 1 requirement

I started from the eleven tables you listed and asked, for each: *which Phase 1 artefact breaks if this table does not exist?* Two tables were added and one was rejected.

| Table | Verdict | Which Phase 1 artefact requires it |
|---|---|---|
| `dim_geography` | **Keep** | §3 Branch 4 (hard-to-serve geos), §8.4 fairness audit by tier, §9.2 `delivery_pincode_tier` |
| `dim_seller` | **Keep** | §3 Branch 3, H5, §9.2 `seller_rating` |
| `dim_product` | **Keep** | §3 Branch 2, H4/H5, §9.2 category & rating features |
| `dim_customer` | **Keep** | Everything. Randomisation unit for the experiment (§11.1) |
| `dim_date` | **Keep (small)** | §14K "weekly seasonality and a month-end liquidity effect" — needs a clean calendar spine for the month-end flag and weekday cycle |
| `fct_checkout_session` | **Keep** | The north-star denominator (§2.4). Without sessions, CM/CSS is undefined |
| `fct_checkout_event` | **Keep** | §5.3 funnel step rates, §3 Branch 5 "abandonment spike between address and payment page", `abandon_step` |
| `fct_payment_attempt` | **Keep** | H11. Different grain from checkout events — one row per *attempt on a rail*, with reason codes and retry ordering |
| `fct_order` | **Keep** | Obvious |
| `fct_delivery_event` | **Keep** | `delivery_attempts`, the attempt sequence, and the leakage demonstration in §9.2 |
| `fct_order_economics` | **Keep** | §6, the entire CM model |
| **`fct_customer_state_at_session`** | **ADD** | **The single most important anti-leakage table.** See §1.2 |
| `truth_customer_latent` | **ADD (hidden)** | §9 ground-truth recovery; holds trust/liquidity/intent |
| `truth_order_probability` | **ADD (hidden)** | Stores `p_cod`, `p_rto_precheckout`, `p_rto_final` for recovery testing |
| `dim_experiment_assignment` | **Keep as shell** | Phase 1 §14K. Populated in Phase 10, created now so the schema is stable |
| ~~`checkout_events` + `payment_events` merged~~ | **Rejected** | Merging them forces a nullable-column-soup table. Different grains deserve different tables |
| ~~`fct_returns` (separate from RTO)~~ | **Rejected** | Phase 1 §1.2 explicitly scopes the project to RTO, not customer returns. Adding returns doubles the fulfilment model for zero narrative gain |

**Total: 15 tables** (12 analytical, 2 hidden truth, 1 experiment shell).

### 1.2 Why `fct_customer_state_at_session` exists — read this one carefully

Phase 1 §9.2 says `historical_rto_rate` must be *"strictly time-lagged (exclude current order)"* and Phase 1 §17 requires *"no post-outcome feature enters the risk model dataset."*

There are two ways to satisfy that:

1. **Compute it in SQL at analysis time** with window functions over `fct_order` ordered by date. Correct, but fragile: one analyst forgetting a `ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING` silently leaks the outcome, and the bug is invisible.
2. **Materialise the state as-of the session timestamp**, once, in the generator, which already knows the correct point-in-time values because it generated events in chronological order.

We do (2). One row per checkout session, holding every historical/behavioural feature **frozen at the instant the session started**. The risk-model input view reads from this table and from immutable order attributes — and *never* from `dim_customer`'s running aggregates.

> **Interview point:** "The leakage protection isn't a code review convention, it's a schema constraint. The features the model is allowed to see live in a different table from the outcomes, and that table is written by the generator at the moment the customer opened checkout. You physically cannot join your way to the answer."

This also gives us the temporal validation split Phase 1 §9.3 demands, for free.

### 1.3 ER relationship map

```
                        ┌──────────────────┐
                        │   dim_date       │
                        │   90 rows        │
                        └────────┬─────────┘
                                 │ (date spine, non-enforcing)
                                 │
┌────────────────┐               │              ┌────────────────┐
│ dim_geography  │               │              │  dim_seller    │
│    500         │               │              │    1,200       │
└───────┬────────┘               │              └───────┬────────┘
        │ 1                      │                      │ 1
        │                        │                      │
        │ N                      │                      │ N
┌───────▼────────┐               │              ┌───────▼────────┐
│  dim_customer  │               │              │  dim_product   │
│    55,000      │               │              │    8,000       │
└───────┬────────┘               │              └───────┬────────┘
        │ 1                      │                      │
        │                        │                      │
        │ N                      │                      │
┌───────▼─────────────────────────▼──────┐               │
│      fct_checkout_session               │               │
│           ~147,000                      │               │
└──┬────────────┬──────────────┬──────────┘               │
   │ 1          │ 1            │ 1                        │
   │ N          │ N            │ 0..1                     │
┌──▼──────────┐ │ ┌────────────▼──────────┐               │
│fct_checkout_│ │ │ fct_customer_state_   │               │
│   event     │ │ │    at_session         │               │
│  ~700,000   │ │ │      ~147,000 (1:1)   │               │
└─────────────┘ │ └───────────────────────┘               │
                │                                          │
   ┌────────────▼───────────┐                              │
   │  fct_payment_attempt   │                              │
   │       ~78,000          │                              │
   └────────────────────────┘                              │
                                                            │
        ┌───────────────────────────────────────────────────┘
        │ N
┌───────▼──────────────────────┐
│         fct_order            │◄──── 0..1 per session
│         100,000              │
└──┬──────────────────┬────────┘
   │ 1                │ 1
   │ N                │ 1
┌──▼──────────────┐ ┌─▼──────────────────────┐
│fct_delivery_    │ │ fct_order_economics    │
│    event        │ │      100,000 (1:1)     │
│   ~232,000      │ └────────────────────────┘
└─────────────────┘

  ┌─────────────────────────────────────────────┐
  │  HIDDEN SCHEMA  `truth`  — never joined by   │
  │  the analytical layer                        │
  │  ├── truth_customer_latent      (55,000)     │
  │  └── truth_order_probability   (~147,000)    │
  └─────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────┐
  │  dim_experiment_assignment  (shell, Phase 10)│
  └─────────────────────────────────────────────┘
```

### 1.4 Cardinality, stated precisely

| Relationship | Cardinality | Notes |
|---|---|---|
| `dim_geography` → `dim_customer` | 1 : N | Each customer has one home pincode cluster |
| `dim_geography` → `fct_order` (delivery geo) | 1 : N | ~97% of orders ship to the customer's home geo; ~3% elsewhere [A] |
| `dim_seller` → `dim_product` | 1 : N | Mean ~6.7 products per seller |
| `dim_customer` → `fct_checkout_session` | 1 : N | Mean 2.67 sessions per customer over 90 days |
| `fct_checkout_session` → `fct_checkout_event` | 1 : N | 2–6 events; every session has ≥1 (`checkout_started`) |
| `fct_checkout_session` → `fct_payment_attempt` | 1 : 0..N | Zero for COD-only sessions and for sessions that abandon before the payment page |
| `fct_checkout_session` → `fct_customer_state_at_session` | **1 : 1** | Enforced. Every session has exactly one state snapshot |
| `fct_checkout_session` → `fct_order` | **1 : 0..1** | This is the conversion event. ~68% of sessions produce an order [A] |
| `dim_product` → `fct_order` | 1 : N | **Single-line orders only** — see §1.5 |
| `fct_order` → `fct_delivery_event` | 1 : N | 1 event for pre-ship cancels; 2–5 for shipped orders |
| `fct_order` → `fct_order_economics` | **1 : 1** | Enforced |
| `dim_customer` → `truth_customer_latent` | **1 : 1** | Hidden |
| `fct_checkout_session` → `truth_order_probability` | **1 : 1** | Hidden. One row per *session*, not per order, because P(COD) and P(convert) are session-level |

### 1.5 One simplification I am making deliberately: single-line orders [A]

Real marketplace orders contain multiple line items. Modelling a cart forces a decision on how to price RTO when *part* of a shipment fails, and it multiplies the economics table by an items-per-order factor.

**Decision: one order = one product line, quantity 1–3.** `cart_size` (Phase 1 §9.2, "multi-variant ordering = optionality-seeking") is preserved as a **session-level attribute** — the number of items the customer had in cart — which drives the intent latent, without requiring a line-item fact table.

**Cost of this simplification:** we cannot analyse basket composition or partial-RTO. **Why it's acceptable:** neither appears in any Phase 1 hypothesis, metric, intervention, or the ₹165 Cr model. **Say this in `docs/05_limitations.md`.**

---

## 2. TABLE GRAIN — UNAMBIGUOUS STATEMENTS

No table proceeds to implementation without this line.

| Table | **One row represents…** | PK | Row count [A] |
|---|---|---|---|
| `dim_date` | one calendar day in the 90-day simulation window | `date_id` | 90 |
| `dim_geography` | one delivery geography cluster (a pincode group sharing tier, serviceability and courier reliability) | `geography_id` | 500 |
| `dim_seller` | one seller on the marketplace | `seller_id` | 1,200 |
| `dim_product` | one sellable product listing (one seller's SKU) | `product_id` | 8,000 |
| `dim_customer` | one customer account, with attributes **as of the end of the simulation window** | `customer_id` | 55,000 |
| `fct_checkout_session` | one checkout session — a customer opening checkout on a specific cart at a specific timestamp | `session_id` | ~147,000 |
| `fct_customer_state_at_session` | one checkout session, holding the customer's history **frozen at that session's start timestamp** | `session_id` | ~147,000 |
| `fct_checkout_event` | one funnel event within one checkout session (`checkout_started`, `address_completed`, `payment_page_reached`, `method_selected`, `order_placed`, `abandoned`) | `event_id` | ~700,000 |
| `fct_payment_attempt` | one attempt to charge one payment rail within one session | `payment_attempt_id` | ~78,000 |
| `fct_order` | one placed order (one product line) | `order_id` | 100,000 |
| `fct_delivery_event` | one fulfilment status event for one order (dispatched, out-for-delivery attempt N, delivered, RTO-initiated, RTO-received, cancelled) | `delivery_event_id` | ~232,000 |
| `fct_order_economics` | one order, with every cost line and the resulting contribution margin | `order_id` | 100,000 |
| `truth_customer_latent` | one customer's hidden generative traits | `customer_id` | 55,000 |
| `truth_order_probability` | one checkout session's hidden generated probabilities and latent scores | `session_id` | ~147,000 |
| `dim_experiment_assignment` | one customer's arm assignment for one experiment | `(experiment_id, customer_id)` | 0 (Phase 10) |

> **Grain trap to avoid at implementation time:** `truth_order_probability` is keyed on **session**, not order, because `p_cod` and `p_convert` exist for sessions that never became orders. `p_rto_*` is null for non-converting sessions. Getting this wrong produces a table that silently drops the abandoned sessions — which are the denominator of the north star.

---

## 3. DATA DICTIONARY

Columns marked **🔒 LEAKAGE** must never enter `vw_risk_model_input`. Columns marked **🔴 HIDDEN** live only in the `truth` schema.

### 3.1 `dim_date`

| Column | Type | Definition | Example | Gen/Der | Available | Used for |
|---|---|---|---|---|---|---|
| `date_id` | DATE | Calendar day | 2026-01-15 | Generated | Stage 1 | Spine |
| `day_index` | INT | 0–89 from window start | 14 | Derived | Stage 1 | Temporal split |
| `day_of_week` | SMALLINT | 1=Mon … 7=Sun | 4 | Derived | Stage 1 | Weekly seasonality |
| `is_weekend` | BOOLEAN | Sat/Sun | false | Derived | Stage 1 | Demand model |
| `day_of_month` | SMALLINT | 1–31 | 15 | Derived | Stage 1 | Salary-cycle effect |
| `is_month_end_window` | BOOLEAN | `day_of_month >= 26` | false | Derived | Stage 1 | **COD liquidity effect (H-liquidity)** |
| `is_salary_week` | BOOLEAN | `day_of_month <= 7` | false | Derived | Stage 1 | Liquidity effect |
| `demand_index` | NUMERIC | Multiplier on daily session volume | 1.14 | Generated | Stage 1 | Volume shaping |

### 3.2 `dim_geography`

| Column | Type | Definition | Example | Gen/Der | Available | Used for |
|---|---|---|---|---|---|---|
| `geography_id` | VARCHAR(10) | Surrogate pincode-cluster key | GEO_0143 | Generated | Stage 1 | PK |
| `pincode_prefix` | VARCHAR(3) | Synthetic pincode prefix | 560 | Generated | Stage 1 | Cosmetic realism |
| `city_name` | VARCHAR(40) | Synthetic city label | Nashik | Generated | Stage 1 | Dashboard labels |
| `state_name` | VARCHAR(40) | Synthetic state | Maharashtra | Generated | Stage 1 | Dashboard rollup |
| `geo_tier` | VARCHAR(8) | METRO / TIER1 / TIER2 / TIER3 | TIER2 | Generated [A] | Stage 1 | **Risk feature + fairness audit** |
| `serviceability_score` | NUMERIC(4,3) | 0–1; courier network density & address resolvability. Higher = easier | 0.612 | Generated [S] | Stage 1 | RTO driver |
| `courier_reliability_score` | NUMERIC(4,3) | 0–1; on-time performance of the dominant courier in this cluster | 0.744 | Generated [S] | Stage 1 | Post-dispatch shock |
| `base_delivery_days` | NUMERIC(3,1) | Mean promise for this cluster | 4.8 | Derived from tier [D] | Stage 1 | `estimated_delivery_days` |
| `cod_cultural_index` | NUMERIC(4,3) | 0–1; local COD normativity, *independent* of trust | 0.71 | Generated [S] | Stage 1 | COD driver — see note |
| `forward_freight_base` | NUMERIC(6,2) | ₹ base forward leg for this cluster | 85.00 | Derived from tier [D] | Stage 1 | Economics |

> **Note on `cod_cultural_index`:** this exists so geography can drive COD **without** being a pure trust proxy. If geography's only path to COD ran through distrust, the analysis would trivially conclude "Tier-3 = low trust," which is precisely the lazy conclusion Phase 1 §1.5 warns against. Having a separate cultural/normative channel means the analyst has to work to separate access, norms and trust — and may not fully succeed. That difficulty is realistic.

### 3.3 `dim_seller`

| Column | Type | Definition | Example | Gen/Der | Available | Used for |
|---|---|---|---|---|---|---|
| `seller_id` | VARCHAR(12) | PK | SLR_000412 | Generated | Stage 1 | PK |
| `seller_name` | VARCHAR(60) | Synthetic name | Vardhman Retail | Generated | Stage 1 | Labels |
| `seller_tenure_days` | INT | Days on platform at window start | 812 | Generated [S] | Stage 1 | Quality correlate |
| `seller_rating` | NUMERIC(3,2) | 1.0–5.0, displayed | 4.31 | Generated [S] | Stage 1 | **Risk + COD feature (H5)** |
| `seller_rating_count` | INT | Number of ratings | 1,844 | Generated [S] | Stage 1 | Rating confidence |
| `seller_sla_breach_rate` | NUMERIC(4,3) | Historical share of orders dispatched late | 0.083 | Generated [S] | Stage 1 | **RTO driver (Branch 3)** |
| `seller_cancellation_rate` | NUMERIC(4,3) | Historical seller-initiated cancels | 0.021 | Generated [S] | Stage 1 | Cancellation model |
| `fulfilment_model` | VARCHAR(12) | PLATFORM / SELLER_SHIP | PLATFORM | Generated [A] | Stage 1 | Freight attribution |
| `seller_tier` | VARCHAR(10) | GOLD/SILVER/BRONZE, derived from rating+SLA | SILVER | Derived [D] | Stage 1 | Segmentation lens |

### 3.4 `dim_product`

| Column | Type | Definition | Example | Gen/Der | Available | Used for |
|---|---|---|---|---|---|---|
| `product_id` | VARCHAR(12) | PK | PRD_003991 | Generated | Stage 1 | PK |
| `seller_id` | VARCHAR(12) | FK → `dim_seller` | SLR_000412 | Generated | Stage 1 | Join |
| `category` | VARCHAR(24) | FASHION / BEAUTY / HOME_KITCHEN / MOBILE_ACC / ELECTRONICS / GROCERY_FMCG | FASHION | Generated [A] | Stage 1 | **Risk + COD feature** |
| `sub_category` | VARCHAR(32) | e.g. Womens_Ethnic | Womens_Ethnic | Generated | Stage 1 | Drill-down |
| `list_price` | NUMERIC(10,2) | ₹ MRP before discount | 1,299.00 | Generated [S] | Stage 1 | GMV base |
| `product_rating` | NUMERIC(3,2) | 1.0–5.0 | 3.94 | Generated [S] | Stage 1 | **Trust proxy (H5)** |
| `review_count` | INT | Number of reviews | 87 | Generated [S] | Stage 1 | **Rating confidence (H5)** |
| `base_discount_pct` | NUMERIC(4,3) | Listing-level discount before promo | 0.075 | Generated [S] | Stage 1 | Discount cost |
| `cogs_ratio` | NUMERIC(4,3) | COGS ÷ net revenue for this product | 0.751 | Generated [S] | Stage 1 | Economics |
| `weight_band` | VARCHAR(8) | LIGHT/MEDIUM/HEAVY | LIGHT | Generated [S] | Stage 1 | Freight cost |
| `shrink_rate` | NUMERIC(4,3) | Share of value lost if returned unsold | 0.120 | Derived from category [A] | Stage 1 | RTO economics |
| `is_returnable` | BOOLEAN | Return policy eligibility | true | Generated [A] | Stage 1 | Trust signal |

### 3.5 `dim_customer`

> ⚠️ **Every `hist_*` column here is an END-OF-WINDOW aggregate and is therefore 🔒 LEAKAGE for the risk model.** The model reads the point-in-time versions from `fct_customer_state_at_session`. This is deliberate: the trap is present in the schema, and the firewall is the view.

| Column | Type | Definition | Example | Gen/Der | Available | Used for |
|---|---|---|---|---|---|---|
| `customer_id` | VARCHAR(12) | PK | CUS_0031882 | Generated | Stage 1 | PK, **randomisation unit** |
| `signup_date` | DATE | Account creation (may predate window) | 2024-11-03 | Generated [S] | Stage 1 | Tenure |
| `tenure_days_at_window_start` | INT | Days since signup on day 0 | 438 | Derived [D] | Stage 1 | Feature source |
| `home_geography_id` | VARCHAR(10) | FK → `dim_geography` | GEO_0143 | Generated | Stage 1 | Geo features |
| `age_bucket` | VARCHAR(10) | 18-24 / 25-34 / 35-44 / 45-54 / 55+ | 25-34 | Generated [S] | Stage 1 | Descriptive only |
| `acquisition_channel` | VARCHAR(16) | ORGANIC / PAID_SOCIAL / PAID_SEARCH / REFERRAL | PAID_SOCIAL | Generated [S] | Stage 1 | Descriptive; intent correlate |
| `has_saved_prepaid_instrument` | BOOLEAN | Tokenised card/UPI on file at window start | true | Generated [S] | Stage 1 | **Ceiling on prepaid shift (H7 limitation)** |
| `pre_window_orders` | INT | Orders completed before day 0 | 7 | Generated [S] | Stage 1 | History seed |
| `pre_window_delivered` | INT | Of those, delivered | 6 | Generated [S] | Stage 1 | History seed |
| `pre_window_rto_count` | INT | Of those, RTO'd | 1 | Generated [S] | Stage 1 | History seed |
| `pre_window_cod_orders` | INT | Of those, COD | 5 | Generated [S] | Stage 1 | History seed |
| `pre_window_prepaid_success` | INT | Successful prepaid payments | 2 | Generated [S] | Stage 1 | History seed |
| `pre_window_payment_failures` | INT | Failed prepaid attempts | 1 | Generated [S] | Stage 1 | H11 seed |
| `hist_orders_final` | INT | Total orders at window end | 10 | Derived [D] | **Stage 5** | 🔒 **LEAKAGE** |
| `hist_rto_rate_final` | NUMERIC(4,3) | RTO rate at window end | 0.200 | Derived [D] | **Stage 5** | 🔒 **LEAKAGE** |
| `hist_cod_share_final` | NUMERIC(4,3) | COD share at window end | 0.700 | Derived [D] | **Stage 5** | 🔒 **LEAKAGE** |
| `clv_estimate` | NUMERIC(10,2) | Realised contribution to date | 642.10 | Derived [D] | **Stage 5** | 🔒 **LEAKAGE from model**; ✅ allowed in fairness/value overlay (Phase 1 §9.2) |
| `analytics_segment` | VARCHAR(24) | Tenure × COD 3×3 label (Phase 1 §8.2) | HABITUAL_COD | Derived [D] | Stage 5 | Dashboard only |

### 3.6 `fct_checkout_session`

| Column | Type | Definition | Example | Gen/Der | Available | Used for |
|---|---|---|---|---|---|---|
| `session_id` | VARCHAR(16) | PK | SES_00104882 | Generated | Stage 2 | PK |
| `customer_id` | VARCHAR(12) | FK | CUS_0031882 | Generated | Stage 2 | Join |
| `session_start_ts` | TIMESTAMP | Checkout opened | 2026-01-15 20:41:03 | Generated [S] | Stage 2 | Ordering, temporal split |
| `date_id` | DATE | FK → `dim_date` | 2026-01-15 | Derived | Stage 2 | Join |
| `device_type` | VARCHAR(10) | ANDROID / IOS / WEB | ANDROID | Generated [S] | Stage 2 | Funnel diagnostic |
| `candidate_product_id` | VARCHAR(12) | FK → `dim_product`; the product in cart | PRD_003991 | Generated [S] | Stage 2 | Order attributes |
| `cart_size` | SMALLINT | Items in cart (1–5) | 2 | Generated [S] | Stage 2 | **Optionality proxy (risk feature)** |
| `cart_value` | NUMERIC(10,2) | ₹ pre-discount cart value | 1,299.00 | Derived [D] | Stage 2 | Order value |
| `delivery_geography_id` | VARCHAR(10) | FK; where it would ship | GEO_0143 | Generated [S] | Stage 2 | Geo features |
| `estimated_delivery_days` | SMALLINT | Promise shown at checkout | 5 | Generated [S] | **Stage 2** | ✅ **SAFE risk feature** |
| `address_completeness_score` | NUMERIC(4,3) | 0–1 quality of the entered address | 0.82 | Generated [S] | **Stage 2** | ✅ **SAFE; cheapest intervention** |
| `checkout_started` | BOOLEAN | Always true | true | Derived | Stage 2 | Funnel |
| `address_completed` | BOOLEAN | Reached address step completion | true | Generated [S] | Stage 2 | Funnel |
| `payment_page_reached` | BOOLEAN | Saw payment options | true | Generated [S] | Stage 2 | Funnel |
| `intended_payment_method` | VARCHAR(8) | COD / PREPAID — **first choice** | PREPAID | Generated [S] | Stage 2 | **H11 numerator** |
| `final_payment_method` | VARCHAR(8) | COD / PREPAID / NULL if abandoned | COD | Derived [D] | Stage 3 | Order attribute |
| `switched_to_cod_after_failure` | BOOLEAN | Intended prepaid, failed, ended COD | true | Derived [D] | Stage 3 | **H11 primary metric** |
| `payment_attempt_count` | SMALLINT | Number of rows in `fct_payment_attempt` | 2 | Derived [D] | Stage 3 | Funnel |
| `checkout_abandoned` | BOOLEAN | No order produced | false | Derived [D] | Stage 3 | Conversion |
| `abandon_step` | VARCHAR(24) | ADDRESS / PAYMENT_PAGE / PAYMENT_FAILURE / FEE_REVEAL / NULL | NULL | Derived [D] | Stage 3 | **Branch-5 diagnosis** |
| `order_id` | VARCHAR(14) | FK → `fct_order`, nullable | ORD_00088211 | Derived | Stage 3 | 0..1 join |

### 3.7 `fct_customer_state_at_session` — the leakage firewall

**Every column is point-in-time as of `session_start_ts`. All ✅ SAFE by construction.**

| Column | Type | Definition | Example | Gen/Der | Used for |
|---|---|---|---|---|---|
| `session_id` | VARCHAR(16) | PK, FK 1:1 | SES_00104882 | Generated | PK |
| `customer_id` | VARCHAR(12) | FK | CUS_0031882 | Generated | Join |
| `pit_tenure_days` | INT | Days since signup, as of session | 452 | Derived [D] | Risk feature |
| `pit_orders_placed` | INT | Orders placed before this session | 9 | Derived [D] | Risk feature |
| `pit_orders_resolved` | INT | Prior orders whose outcome had **resolved** before this session — the `pit_rto_rate_raw` denominator (decision A20) | 7 | Derived [D] | Risk feature |
| `pit_orders_delivered` | INT | Delivered **and resolved** before this session | 7 | Derived [D] | Risk feature |
| `pit_rto_count` | INT | RTOs **resolved** before this session | 1 | Derived [D] | Risk feature |
| `pit_rto_rate_raw` | NUMERIC(5,4) | `pit_rto_count / pit_orders_resolved` | 0.1250 | Derived [D] | Risk feature (unstable at low n) |
| `pit_rto_rate_shrunk` | NUMERIC(5,4) | Empirical-Bayes shrunk toward population mean, `k=8` [A] | 0.1573 | Derived [D] | **Preferred risk feature (Phase 1 §9.2)** |
| `pit_has_history` | BOOLEAN | `pit_orders_placed > 0`. The **missing-indicator** companion to the NULL point-in-time rates: decision A18 forbids imputing them, so a model uses this flag instead | true | Derived [D] | Risk feature |
| `pit_cod_orders` | INT | COD orders before this session | 6 | Derived [D] | Risk feature |
| `pit_cod_share` | NUMERIC(5,4) | `pit_cod_orders / pit_orders_placed` | 0.6667 | Derived [D] | **Habit feature (H2/H4)** |
| `pit_prepaid_success_count` | INT | Successful prepaid payments before | 2 | Derived [D] | **Trust-established feature** |
| `pit_payment_failure_count` | INT | Failed prepaid attempts before | 1 | Derived [D] | **H11 feature** |
| `pit_payment_failure_rate` | NUMERIC(5,4) | Failures ÷ prepaid attempts | 0.3333 | Derived [D] | H11 feature |
| `pit_days_since_last_order` | INT | Recency; NULL if none | 22 | Derived [D] | Risk feature |
| `pit_avg_order_value` | NUMERIC(10,2) | Mean OV of prior orders | 1,104.20 | Derived [D] | Risk feature |
| `pit_is_new_customer` | BOOLEAN | `pit_orders_delivered = 0` | false | Derived [D] | **Fairness gate (§8.4)** |
| `pit_has_clean_record` | BOOLEAN | `pit_orders_delivered >= 3 AND pit_rto_count = 0` | false | Derived [D] | **Fairness cap (Phase 1 §8.4 rule 1)** |
| `pit_risk_tier_rule_based` | VARCHAR(8) | LOW/MED/HIGH from the 3-rule baseline (Phase 1 §9.3) | MED | Derived [D] | Model floor + stratification |
| `order_risk_tier_rule_based` | VARCHAR(8) | **M2** baseline tier — post-selection, *knows* `payment_method`. Lives on `fct_order`. Hard-blocked from M1: applying an M1 score to the M2 threshold is a category error | HIGH | Derived [D] | Model floor (M2 only) |

> **Reconciliation invariant that must be tested:** for each customer, the *last* session's `pit_*` values plus that session's outcome must equal `dim_customer.hist_*_final`. If they don't, the point-in-time logic is broken. This is Validation Test **DQ-07**.

### 3.8 `fct_checkout_event`

| Column | Type | Definition | Example | Gen/Der | Available |
|---|---|---|---|---|---|
| `event_id` | BIGSERIAL | PK | 4488201 | Generated | Stage 2 |
| `session_id` | VARCHAR(16) | FK | SES_00104882 | Generated | Stage 2 |
| `event_seq` | SMALLINT | 1..N ordering within session | 3 | Generated | Stage 2 |
| `event_ts` | TIMESTAMP | Event time | 2026-01-15 20:42:11 | Generated [S] | Stage 2 |
| `event_name` | VARCHAR(28) | CHECKOUT_STARTED / ADDRESS_COMPLETED / PAYMENT_PAGE_REACHED / FEE_DISPLAYED / METHOD_SELECTED / PAYMENT_ATTEMPTED / METHOD_SWITCHED / ORDER_PLACED / ABANDONED | METHOD_SELECTED | Generated | Stage 2 |
| `event_detail` | JSONB | Payload (method chosen, fee shown, rail) | {"method":"PREPAID","rail":"UPI_COLLECT"} | Generated | Stage 2 |
| `seconds_since_session_start` | INT | Dwell | 68 | Derived [D] | Stage 2 |

### 3.9 `fct_payment_attempt`

| Column | Type | Definition | Example | Gen/Der | Available |
|---|---|---|---|---|---|
| `payment_attempt_id` | BIGSERIAL | PK | 771204 | Generated | Stage 3 |
| `session_id` | VARCHAR(16) | FK | SES_00104882 | Generated | Stage 3 |
| `attempt_seq` | SMALLINT | 1, 2, 3 within session | 2 | Generated | Stage 3 |
| `payment_rail` | VARCHAR(16) | UPI_INTENT / UPI_COLLECT / CARD / NETBANKING / WALLET | UPI_COLLECT | Generated [S] | Stage 3 |
| `attempt_ts` | TIMESTAMP | When attempted | 2026-01-15 20:43:02 | Generated [S] | Stage 3 |
| `attempt_amount` | NUMERIC(10,2) | ₹ charged | 1,199.00 | Derived [D] | Stage 3 |
| `payment_success` | BOOLEAN | Outcome | false | Generated [S] | Stage 3 |
| `failure_reason` | VARCHAR(32) | BANK_DECLINE / TIMEOUT / OTP_FAILURE / INSUFFICIENT_FUNDS / PSP_DOWNTIME / USER_CANCELLED / NULL | TIMEOUT | Generated [S] | Stage 3 |
| `is_retry` | BOOLEAN | `attempt_seq > 1` on same rail | true | Derived [D] | Stage 3 |
| `post_failure_action` | VARCHAR(20) | RETRY_SAME / SWITCH_RAIL / SWITCH_TO_COD / ABANDON / NULL | SWITCH_TO_COD | Generated [S] | Stage 3 |

### 3.10 `fct_order`

| Column | Type | Definition | Example | Gen/Der | Available | Used for |
|---|---|---|---|---|---|---|
| `order_id` | VARCHAR(14) | PK | ORD_00088211 | Generated | Stage 3 | PK |
| `session_id` | VARCHAR(16) | FK 1:1 | SES_00104882 | Generated | Stage 3 | Join |
| `customer_id` | VARCHAR(12) | FK | CUS_0031882 | Generated | Stage 3 | Join |
| `product_id` | VARCHAR(12) | FK | PRD_003991 | Generated | Stage 3 | Join |
| `seller_id` | VARCHAR(12) | FK (denormalised) | SLR_000412 | Derived | Stage 3 | Convenience |
| `delivery_geography_id` | VARCHAR(10) | FK | GEO_0143 | Generated | Stage 3 | Join |
| `order_ts` | TIMESTAMP | Placement time | 2026-01-15 20:44:19 | Generated | Stage 3 | Ordering |
| `order_date` | DATE | FK → `dim_date` | 2026-01-15 | Derived | Stage 3 | Join |
| `quantity` | SMALLINT | 1–3 | 1 | Generated [S] | Stage 3 | GMV |
| `gmv` | NUMERIC(10,2) | `list_price × quantity` | 1,299.00 | Derived [D] | Stage 3 | Reporting |
| `discount_amount` | NUMERIC(10,2) | Platform-funded ₹ | 103.92 | Derived [D] | Stage 3 | Economics |
| `discount_pct` | NUMERIC(5,4) | `discount_amount / gmv` | 0.0800 | Derived [D] | **Stage 2** | ✅ SAFE risk feature |
| `order_value` | NUMERIC(10,2) | `gmv − discount_amount` (the "₹1,000 AOV" quantity) | 1,195.08 | Derived [D] | **Stage 2** | ✅ SAFE risk feature |
| `shipping_fee_charged` | NUMERIC(6,2) | ₹ charged to customer | 0.00 | Derived [D] | Stage 2 | Net revenue |
| `cod_fee_charged` | NUMERIC(6,2) | ₹ COD convenience fee (0 in baseline) | 0.00 | Derived [D] | Stage 2 | **Intervention lever** |
| `payment_method` | VARCHAR(8) | COD / PREPAID | COD | Derived [D] | Stage 3 | **Primary cut** |
| `payment_rail` | VARCHAR(16) | Successful rail; NULL for COD | NULL | Derived [D] | Stage 3 | Diagnostics |
| `paid_via_switch` | BOOLEAN | COD after a failed prepaid attempt | true | Derived [D] | Stage 3 | **H11; RTO modifier (D5)** |
| `estimated_delivery_days` | SMALLINT | Promise at order | 5 | Derived from session | **Stage 2** | ✅ SAFE risk feature |
| `promised_delivery_date` | DATE | `order_date + estimated_delivery_days` | 2026-01-20 | Derived [D] | Stage 3 | SLA |
| `order_status` | VARCHAR(20) | PLACED / CANCELLED_PRESHIP / SHIPPED / DELIVERED / RTO | RTO | Derived [D] | **Stage 5** | 🔒 LEAKAGE |
| `is_cancelled_preship` | BOOLEAN | Cancelled before dispatch | false | Derived [D] | **Stage 3/4** | 🔒 LEAKAGE |
| `cancel_actor` | VARCHAR(10) | CUSTOMER / SELLER / SYSTEM / NULL | NULL | Generated [S] | Stage 4 | 🔒 LEAKAGE |
| `is_shipped` | BOOLEAN | Dispatched | true | Derived [D] | **Stage 4** | 🔒 LEAKAGE; **RTO-rate denominator** |
| `is_censored` | BOOLEAN | Shipped, but the 4–25 day outcome had not resolved by the window close (decision A10). Every outcome column is NULL. **Excluded from RTO-rate denominators and from annualisation** (L9) | false | Derived [D] | **Stage 4** | 🔒 LEAKAGE |
| `actual_delivery_days` | SMALLINT | Days order→terminal event; NULL if not delivered | NULL | Generated [S] | **Stage 5** | 🔒 **LEAKAGE** |
| `delivery_delay_days` | SMALLINT | `actual − estimated` | NULL | Derived [D] | **Stage 5** | 🔒 **LEAKAGE** |
| `delivery_attempts` | SMALLINT | Count of attempt events | 3 | Derived [D] | **Stage 5** | 🔒 **LEAKAGE** |
| `is_delivered` | BOOLEAN | Terminal success | false | Derived [D] | **Stage 5** | 🔒 LEAKAGE; target component |
| `rto_flag` | BOOLEAN | **The outcome** | true | Generated [S] | **Stage 5** | 🔒 **TARGET** |
| `rto_reason` | VARCHAR(40) | See §11 | CUSTOMER_REFUSED | Generated [S] | **Stage 5** | 🔒 **LEAKAGE**; avoidability waterfall only |
| `rto_reason_class` | VARCHAR(16) | ADDRESSABLE / STRUCTURAL | ADDRESSABLE | Derived [D] | **Stage 5** | 🔒 LEAKAGE; §7 waterfall |
| `outcome_resolved_date` | DATE | Date the outcome settled (delivery or RTO-received) | 2026-02-04 | Derived [D] | Stage 5 | **Censoring / maturation analysis** |

### 3.11 `fct_delivery_event`

| Column | Type | Definition | Example | Gen/Der | Available |
|---|---|---|---|---|---|
| `delivery_event_id` | BIGSERIAL | PK | 1180422 | Generated | Stage 4 |
| `order_id` | VARCHAR(14) | FK | ORD_00088211 | Generated | Stage 4 |
| `event_seq` | SMALLINT | Ordering | 3 | Generated | Stage 4 |
| `event_ts` | TIMESTAMP | Event time | 2026-01-21 11:02:00 | Generated [S] | Stage 4 |
| `event_name` | VARCHAR(28) | ORDER_PLACED / CANCELLED_PRESHIP / DISPATCHED / IN_TRANSIT / OUT_FOR_DELIVERY / DELIVERY_ATTEMPT_FAILED / DELIVERED / RTO_INITIATED / RTO_RECEIVED | DELIVERY_ATTEMPT_FAILED | Generated | Stage 4 |
| `attempt_number` | SMALLINT | 1..3 for attempt events, else NULL | 2 | Generated | Stage 4 |
| `ndr_code` | VARCHAR(32) | Non-delivery report code on failed attempts | CUSTOMER_UNREACHABLE | Generated [S] | Stage 5 |
| `attempt_delay_days` | SMALLINT | Days between the promised date and the **first** delivery attempt. Decision A8: the Stage-2 shock input, distinct from `fct_order.delivery_delay_days`. NULL on non-attempt events. **Hard-blocked from every model** | 3 | Generated [S] | Stage 4 | 🔒 LEAKAGE |
| `courier_partner` | VARCHAR(20) | Synthetic courier | Bluewing | Generated [S] | Stage 4 |

### 3.12 `fct_order_economics`

All ₹, one row per order. **All 🔒 LEAKAGE for the risk model** (realised CM depends on the outcome), but the *expected* CM is computable pre-checkout and is built in Phase 8, not here.

| Column | Type | Definition | Incurred when | Gen/Der |
|---|---|---|---|---|
| `order_id` | VARCHAR(14) | PK/FK | — | Generated |
| `gmv` | NUMERIC(10,2) | List × qty | Always | Derived [D] |
| `discount_cost` | NUMERIC(10,2) | Platform-funded discount | On delivery (promo) / on order (incentive) | Derived [D] |
| `shipping_fee_revenue` | NUMERIC(6,2) | Charged to customer | On delivery only | Derived [D] |
| `cod_fee_revenue` | NUMERIC(6,2) | COD fee collected | On delivery only | Derived [D] |
| `net_revenue` | NUMERIC(10,2) | `gmv − discount + shipping_fee + cod_fee`, **₹0 unless delivered** | Delivery | Derived [D] |
| `cogs` | NUMERIC(10,2) | `cogs_ratio × net_revenue_if_delivered`; ₹0 on RTO (goods return) | Delivery | Derived [D] |
| `cogs_value` | NUMERIC(10,2) | **Counterfactual** goods value, as if delivered (decision A23). Needed because on an RTO `net_revenue` and therefore `cogs` are zero, yet shrink and working-capital costs remain proportional to goods value | — | Derived [D] |
| `forward_shipping_cost` | NUMERIC(6,2) | Outbound freight | **On dispatch — always** | Derived [D] |
| `reverse_shipping_cost` | NUMERIC(6,2) | Return freight | RTO only | Derived [D] |
| `packaging_cost` | NUMERIC(6,2) | Material + pick/pack | On dispatch — always | Derived [D] |
| `payment_processing_fee` | NUMERIC(6,2) | PG fee | Prepaid, on payment | Derived [D] |
| `cod_handling_cost` | NUMERIC(6,2) | `rate × value + fixed` | COD, on successful collection | Derived [D] |
| `cod_failed_attempt_cost` | NUMERIC(6,2) | Failed collection attempt fee | COD RTO only | Derived [D] |
| `reverse_handling_cost` | NUMERIC(6,2) | Re-inward, QC, restock | RTO only | Derived [D] |
| `shrink_cost` | NUMERIC(8,2) | `shrink_rate × cogs_value` | RTO only | Derived [D] |
| `support_ndr_cost` | NUMERIC(6,2) | NDR handling, calls | RTO (high) / delivered (low) | Derived [D] |
| `working_capital_cost` | NUMERIC(6,2) | `cogs × rate × days/365` | RTO only | Derived [D] |
| `ops_allocation_cost` | NUMERIC(6,2) | Per-delivered-order ops | Delivery only | Derived [D] |
| `total_variable_cost` | NUMERIC(10,2) | Sum of all cost lines | — | Derived [D] |
| `contribution_margin` | NUMERIC(10,2) | `net_revenue − total_variable_cost` | — | Derived [D] |
| `counterfactual_cm_if_delivered` | NUMERIC(10,2) | What the CM **would have been** had this order delivered. Decision A42: ~5% of RTO orders were unprofitable anyway, so Phase 3's intervention set needs a *don't take this order* tier, not only payment and address levers (L10) | — | Derived [D] |
| `rto_cash_loss` | NUMERIC(10,2) | Cash cost lines on RTO orders; ₹0 otherwise | — | Derived [D] |
| `foregone_cm` | NUMERIC(10,2) | Counterfactual CM had it delivered; ₹0 if delivered | — | Derived [D] |
| `rto_economic_cost` | NUMERIC(10,2) | `rto_cash_loss + foregone_cm` | — | Derived [D] |

### 3.13 Hidden truth tables

`truth_customer_latent` — 🔴 **HIDDEN**

| Column | Type | Definition |
|---|---|---|
| `customer_id` | VARCHAR(12) | PK |
| `latent_trust` | NUMERIC(6,4) | z-scored platform-trust propensity. High → prepaid, low RTO |
| `latent_liquidity` | NUMERIC(6,4) | z-scored cash/credit access. High → prepaid, low cash-failure RTO |
| `latent_intent` | NUMERIC(6,4) | z-scored **low-commitment** trait. High → COD *and* high RTO. **The core confounder** |
| `latent_price_sensitivity` | NUMERIC(6,4) | z-scored; drives discount-seeking |
| `true_cod_propensity` | NUMERIC(5,4) | Customer-level mean P(COD) across their sessions |

`truth_order_probability` — 🔴 **HIDDEN**, one row per session

| Column | Type | Definition |
|---|---|---|
| `session_id` | VARCHAR(16) | PK |
| `p_convert` | NUMERIC(6,5) | Generated session→order probability |
| `p_cod_intent` | NUMERIC(6,5) | P(customer's *first choice* is COD) |
| `logit_cod_components` | JSONB | Every additive term in the COD logit, by name. Populated for the A45 audit sample only — see `components_populated` |
| `p_rto_precheckout` | NUMERIC(6,5) | RTO probability from Stage-1/2 features only — **the theoretical ceiling for any risk model** |
| `p_rto_final` | NUMERIC(6,5) | After the post-dispatch shock — what the Bernoulli draw actually used |
| `logit_rto_components` | JSONB | Every additive term in the RTO logit, by name. Stage-1 terms bare; the four post-dispatch terms prefixed `shock.`. NULL where the session produced no order |
| `post_dispatch_shock` | NUMERIC(6,4) | The logit-scale shock added at Stage 4 |
| `components_populated` | BOOLEAN | Whether `logit_*_components` carry a trace for this session. TRUE for the A45 stratified audit sample (~2,000 sessions); FALSE otherwise. See limitation **L12** |

> **Decision A45 — the trace columns are a documented sample, not a full column.**
> `logit_cod_components` and `logit_rto_components` exist to make GT-01
> auditable: open one order and read every additive term that produced its
> probability. That is a lookup, never a scan, so ~2,000 stratified sessions are
> traced rather than all 155,000 — full population costs ~190 MB of JSONB for a
> query nobody runs in bulk. `components_populated` is what makes the remaining
> NULLs a *stated* absence rather than an ambiguous one, and two CHECK
> constraints tie it to the data so it cannot drift. Query these columns with
> `WHERE components_populated`, or expect ~98.7% NULL.

> **Implementation rule:** these two tables live in PostgreSQL schema `truth`, and the analytical role (`analyst`) is granted **no privileges** on that schema. The validation harness runs as a separate role. This makes leakage a permissions error, not a discipline problem.

---

## 4. DATA AVAILABILITY TIMELINE

### 4.1 The five stages

| Stage | Moment | What exists | Can the risk model use it? |
|---|---|---|---|
| **Stage 1** | Before checkout opens | Customer history, product, seller, geography, calendar | ✅ **YES** |
| **Stage 2** | During checkout, **before payment-method selection** | Cart, order value, discount, delivery promise, address quality, device | ✅ **YES — this is the decision moment** |
| **Stage 3** | After method selection / payment / order placement | Payment method, rail, attempt outcomes, switch flag, order id | ⚠️ **Partly** — see §4.3 |
| **Stage 4** | After shipment | Dispatch timestamp, courier, in-transit events | ❌ **NO** |
| **Stage 5** | After delivery / RTO resolution | `rto_flag`, `actual_delivery_days`, `delivery_attempts`, `rto_reason`, realised CM | ❌ **NO — TARGET & LEAKAGE** |

**The decision boundary is the end of Stage 2.** Phase 1 §9.1 says the test is *"known before the customer selects a payment method"* — not before delivery, not before shipment. Everything the model sees must exist at the instant the payment options render, because that is when the intervention fires.

### 4.2 Complete feature classification

**✅ SAFE — Stage 1**

`pit_tenure_days` · `pit_orders_placed` · `pit_orders_delivered` · `pit_rto_count` · `pit_rto_rate_shrunk` · `pit_cod_share` · `pit_prepaid_success_count` · `pit_payment_failure_rate` · `pit_days_since_last_order` · `pit_avg_order_value` · `pit_is_new_customer` · `has_saved_prepaid_instrument` · `age_bucket` · `acquisition_channel` · `geo_tier` · `serviceability_score` · `courier_reliability_score` · `cod_cultural_index` · `category` · `list_price` · `product_rating` · `review_count` · `seller_rating` · `seller_rating_count` · `seller_sla_breach_rate` · `is_returnable` · `day_of_week` · `is_month_end_window`

**✅ SAFE — Stage 2**

`cart_size` · `cart_value` · `order_value` · `discount_pct` · `estimated_delivery_days` · `address_completeness_score` · `device_type` · `delivery_geography_id` · `hour_of_day`

**⚠️ CONDITIONAL — Stage 3**

| Feature | Verdict | Reasoning |
|---|---|---|
| `payment_method` | ✅ **Usable, with care** | It exists *after* the decision the model informs. Two legitimate models: a **pre-selection** model (excludes it) that decides which options to show, and a **post-selection** model (includes it) that decides whether to apply a fee once COD is chosen. **Build both. Report both.** Conflating them is a real modelling error, not a leakage error |
| `paid_via_switch` | ✅ Usable in the post-selection model | Known at order confirmation, before shipment |
| `payment_attempt_count` | ✅ Usable in the post-selection model | Same |
| `cod_fee_charged` | ⚠️ Treatment variable, not a feature | It's the *intervention*. Using it as a predictor is circular |

**❌ LEAKAGE — Stage 4/5. Hard-blocked.**

| Feature | Why it leaks |
|---|---|
| `delivery_attempts` | A failed attempt *is* the RTO process. Near-perfect predictor, zero decision value |
| `actual_delivery_days` / `delivery_delay_days` | Only known after the outcome. Phase 1 H6 uses these **for diagnosis only** |
| `rto_reason` / `rto_reason_class` | Post-outcome by definition. Used only in the §7 avoidability waterfall |
| `order_status`, `is_shipped`, `is_delivered`, `is_cancelled_preship` | Encode the outcome |
| `ndr_code` | Post-attempt |
| `contribution_margin`, `rto_cash_loss`, `foregone_cm` | Realised economics depend on the outcome |
| `dim_customer.hist_*_final`, `clv_estimate`, `analytics_segment` | **End-of-window aggregates that include the current order.** The subtlest trap in the schema — they *look* like customer attributes |

### 4.3 The two-model discipline (a Phase 1 gap this document closes)

Phase 1 §9 describes "the risk model" as one artefact. Implementation forces the distinction:

| Model | Runs at | Features | Answers | Drives |
|---|---|---|---|---|
| **M1 — Pre-selection risk** | Payment page renders | Stage 1 + Stage 2 only | "How risky is this order *before* we know how they'll pay?" | Which options to show, which trust signals, whether to offer an incentive (Interventions C, E, A) |
| **M2 — Post-selection risk** | COD tapped | Stage 1 + 2 + `payment_method`, `paid_via_switch` | "Given they chose COD, how risky is this *specific* COD order?" | Whether the ₹39 fee or partial payment applies (Interventions B, D) |

The **₹25.7% break-even threshold from Phase 1 §6.6 belongs to M2**, because it is derived from the expected value of a *COD* order. Applying an M1 score to an M2 threshold is a category error that would mis-tier a large share of traffic. Flag this in the PRD.

### 4.4 The leakage firewall in code

```sql
-- The ONLY table the risk model training script is permitted to read from.
CREATE VIEW analytics.vw_risk_model_input AS
SELECT
    s.session_id, s.customer_id, s.session_start_ts,
    st.pit_tenure_days, st.pit_orders_delivered, st.pit_rto_rate_shrunk,
    st.pit_cod_share, st.pit_prepaid_success_count, st.pit_payment_failure_rate,
    st.pit_days_since_last_order, st.pit_is_new_customer, st.pit_has_clean_record,
    s.cart_size, s.estimated_delivery_days, s.address_completeness_score,
    o.order_value, o.discount_pct,
    p.category, p.product_rating, p.review_count,
    sl.seller_rating, sl.seller_sla_breach_rate,
    g.geo_tier, g.serviceability_score, g.cod_cultural_index,
    c.has_saved_prepaid_instrument,
    -- M2-only columns; M1 training must project these away
    o.payment_method, o.paid_via_switch,
    -- TARGET
    o.rto_flag, o.is_shipped
FROM fct_checkout_session s
JOIN fct_customer_state_at_session st USING (session_id)
JOIN fct_order o          USING (session_id)
JOIN dim_product p        ON o.product_id  = p.product_id
JOIN dim_seller  sl       ON o.seller_id   = sl.seller_id
JOIN dim_geography g      ON o.delivery_geography_id = g.geography_id
JOIN dim_customer c       ON o.customer_id = c.customer_id
WHERE o.is_shipped = TRUE;   -- RTO-rate denominator per Phase 1 §5.3
```

Note what this view does **not** select: no `dim_customer.hist_*`, no delivery events, no economics, no `rto_reason`. Validation test **LK-01** asserts that the view's column list is a subset of the approved safe-feature registry in `params.yaml`.

---

## 5. THE DATA-GENERATING PROCESS

### 5.1 Generation sequence and why the order is forced

```
   ┌──────────────────────────────────────────────────────┐
 0 │  params.yaml  +  master seed  → spawned substreams    │
   └───────────────────────┬──────────────────────────────┘
                           │
 1 │  dim_date          ── calendar spine, demand index, month-end flags
                           │  (needed: sessions are placed on days)
 2 │  dim_geography     ── tier, serviceability, courier reliability, cod_cultural_index
                           │  (needed: customers live somewhere; freight depends on it)
 3 │  dim_seller        ── rating, SLA breach, fulfilment model
                           │  (needed: products belong to sellers)
 4 │  dim_product       ── category, price, rating, reviews, shrink, weight
                           │  (needed: carts contain products)
 5 │  dim_customer      ── demographics, home geo, saved instrument
   │  truth_customer_latent ── trust / liquidity / intent / price-sensitivity
                           │  ★ LATENTS MUST EXIST BEFORE HISTORY ★
 6 │  customer pre-window history ── orders, RTOs, COD share, prepaid successes,
   │                                  payment failures, GENERATED FROM THE LATENTS
                           │  ★ this is what creates the confounding ★
 7 │  fct_checkout_session ── who shops, when, what's in the cart, delivery promise,
   │                           address quality
                           │
 8 │  fct_customer_state_at_session ── freeze history as of each session (chronological)
                           │  ★ must run in strict timestamp order ★
 9 │  COD intent           ── P(COD | latents, history, order, product, seller, geo)
                           │
10 │  fct_payment_attempt  ── prepaid attempts, failures, retries, switches  → H11
                           │
11 │  session outcome      ── convert / abandon + abandon_step
                           │
12 │  fct_order            ── materialise converted sessions
                           │
13 │  pre-ship cancellation ── customer / seller / system
                           │
14 │  P(RTO) stage 1       ── pre-checkout latent score  (→ truth.p_rto_precheckout)
                           │
15 │  fct_delivery_event   ── dispatch, courier assignment, actual transit
   │  + post-dispatch shock ── courier reliability, realised delay
                           │  (→ truth.p_rto_final)
16 │  RTO Bernoulli draw   ── rto_flag; then rto_reason conditional on drivers
                           │
17 │  fct_order_economics  ── every cost line, conditional on the outcome
                           │
18 │  dim_customer roll-up ── hist_*_final, clv_estimate, analytics_segment
                           │
19 │  validation harness   ── 62 tests → PASS/FAIL report
```

### 5.2 Why each dependency is non-negotiable

| Step order | Why it cannot be reversed |
|---|---|
| **Latents (5) before history (6)** | If history were generated independently and latents fitted afterwards, the confounding would be an artefact of the fit rather than a causal structure. The whole project's payoff — naive vs adjusted — requires the latents to be *upstream* of both COD choice and RTO |
| **History (6) before sessions (7)** | Point-in-time features on day 0 need a pre-window baseline. Starting every customer at zero orders would make 100% of the population "new" and destroy H2/H3 |
| **State snapshot (8) in strict chronological order** | Session #2 for a customer must see the outcome of session #1 *only if it had resolved* by then. Resolution takes 4–25 days. Processing out of order silently leaks |
| **COD intent (9) before payment attempts (10)** | H11's numerator is "COD orders whose *intent* was prepaid." Without an intent variable recorded before the attempt, the question is unanswerable |
| **Payment attempts (10) before session outcome (11)** | Some abandonment is *caused* by payment failure. If conversion were drawn first, `abandon_step = PAYMENT_FAILURE` would be fiction |
| **Pre-ship cancel (13) before RTO (14–16)** | Phase 1 §5.3: RTO rate's denominator is **shipped** orders. Cancelled orders must be removed *before* the RTO draw, or the rate is wrong and the "cancellation gaming" trap can't be demonstrated |
| **Pre-checkout score (14) recorded before the shock (15)** | `p_rto_precheckout` is the model's theoretical ceiling. It must be stored *before* Stage-4 information contaminates it |
| **Delivery (15–16) before economics (17)** | Costs are outcome-conditional. §6.3 of Phase 1 is a matrix over outcomes |
| **Everything before roll-up (18)** | `hist_*_final` are end-state aggregates — generating them early would make them available mid-window, which is the leakage we are designing against |

### 5.3 One structural point worth internalising

The generator writes **two parallel realities**:

- the **analytical schema**, which is what a real analyst would see, and
- the **truth schema**, which is what a real analyst never sees.

Phase 5's headline demonstration is measuring the distance between them. Do not let convenience collapse the two.

---

## 6. CAUSAL / BEHAVIOURAL STRUCTURE

### 6.1 The latent variables — the engine of the whole simulation

Four customer-level traits, drawn at customer creation, **never exposed**:

| Latent | Distribution | Interpretation | Drives COD | Drives RTO | Observable proxies the analyst *does* get |
|---|---|---|---|---|---|
| `latent_trust` | N(0,1) | Belief that money sent online will result in the right goods arriving | ↓ strong | ↓ moderate | `pit_prepaid_success_count`, `has_saved_prepaid_instrument`, seller/product ratings of what they buy |
| `latent_liquidity` | N(0,1), correlated +0.35 with geo tier | Cash/credit access and budgeting style | ↓ strong | ↓ moderate-strong (cash-at-door failures) | `pit_avg_order_value`, `geo_tier`, `age_bucket` — all weak |
| `latent_intent` | N(0,1) | **Low-commitment / free-optionality trait.** High = "order it, decide later" | ↑ moderate | ↑ **strong** | `pit_rto_rate_shrunk`, `cart_size`, `discount_pct` — partial |
| `latent_price_sensitivity` | N(0,1), correlated +0.30 with `latent_liquidity`⁻ | Deal-seeking | ↑ weak | ↑ weak | `discount_pct` |

> **`latent_intent` is the single most important object in this project.** It is the unobservable that makes COD *look* more causal than it is, it is the reason the adjusted effect is smaller than the naive effect, and it is what the partial-payment intervention (Phase 1 §10 D) is designed to screen on. Everything Phase 1 §13 Q1 promises depends on this variable existing and being genuinely unobservable.

**Correlation structure among latents [A]:** mild, not orthogonal — `corr(trust, liquidity) = +0.25`, `corr(intent, liquidity) = −0.20`, `corr(intent, trust) = −0.15`. Perfectly independent latents would be unrealistic and would make the confounding too easy to disentangle.

### 6.2 Relationship strength register

Strength is stated on the logit scale as the change in log-odds across the **interquartile range** of the driver, so the labels mean something comparable.

| # | Relationship | → COD | → RTO | Strength | Mechanism |
|---|---|---|---|---|---|
| R1 | `latent_intent` ↑ | ↑ | ↑↑ | **Strong (RTO)** | Free optionality; the core confounder |
| R2 | `latent_trust` ↓ | ↑↑ | ↑ | **Strong (COD)** | Won't send money first |
| R3 | `latent_liquidity` ↓ | ↑↑ | ↑↑ | **Strong** | No instrument; no cash at door |
| R4 | `pit_cod_share` ↑ | ↑↑↑ | — (indirect) | **Strongest observable → COD** | Habit / revealed preference |
| R5 | `pit_rto_rate_shrunk` ↑ | — | ↑↑↑ | **Strongest observable → RTO** | Persistent trait (H3) |
| R6 | `pit_is_new_customer` | ↑↑ | ↑ | **Moderate-strong** | No anchor, no instrument (H2) |
| R7 | `pit_prepaid_success_count` ↑ | ↓↓ | ↓ | **Moderate-strong** | Trust demonstrated |
| R8 | `geo_tier` T2/T3 | ↑↑ | ↑ | **Moderate** | Norms + serviceability (two channels) |
| R9 | `serviceability_score` ↓ | — | ↑↑ | **Moderate** | Address resolution failure |
| R10 | `seller_rating` ↓ | ↑ | ↑ | **Moderate** | Trust proxy + real fulfilment quality |
| R11 | `seller_sla_breach_rate` ↑ | — | ↑↑ | **Moderate** | Late dispatch → intent decay |
| R12 | `order_value` ↑ | ↑ then ↓ (**inverted-U**) | ↑ | **Weak-moderate** | Inspect-before-pay, then affluence flips it (H4) |
| R13 | `product_rating` ↓ | ↑ | ↑ | **Weak** | Product-level uncertainty (H5) |
| R14 | `review_count` ↓ | ↑ | ~0 | **Weak** | Thin signal |
| R15 | `estimated_delivery_days` ↑ | ↑ | ↑ | **Weak-moderate** | Intent decay window (H6) |
| R16 | `delivery_delay_days` ↑ (actual − promise) | n/a | ↑↑ | **Moderate — Stage 4 only** | Broken promise. **Not available to the model** — this is H6's punchline |
| R17 | `address_completeness_score` ↓ | — | ↑↑ | **Strong** | Direct mechanism; cheapest fix |
| R18 | `category` | Fashion ↑, Electronics ↓ | Fashion ↑, Grocery ↓ | **Weak-moderate** | Fit/size uncertainty |
| R19 | `discount_pct` ↑ | ↑ | ↑ | **Weak** | Deal-seeking / low commitment |
| R20 | `is_month_end_window` × COD | ↑ (small) | ↑↑ | **Moderate, interaction only** | Salary-cycle liquidity |
| R21 | `cart_size` ≥ 3 | ↑ | ↑ | **Weak-moderate** | Multi-variant optionality |
| R22 | payment failure in session | ↑↑↑ (mechanical) | ↓ (as COD orders go) | **Strong → COD, protective → RTO** | H11 + deviation **D5** |
| R23 | `cod_cultural_index` ↑ | ↑↑ | ~0 | **Moderate → COD only** | Norms channel, deliberately decoupled from trust |
| R24 | `courier_reliability_score` ↓ | — | ↑↑ | **Moderate — Stage 4** | Post-dispatch shock component |

**Deliberately excluded relationships:**
- **Trust signals at checkout → prepaid.** Phase 1 H7 says this is untestable observationally because nothing varies. The generator therefore plants **no** trust-signal variation in the observational data. The A/B simulation in Phase 10 injects it as an experimental treatment. Generating it here would let the analyst "discover" an effect that only an experiment could reveal — which would be cheating.
- **Competitor dynamics.** Phase 1 Open Question #5 resolved: out of scope, noted in limitations.

---

## 7. THE COD PROBABILITY MODEL

### 7.1 Formulation

COD choice is modelled in two steps, which matters for H11:

```
STEP A — INTENT (what the customer wanted)
  logit P(COD_intent) = β₀
                      + β_latent · [trust, liquidity, intent, price_sens]
                      + β_hist   · [cod_share, prepaid_success, new, tenure, failures]
                      + β_geo    · [tier, cod_cultural_index]
                      + β_order  · [log(order_value), log(order_value)², discount_pct, cart_size]
                      + β_trustproxy · [seller_rating, product_rating, review_count]
                      + β_logi   · [estimated_delivery_days]
                      + β_cat    · [category]
                      + β_time   · [month_end, weekend]
                      + ε,   ε ~ N(0, 0.35)          ← individual-session shock

  COD_intent ~ Bernoulli( logistic( · ) )

STEP B — REALISATION (what actually happened)
  if COD_intent = 1                       → final_payment_method = COD
  if COD_intent = 0                       → prepaid attempt sequence (§10)
        ├─ success                        → PREPAID
        ├─ failure → switch               → COD, paid_via_switch = TRUE
        └─ failure → abandon              → no order

  OBSERVED COD share = P(COD_intent) + P(prepaid intent) × P(forced to COD)
```

> **This two-step structure is the whole reason H11 is answerable.** A single-step model that just draws COD-or-prepaid cannot distinguish preference from coercion.

### 7.2 Coefficient specification [A]

All on the logit scale. `z(·)` = standardised. Direction and magnitude are chosen to reproduce Phase 1's §4 pre-registered priors — which means the priors are *targets the data can miss*, not guarantees.

| Term | Coefficient | Direction | Effect size across IQR | Why it exists |
|---|---:|---|---|---|
| **Intercept β₀** | *solved* ≈ **+0.30** | — | — | Bisection-calibrated (§7.3) |
| `z(latent_trust)` | **−0.55** | ↓ | −0.74 log-odds | Core trust channel |
| `z(latent_liquidity)` | **−0.45** | ↓ | −0.61 | Access channel |
| `z(latent_intent)` | **+0.40** | ↑ | +0.54 | Optionality channel |
| `z(latent_price_sensitivity)` | **+0.12** | ↑ | +0.16 | Weak |
| `pit_cod_share` (0→1) | **+2.20** | ↑↑ | +1.10 (IQR ≈0.5) | **Habit — strongest observable.** H2/analytics segment |
| `log1p(pit_prepaid_success_count)` | **−0.35** | ↓ | −0.38 | Trust demonstrated (Phase 1: "highest weight protect signal") |
| `pit_is_new_customer` (0/1) | **+0.70** | ↑ | — | H2. Yields ≈ +14pp for new vs established, inside the 12–18pp prior |
| `log1p(pit_orders_delivered)` | **−0.18** | ↓ | −0.30 | Tenure gradient |
| `pit_payment_failure_rate` (0→1) | **+1.10** | ↑ | +0.33 | H11 at customer level |
| `has_saved_prepaid_instrument` | **−0.60** | ↓ | — | Mechanical availability |
| `geo_tier` = METRO / TIER1 / TIER2 / TIER3 | **−0.45 / −0.15 / +0.25 / +0.55** | — | 1.00 spread | Two-channel: norms + access |
| `z(cod_cultural_index)` | **+0.30** | ↑ | +0.40 | Norms, decoupled from trust |
| `log(order_value/1000)` | **+0.28** | ↑ | — | H4 rising limb |
| `log(order_value/1000)²` | **−0.12** | ↓ | — | **H4 inverted-U.** Peak ≈ ₹3,200; declines above |
| `seller_rating` − 4.30 (per point) | **−0.35** | ↓ | −0.14 | H5 |
| `product_rating` − 4.20 (per point) | **−0.30** | ↓ | −0.15 | H5 |
| `log1p(review_count)` centred | **−0.12** | ↓ | −0.19 | H5 confidence |
| `estimated_delivery_days` − 4 (per day) | **+0.09** | ↑ | +0.27 | H6 |
| `discount_pct` − 0.08 (per 10pp) | **+0.15** | ↑ | +0.11 | Deal-seeking |
| `cart_size` ≥ 3 | **+0.22** | ↑ | — | Optionality |
| `category` FASH/BEAU/HOME/MOBACC/ELEC/GROC | **+0.30 / +0.10 / +0.05 / +0.15 / −0.35 / −0.10** | — | 0.65 spread | Fit uncertainty |
| `is_month_end_window` | **+0.14** | ↑ | — | Liquidity timing |
| `ε` | N(0, 0.35) | — | — | Non-determinism |

### 7.3 Calibration to 62% [A→D]

**The target is on the observed final share, not on intent.** Because §10 pushes some prepaid-intent orders into COD, `P(COD_intent)` must be calibrated *below* 62%.

```
Algorithm CALIBRATE_COD:
  1. Build the full design matrix for all ~147,000 sessions
  2. Bisect on β₀ over [−2.0, +2.0]:
       a. compute p_cod_intent for every session
       b. run the full realisation pipeline (§10) WITHOUT resampling other randomness
       c. compute observed COD share among resulting orders
       d. adjust β₀
  3. Stop when |observed_cod_share − 0.620| < 0.004
  4. Write the solved β₀ back into params.yaml as `cod_model.intercept_solved`
     and record `calibration_run_id` + timestamp
```

Expected outcome: `P(COD_intent)` mean ≈ **0.585**, observed COD share ≈ **0.620**, with ≈3.5pp of the final share arriving via the failure-switch path.

**Two rules that make this honest:**
1. **62% is never assigned.** No session is forced to COD. The share *emerges* from the population's latents and observables. If the customer mix shifts (change `geo_tier` weights, say), the share moves — which is correct behaviour and exactly what makes the sensitivity analysis meaningful.
2. **Only the intercept is calibrated.** Every slope is fixed a priori from the Phase 1 hypothesis priors. Tuning slopes to hit the target would be reverse-engineering the conclusion. **Validation test CAL-09 asserts that no slope coefficient changed between the params file and the run manifest.**

---

## 8. THE RTO PROBABILITY MODEL

### 8.1 Two-stage formulation (deviation D4)

```
STAGE 1 — PRE-CHECKOUT SCORE   (everything knowable at the payment step)

  logit_pre = γ₀
            + γ_pay   · [is_cod, paid_via_switch]
            + γ_hist  · [rto_rate_shrunk, new, orders_delivered, cod_share]
            + γ_latent· [trust, liquidity, intent]            ← hidden from analyst
            + γ_geo   · [tier, serviceability, cod_cultural]
            + γ_order · [log(order_value), discount_pct, cart_size]
            + γ_prod  · [category, product_rating, review_count]
            + γ_sell  · [seller_rating, seller_sla_breach_rate]
            + γ_logi  · [estimated_delivery_days, address_completeness]
            + γ_time  · [month_end × is_cod]

  p_rto_precheckout = logistic(logit_pre)      →  truth.p_rto_precheckout
                                                  ★ the AUC ceiling for any risk model ★

STAGE 2 — POST-DISPATCH SHOCK  (exists only after the parcel moves)

  shock = δ₁ · z(courier_reliability_score)⁻
        + δ₂ · realised_delay_days
        + δ₃ · seller_dispatch_late_flag
        + ν,     ν ~ N(0, 0.85)               ← irreducible: customer's day, luck

  logit_final = logit_pre + shock
  p_rto_final = logistic(logit_final)         →  truth.p_rto_final

STAGE 3 — DRAW
  rto_flag ~ Bernoulli(p_rto_final)           [shipped orders only]
```

**Why the two stages exist.** Phase 1 §9.3 promises AUC ≈ 0.75 and calls a 0.95 model "useless." A single-stage DGP using only pre-checkout features would let a well-specified logistic regression recover the truth almost perfectly. The Stage-2 shock — courier quality, actual transit delay, whether the customer happened to be home — is real, is genuinely unavailable at checkout, and structurally caps achievable AUC. **This is the honest source of the accuracy ceiling.** The `ν ~ N(0, 0.85)` term plus the three hidden latents together should land the ceiling at **AUC ≈ 0.74–0.78**.

> **Interview point:** "My model tops out around 0.76 and I can tell you exactly why: about a third of the variance in whether a parcel gets delivered is generated *after* the parcel leaves the warehouse. No checkout-time model can ever see that. The right response isn't a better model — it's routing the risky ones to a better courier."

### 8.2 Coefficient specification [A]

| Feature | Coefficient | Direction | Strength | Reason |
|---|---:|---|---|---|
| **Intercept γ₀** | *solved* ≈ **−3.25** | — | — | Calibrated to blended 16.5% |
| `is_cod` | **+1.60** | ↑ | **Strong — the planted causal effect** | Zero-collateral commitment. OR ≈ 4.95 |
| `paid_via_switch` | **−0.45** | ↓ | Moderate | **D5.** They tried to prepay — demonstrated intent |
| `pit_rto_rate_shrunk` (0→1) | **+2.80** | ↑ | **Strongest observable** | H3. Yields ≈2.3× lift, inside the 2.0–2.5× prior |
| `z(latent_intent)` | **+0.70** | ↑ | **Strong, hidden** | The confounder |
| `z(latent_liquidity)` | **−0.55** | ↓ | Moderate-strong, hidden | Cash at door |
| `z(latent_trust)` | **−0.30** | ↓ | Moderate, hidden | Refusal propensity |
| `pit_is_new_customer` | **+0.45** | ↑ | Moderate | No track record |
| `log1p(pit_orders_delivered)` | **−0.22** | ↓ | Moderate | Established relationship |
| `pit_cod_share` (0→1) | **+0.35** | ↑ | Weak | Habitual-COD residual risk |
| `geo_tier` METRO/T1/T2/T3 | **−0.35 / −0.10 / +0.20 / +0.45** | — | Moderate | ⚠️ fairness-audited |
| `z(serviceability_score)` | **−0.25** | ↓ | Moderate | Address resolution |
| `address_completeness_score` (0→1) | **−1.40** | ↓ | **Strong** | Direct mechanism; cheapest intervention |
| `seller_sla_breach_rate` (0→1) | **+1.20** | ↑ | Moderate | Late dispatch → decay |
| `seller_rating` − 4.30 (per pt) | **−0.28** | ↓ | Moderate | Fulfilment quality |
| `product_rating` − 4.20 (per pt) | **−0.15** | ↓ | Weak | H5 secondary |
| `log1p(review_count)` centred | **−0.05** | ↓ | **Noise-level** | Deliberately near-zero — H5 should be *partly* rejected |
| `log(order_value/1000)` | **+0.15** | ↑ | Weak | More to refuse |
| `discount_pct` − 0.08 (per 10pp) | **+0.18** | ↑ | Weak | Low commitment |
| `cart_size` ≥ 3 | **+0.25** | ↑ | Weak-moderate | Multi-variant |
| `estimated_delivery_days` − 4 (per day) | **+0.11** | ↑ | Weak-moderate | H6 promise limb |
| `category` FASH/BEAU/HOME/MOBACC/ELEC/GROC | **+0.35 / +0.05 / 0.00 / +0.10 / −0.20 / −0.15** | — | Weak-moderate | Fit uncertainty |
| `is_month_end_window × is_cod` | **+0.30** | ↑ | Moderate, interaction | Salary cycle |
| **Stage 2:** `z(courier_reliability)`⁻ | **δ₁ = +0.40** | ↑ | Moderate | Post-dispatch |
| **Stage 2:** `realised_delay_days` (per day late) | **δ₂ = +0.22** | ↑ | **Moderate — H6's real answer** | Broken promise > long promise |
| **Stage 2:** `seller_dispatch_late_flag` | **δ₃ = +0.25** | ↑ | Weak-moderate | |
| **Stage 2:** `ν` | N(0, 0.85) | — | — | Irreducible |

### 8.3 What this produces, and the selection arithmetic

Calibrate γ₀ by bisection so the **blended shipped-order RTO rate = 16.5% ± 0.5pp**.

Expected emergent outcome:

| Quantity | Target | **AS BUILT** | Source |
|---|---|---|---|
| Prepaid RTO rate | **4.1%** | **5.75%** | Emergent (CAL-04, SOFT) |
| COD RTO rate | **24.0%** | **23.39%** | Emergent (CAL-03, SOFT) |
| Blended | **16.5%** | **16.56%** | The only rate γ₀ solves against (CAL-05, HARD) |
| **Naive COD−prepaid gap** | **19.9pp** | **17.65pp** | What a crosstab shows |
| **Marginal effect of `is_cod`** | ~13.4pp | **10.05pp** | **DERIVED** as the AME (decision A6) |
| **Selection component** | 6.5pp = 33% | **7.60pp = 43.0%** | CAL-11 gate [0.25, 0.45] |

> **The AS BUILT column is the authority.** The Target column's derivation below assumes
> `γ₀ = −3.15` and `noise_sd = 0.85`; γ₀ is now solved (−5.25) and `noise_sd` was calibrated
> to 3.3125 against the GT-05 AUC ceiling (decisions A6, A37, A38). See limitation L8.
> Downstream work must read `data/truth/_truth.json`, not this table.

> **This is the number the entire project exists to produce.** Phase 1 §13 (bonus) claims *"the raw COD–RTO gap over-states the true planted effect by roughly a third."* 6.5 ÷ 19.9 = **32.7%**. The DGP delivers exactly that claim, and `truth_order_probability` lets Phase 5 prove the adjusted analysis recovers it.

**Two rules, same as §7.3:** only γ₀ is calibrated; every slope is fixed a priori. And no order is assigned an outcome — every `rto_flag` is a Bernoulli draw.

---

## 9. GROUND-TRUTH SPECIFICATION

### 9.1 What gets written to `data/truth/_truth.json`

```json
{
  "run_manifest": {
    "master_seed": 20260115,
    "params_sha256": "…",
    "generated_at_utc": "…",
    "generator_version": "2.0.0"
  },
  "calibrated_intercepts": {
    "cod_model_beta0": 0.301,
    "rto_model_gamma0": -3.247,
    "conversion_model_alpha0": 0.784
  },
  "planted_coefficients": {
    "cod_logit":  { "...": "every β from §7.2" },
    "rto_logit":  { "...": "every γ and δ from §8.2" }
  },
  "planted_causal_effects": {
    "cod_on_rto": {
      "logit_coefficient": 1.60,
      "odds_ratio": 4.953,
      "marginal_effect_pp_at_prepaid_baseline": 13.4,
      "naive_observed_gap_pp": 19.9,
      "selection_share_of_naive_gap": 0.327
    },
    "address_completeness_on_rto": { "logit_coefficient": -1.40 },
    "prior_rto_on_future_rto":     { "lift_multiple": 2.3 }
  },
  "hypothesis_ground_truth": {
    "H1_adjusted_gap_survives": true,
    "H2_new_customer_cod_lift_pp": 14.0,
    "H3_prior_rto_lift_multiple": 2.3,
    "H4_order_value_shape": "inverted_u_peak_3200",
    "H5_rating_effect": "small_but_real_on_cod; near_zero_review_count_on_rto",
    "H6_dominant_driver": "delay_not_promise",
    "H11_pct_cod_from_payment_failure": 0.068,
    "H12_achievable_auc_ceiling": 0.762
  },
  "avoidability": {
    "addressable_share_of_rto": 0.65,
    "structural_share_of_rto": 0.35
  },
  "economics_targets": {
    "mean_order_value": 1000,
    "rto_cash_loss_at_1000_aov": 309,
    "rto_economic_cost_at_1000_aov": 416,
    "breakeven_rto_probability": 0.257
  }
}
```

### 9.2 Truth strength summary

| Feature | True effect | Should the analysis recover it? |
|---|---|---|
| Historical RTO rate | **Strong** | Yes — cleanly |
| COD (causal) | **Moderate-strong**, but *observed as strong* | **Only partially.** The naive read overstates by ~50%; adjustment should close ~2/3 of the excess but not all, because `latent_intent` is unobservable |
| Address completeness | **Strong** | Yes — and it should surprise the analyst |
| `latent_intent` | **Strong** | **No — by design.** Its influence should show up as unexplained residual variance |
| Customer tenure / new | Moderate | Yes |
| Seller SLA breach | Moderate | Yes |
| Geography | Moderate | Yes — with the fairness caveat |
| Delivery *delay* (Stage 4) | Moderate | Yes, in diagnosis — but **must not** enter the model. H6's answer is "delay > promise, so this is a logistics fix" |
| Delivery *promise* (Stage 2) | Weak-moderate | Yes, weakly |
| Order value | Weak | Yes, weakly; inverted-U on COD |
| Product rating | Weak | Marginally |
| **Review count** | **≈ Zero** | **No — and that is the point.** A planted null. If the analysis "finds" a review-count effect on RTO, it is over-fitting |
| Post-dispatch noise | Moderate | Never |

### 9.3 Recovery tests (Phase 5 runs these; the harness defines them now)

| Test | Method | PASS criterion |
|---|---|---|
| **GT-01** Coefficient recovery | Logistic regression of `rto_flag` on all safe features | ≥80% of planted coefficients fall inside the estimate's 95% CI, and **no sign flips** on any Strong/Moderate relationship |
| **GT-02** Naive overstatement | Raw crosstab gap vs planted marginal effect | Naive gap ∈ [18.5, 21.5]pp and exceeds the planted 13.4pp |
| **GT-03** Adjustment closes the gap partially | Adjusted OR from the confounder-controlled model | Adjusted marginal effect ∈ [15, 19]pp — i.e. it moves toward 13.4 but does **not** reach it, because the latents are unobserved |
| **GT-04** Planted null holds | Coefficient on `log1p(review_count)` in the RTO model | 95% CI contains zero |
| **GT-05** AUC ceiling | Compare model AUC to AUC of `truth.p_rto_precheckout` used as a score | Model AUC within 0.03 of the ceiling; ceiling ∈ [0.74, 0.79] |
| **GT-06** H6 resolution | Compare model fit with `estimated_delivery_days` vs `delivery_delay_days` | Delay explains materially more deviance than promise |
| **GT-07** Selection decomposition | Propensity-matched COD effect vs planted | PSM estimate lands between the naive (19.9) and true (13.4) values |

> **Interview point:** "GT-03 is the test I'm proudest of. It's designed to *fail to fully recover the truth* — because in the real world you never observe purchase intent. If my adjusted estimate landed exactly on the planted effect, I'd know I'd accidentally given the model a variable it shouldn't have."

---

## 10. PAYMENT FAILURE & COD SWITCHING (H11)

### 10.1 The mechanism

```
prepaid intent
   │
   ├─► select rail  (UPI_INTENT / UPI_COLLECT / CARD / NETBANKING / WALLET)
   │
   ├─► ATTEMPT 1 ── success (p = 1 − f_rail_adj) ──► PREPAID ORDER
   │                    │
   │                 failure  → reason code
   │                    │
   │        ┌───────────┴─────────────┐
   │        │  retry same rail?       │  p_retry = 0.45
   │        └───────────┬─────────────┘
   │                    │
   │           ATTEMPT 2 ── success (p = 0.60) ──► PREPAID ORDER
   │                    │
   │                 failure
   │                    │
   └────────────────────┴──► TERMINAL FAILURE, choose:
                                 SWITCH_TO_COD   p = 0.70  → COD ORDER, paid_via_switch = TRUE
                                 SWITCH_RAIL     p = 0.10  → 85% succeed → PREPAID ORDER
                                 ABANDON         p = 0.20  → no order, abandon_step = PAYMENT_FAILURE
```

### 10.2 Parameters [A]

| Parameter | Value | Basis |
|---|---|---|
| Rail mix (prepaid intent) | UPI_INTENT 34% · UPI_COLLECT 22% · CARD 21% · NETBANKING 13% · WALLET 10% | Indian checkout mix |
| First-attempt failure by rail | UPI_INTENT 6% · UPI_COLLECT 27% · CARD 18% · NETBANKING 29% · WALLET 5% | Collect-flow and netbanking are the known weak rails |
| Blended first-attempt failure | **≈ 17.5%** | Weighted |
| Customer multiplier | `× (1 + 0.8 × pit_payment_failure_rate)` | Failure-prone customers stay failure-prone |
| Bank-downtime shock | 3% of hours get `× 2.2` failure multiplier | Creates clustered failures, realistic and analysable |
| `p_retry` | 0.45 | |
| Retry success | 0.60 | |
| Terminal split | COD 0.70 · other rail 0.10 · abandon 0.20 | |
| Failure reasons | TIMEOUT 31% · BANK_DECLINE 24% · OTP_FAILURE 19% · PSP_DOWNTIME 12% · INSUFFICIENT_FUNDS 9% · USER_CANCELLED 5% | |

### 10.3 Expected emergent outcomes

| Quantity | Expected [A] | Where it's used |
|---|---|---|
| Session-level prepaid payment success rate | **≈ 85%** | Funnel metric, guardrail baseline |
| Orders that are COD-via-switch | **≈ 4.2% of all orders** | — |
| **% of COD orders caused by prepaid friction** | **≈ 6.8%** | **H11 headline** |
| Sessions abandoned at payment failure | ≈ 1.2% of sessions | `abandon_step` diagnosis |
| RTO rate of switch-COD orders | ≈ **16%** vs 24% for intent-COD | **The D5 payoff** |

> **Deliberate design choice worth defending:** 6.8% sits **below** Phase 1's pre-registered 8–15% prior. That is intentional. The parameters come from plausible external PG-failure ranges, not from the prior. Phase 1 §4 says *"nothing signals genuine analytical work more than a documented wrong prior"* — H11 is the most likely candidate, and the honest finding becomes: *"payment reliability is real but smaller than I expected; it's still the first thing I'd ship because it's free, but it doesn't reframe the project."*

**A second, sharper finding falls out of D5:** switch-COD orders RTO at ~16% vs ~24% for intent-COD. So fixing payment reliability doesn't just move volume to prepaid — it recovers *the better half* of COD. That is a stronger business argument than the raw 6.8%.

---

## 11. RTO REASON GENERATION

### 11.1 Reasons are *conditional*, not sampled from a fixed table

Hard-coding a reason distribution would make the §7 avoidability waterfall circular — we'd be assuming the 65% we later "discover." Instead, each reason's probability is a softmax over driver-weighted scores, so the 65/35 split **emerges** and must be validated.

```
score(reason r | order) = base_weight[r]
                        + Σ driver_weight[r][d] × driver_value[d]

P(reason r | rto_flag = 1) = softmax(score)
```

| Reason | Class | Target share [A] | Dominant drivers |
|---|---|---|---|
| `CUSTOMER_REFUSED_CHANGED_MIND` | **ADDRESSABLE** | 22% | `latent_intent` ↑, `discount_pct` ↑, `estimated_delivery_days` ↑, `cart_size` ↑ |
| `CUSTOMER_UNREACHABLE_NO_ANSWER` | **ADDRESSABLE** | 14% | `latent_intent` ↑, `pit_is_new_customer`, `geo_tier` T3 |
| `ADDRESS_INCORRECT_INCOMPLETE` | **ADDRESSABLE** | 13% | `address_completeness_score` ↓ (dominant), `serviceability_score` ↓ |
| `INSUFFICIENT_CASH_AT_DELIVERY` | **ADDRESSABLE** | 11% | `is_cod` (required), `latent_liquidity` ↓, `is_month_end_window`, `order_value` ↑ |
| `NEVER_ORDERED_LOW_INTENT` | **ADDRESSABLE** | 5% | `latent_intent` ↑↑, `pit_is_new_customer`, `cart_size` ≥3 |
| | | **65%** | |
| `COURIER_OPERATIONAL_FAILURE` | STRUCTURAL | 10% | `courier_reliability_score` ↓ |
| `CUSTOMER_UNAVAILABLE_GENUINE` | STRUCTURAL | 9% | ~uniform (travel, emergency) |
| `PINCODE_SERVICEABILITY_FAILURE` | STRUCTURAL | 8% | `serviceability_score` ↓↓, `geo_tier` T3 |
| `DELIVERY_ATTEMPTED_OUTSIDE_WINDOW` | STRUCTURAL | 5% | `courier_reliability_score` ↓, `realised_delay_days` ↑ |
| `OTHER_UNCLASSIFIED` | STRUCTURAL | 3% | uniform |
| | | **35%** | |

### 11.2 Consistency constraints (enforced, not hoped for)

| Constraint | Rule |
|---|---|
| `INSUFFICIENT_CASH_AT_DELIVERY` | **Only possible when `payment_method = COD`.** Zero probability on prepaid. Validation test DQ-11 |
| `ADDRESS_INCORRECT_INCOMPLETE` | P must rise monotonically as `address_completeness_score` falls. Validation test BR-08 |
| `NEVER_ORDERED_LOW_INTENT` | Suppressed for customers with `pit_orders_delivered ≥ 5` |
| Reason is **never** a model feature | Enforced by the view in §4.4. Validation test LK-03 |

### 11.3 What this feeds

Phase 1 §7.2 Level 3 subtracts a "structurally unavoidable (35%)" layer from the ₹164.1 Cr exposure. **That 35% is now a measured quantity**, computed as the share of RTO *economic cost* (not order count) carried by STRUCTURAL reasons — which will differ slightly from 35% because reasons correlate with order value. Report both.

> **Interview point:** "The 65% avoidable figure in my waterfall isn't an assumption I typed in — it's the share of RTO cost attributable to reason codes I classified as checkout-addressable, and I'll show you the classification and defend each line. The one I'd argue about is 'customer unreachable', where I split it 60/40 between behavioural and genuine."

---

## 12. ECONOMICS GENERATION

### 12.1 Order value: resolving Phase 1 Open Question #3

**Decision (D2):** category-mixture lognormals, **population mean pinned at ₹1,000**.

| Category | Share of orders [A] | Mean order value | σ (log) | Median ≈ |
|---|---:|---:|---:|---:|
| FASHION | 30% | ₹850 | 0.55 | ₹732 |
| BEAUTY | 12% | ₹650 | 0.50 | ₹574 |
| HOME_KITCHEN | 15% | ₹1,150 | 0.60 | ₹961 |
| MOBILE_ACC | 18% | ₹550 | 0.52 | ₹481 |
| ELECTRONICS | 10% | ₹2,900 | 0.75 | ₹2,193 |
| GROCERY_FMCG | 15% | ₹700 | 0.45 | ₹634 |
| **Weighted mean** | 100% | **₹999.5** | | **≈ ₹690** |

Truncate to [₹149, ₹60,000]. Validation test **EC-01** asserts mean ∈ [₹975, ₹1,025].

> **Why pin the mean and not the median:** every Phase 1 figure — ₹416/RTO, ₹164.1 Cr, p* = 25.7% — is computed at a ₹1,000 order. Pinning the median at ₹1,000 would push the mean to ~₹1,450, inflate mean RTO cost to ~₹560, and quietly turn the headline into ₹221 Cr. **That would be exactly the kind of silent assumption drift this project is supposed to expose.**

### 12.2 Cost parameter generation

| Parameter | Fixed/Variable | Distribution & drivers | Mean | When incurred |
|---|---|---|---:|---|
| `discount_pct` | Variable | `Beta`-shaped around product `base_discount_pct`, + category promo, + `latent_price_sensitivity` selection | **8.0%** of GMV | Order (incentive) / delivery (promo) |
| `cogs_ratio` | Variable | N(0.75, 0.045) truncated [0.60, 0.86], by category | **0.750** | Delivery only |
| `forward_shipping_cost` | Variable | `forward_freight_base[geo_tier] × weight_factor[weight_band] + N(0, 6)`. Tier bases: METRO ₹62 · T1 ₹72 · T2 ₹86 · T3 ₹99 | **₹78.0** | **Dispatch — always** |
| `reverse_shipping_cost` | Variable | `1.09 × forward_shipping_cost + N(0, 8)` | **₹85.0** | RTO only |
| `packaging_cost` | Variable | By `weight_band`: LIGHT ₹9 · MEDIUM ₹13 · HEAVY ₹21, + N(0, 1.5) | **₹12.0** | Dispatch — always |
| `payment_processing_fee` | Variable | `1.8% × order_value`, rail-adjusted (UPI 0.9%, card 2.1%, NB 1.6%, wallet 1.9%) | **1.8%** blended | Prepaid, at payment |
| `cod_handling_cost` | Variable | `1.5% × order_value + ₹8` | **₹23** @ ₹1,000 | COD, on successful collection |
| `cod_failed_attempt_cost` | Fixed | ₹14 | **₹14** | COD RTO only |
| `reverse_handling_cost` | Variable | ₹35 base × weight factor, + N(0, 4) | **₹35.0** | RTO only |
| `shrink_cost` | Variable | `product.shrink_rate × cogs_value`. Category rates: FASHION 12% · BEAUTY 10% · GROCERY 20% · HOME 6% · MOBACC 4% · ELECTRONICS 3% | **≈ 8.0%** of COGS | RTO only |
| `support_ndr_cost` | Variable | RTO: ₹18 + ₹6 × (attempts−1). Delivered: ₹2 | **₹18** on RTO | Mostly RTO |
| `working_capital_cost` | Variable | `cogs_value × 0.14 × days_blocked / 365`, `days_blocked ~ LogN(μ=3.36, σ=0.22)` ⇒ mean ≈30d | **₹11.8** @ ₹1,000 | RTO only |
| `ops_allocation_cost` | Fixed | ₹10 | **₹10** | Delivered only |

### 12.3 Formulas

```
net_revenue        = CASE WHEN is_delivered
                          THEN gmv − discount_amount
                               + shipping_fee_charged + cod_fee_charged
                          ELSE 0 END

cogs               = CASE WHEN is_delivered THEN cogs_ratio × net_revenue ELSE 0 END

total_variable_cost = forward_shipping_cost
                    + packaging_cost
                    + COALESCE(payment_processing_fee, 0)
                    + CASE WHEN is_delivered THEN COALESCE(cod_handling_cost,0) ELSE 0 END
                    + CASE WHEN rto_flag THEN
                          reverse_shipping_cost + reverse_handling_cost
                        + shrink_cost + working_capital_cost
                        + CASE WHEN payment_method='COD' THEN cod_failed_attempt_cost ELSE 0 END
                      ELSE 0 END
                    + support_ndr_cost
                    + CASE WHEN is_delivered THEN ops_allocation_cost ELSE 0 END

contribution_margin = net_revenue − cogs − total_variable_cost

rto_cash_loss       = CASE WHEN rto_flag THEN −contribution_margin ELSE 0 END
foregone_cm         = CASE WHEN rto_flag THEN counterfactual_cm_if_delivered ELSE 0 END
rto_economic_cost   = rto_cash_loss + foregone_cm
```

`counterfactual_cm_if_delivered` is computed by re-running the CM formula on the same order with `is_delivered = TRUE` and `rto_flag = FALSE`. Storing it makes the Phase 1 §7 waterfall reproducible from the table with no re-derivation.

### 12.4 Reconciliation to Phase 1 (mandatory)

At a ₹1,000 order value the generator must reproduce:

| Line | Phase 1 §6.5 | Tolerance |
|---|---:|---|
| Prepaid delivered CM | **+₹112.0** | ±₹4 |
| COD delivered CM | **+₹107.0** | ±₹4 |
| COD RTO cash loss | **−₹309.0** | ±₹12 |
| COD RTO economic cost | **−₹416.0** | ±₹15 |
| Break-even RTO probability p\* | **25.7%** | ±0.8pp |

**Report separately, because they will differ and the difference is informative:**

| Metric | Expected | Why it differs |
|---|---|---|
| Mean RTO cash loss across the *actual* order distribution | **≈ ₹295–₹320** | Right-skewed AOV, and RTO concentrates in lower-AOV categories (Fashion, Mobile Acc) |
| Mean RTO economic cost across actual orders | **≈ ₹400–₹430** | Same |
| Annualised exposure (×240) | **₹158–₹172 Cr** | The ₹164.1 Cr headline should sit inside this band |

> **This is a genuine test, not a formality.** If the empirical mean lands at ₹350, the ₹165 Cr headline becomes ~₹138 Cr and Phase 1 §7.4 already commits us to rebuilding the waterfall from the empirical distribution rather than the ₹1,000 exemplar. **Do not tune the parameters to force ₹416.** Report what emerges.

---

## 13. PARAMETER REGISTRY — `config/params.yaml`

### 13.1 Why one file

Phase 1 §6.4 commits to it: *"one file change re-runs the entire model."* Three concrete reasons:

1. **Sensitivity analysis becomes a loop, not a rewrite.** Phase 1 §7.3's table (annual orders 12M/24M/36M, cost per RTO ₹300/₹416/₹520) is a `for` loop over param overrides, not six manual edits.
2. **An interviewer can attack any input and you answer in seconds.** "What if COD RTO is really 18%?" → change one line, re-run, show the new waterfall. That is the difference between a defensible model and a spreadsheet.
3. **It separates the DGP from the sampling.** Coefficients live in the params file; randomness lives in the seed. Changing the seed must never change a coefficient, and changing a coefficient must never require a new seed. §16 enforces this.

### 13.2 Structure

```yaml
meta:
  version: "2.0.0"
  currency: INR
  window_days: 90
  window_start: 2026-01-01

scale:
  n_customers: 55000
  n_sellers: 1200
  n_products: 8000
  n_geographies: 500
  target_orders: 100000
  checkout_conversion_target: 0.68        # → ~147,059 sessions
  # Population framing (Phase 1 §7.2, deviation D1)
  population_annual_orders: 24000000
  sample_to_quarter_factor: 60            # 100K ÷ 1.667%
  quarter_to_year_factor: 4
  annualization_factor: 240               # MUST equal the product above

calibration_targets:
  cod_share: {target: 0.620, tol: 0.010}
  rto_rate_cod: {target: 0.240, tol: 0.015}
  rto_rate_prepaid: {target: 0.041, tol: 0.008}
  rto_rate_blended: {target: 0.165, tol: 0.010}
  mean_order_value: {target: 1000, tol: 25}
  auc_ceiling: {target: 0.76, tol: 0.03}
  pct_cod_from_payment_failure: {target: 0.068, tol: 0.020}
  addressable_share_of_rto: {target: 0.65, tol: 0.05}

distributions:
  geo_tier_weights:      {METRO: 0.22, TIER1: 0.24, TIER2: 0.28, TIER3: 0.26}
  category_weights:      {FASHION: 0.30, BEAUTY: 0.12, HOME_KITCHEN: 0.15,
                          MOBILE_ACC: 0.18, ELECTRONICS: 0.10, GROCERY_FMCG: 0.15}
  category_mean_order_value: {FASHION: 850, BEAUTY: 650, HOME_KITCHEN: 1150,
                          MOBILE_ACC: 550, ELECTRONICS: 2900, GROCERY_FMCG: 700}
  category_ov_sigma:     {FASHION: 0.55, BEAUTY: 0.50, HOME_KITCHEN: 0.60,
                          MOBILE_ACC: 0.52, ELECTRONICS: 0.75, GROCERY_FMCG: 0.45}
  seller_rating:         {dist: beta_scaled, a: 6.0, b: 2.2, lo: 2.5, hi: 5.0}
  product_rating:        {dist: beta_scaled, a: 5.4, b: 2.0, lo: 1.5, hi: 5.0}
  review_count:          {dist: lognormal, mu: 3.6, sigma: 1.4, max: 25000}
  pre_window_orders:     {dist: neg_binomial, r: 1.6, p: 0.30, zero_inflation: 0.28}

latents:
  correlations: {trust_liquidity: 0.25, intent_liquidity: -0.20, intent_trust: -0.15}
  liquidity_geo_tier_shift: {METRO: 0.45, TIER1: 0.15, TIER2: -0.15, TIER3: -0.45}

cod_model:
  intercept_solved: null                   # written by the calibrator, never hand-edited
  coefficients:                            # §7.2 — FIXED A PRIORI, never tuned
    latent_trust: -0.55
    latent_liquidity: -0.45
    latent_intent: 0.40
    latent_price_sensitivity: 0.12
    pit_cod_share: 2.20
    log1p_prepaid_success: -0.35
    is_new_customer: 0.70
    log1p_orders_delivered: -0.18
    payment_failure_rate: 1.10
    has_saved_instrument: -0.60
    geo_tier: {METRO: -0.45, TIER1: -0.15, TIER2: 0.25, TIER3: 0.55}
    cod_cultural_index_z: 0.30
    log_order_value: 0.28
    log_order_value_sq: -0.12
    seller_rating_centered: -0.35
    product_rating_centered: -0.30
    log1p_review_count_centered: -0.12
    est_delivery_days_centered: 0.09
    discount_pct_centered: 0.15
    cart_size_ge3: 0.22
    category: {FASHION: 0.30, BEAUTY: 0.10, HOME_KITCHEN: 0.05,
               MOBILE_ACC: 0.15, ELECTRONICS: -0.35, GROCERY_FMCG: -0.10}
    is_month_end: 0.14
  noise_sd: 0.35

rto_model:
  intercept_solved: null
  coefficients:                            # §8.2 — FIXED A PRIORI
    is_cod: 1.60                           # ★ THE PLANTED CAUSAL EFFECT ★
    paid_via_switch: -0.45
    pit_rto_rate_shrunk: 2.80
    latent_intent: 0.70
    latent_liquidity: -0.55
    latent_trust: -0.30
    is_new_customer: 0.45
    log1p_orders_delivered: -0.22
    pit_cod_share: 0.35
    geo_tier: {METRO: -0.35, TIER1: -0.10, TIER2: 0.20, TIER3: 0.45}
    serviceability_z: -0.25
    address_completeness: -1.40
    seller_sla_breach_rate: 1.20
    seller_rating_centered: -0.28
    product_rating_centered: -0.15
    log1p_review_count_centered: -0.05     # planted null
    log_order_value: 0.15
    discount_pct_centered: 0.18
    cart_size_ge3: 0.25
    est_delivery_days_centered: 0.11
    category: {FASHION: 0.35, BEAUTY: 0.05, HOME_KITCHEN: 0.00,
               MOBILE_ACC: 0.10, ELECTRONICS: -0.20, GROCERY_FMCG: -0.15}
    month_end_x_cod: 0.30
  post_dispatch_shock:
    courier_reliability_z_neg: 0.40
    realised_delay_days: 0.22
    seller_dispatch_late: 0.25
    noise_sd: 0.85                         # ★ the AUC ceiling lever ★

payment_failure:
  rail_mix: {UPI_INTENT: 0.34, UPI_COLLECT: 0.22, CARD: 0.21,
             NETBANKING: 0.13, WALLET: 0.10}
  first_attempt_failure: {UPI_INTENT: 0.06, UPI_COLLECT: 0.27, CARD: 0.18,
             NETBANKING: 0.29, WALLET: 0.05}
  customer_failure_multiplier: 0.80
  downtime_hour_share: 0.03
  downtime_multiplier: 2.20
  p_retry: 0.45
  retry_success: 0.60
  terminal: {switch_to_cod: 0.70, switch_rail: 0.10, abandon: 0.20}
  switch_rail_success: 0.85

rto_reasons:
  base_weights: {CUSTOMER_REFUSED_CHANGED_MIND: 0.22, CUSTOMER_UNREACHABLE_NO_ANSWER: 0.14,
                 ADDRESS_INCORRECT_INCOMPLETE: 0.13, INSUFFICIENT_CASH_AT_DELIVERY: 0.11,
                 NEVER_ORDERED_LOW_INTENT: 0.05, COURIER_OPERATIONAL_FAILURE: 0.10,
                 CUSTOMER_UNAVAILABLE_GENUINE: 0.09, PINCODE_SERVICEABILITY_FAILURE: 0.08,
                 DELIVERY_ATTEMPTED_OUTSIDE_WINDOW: 0.05, OTHER_UNCLASSIFIED: 0.03}
  class_map: {CUSTOMER_REFUSED_CHANGED_MIND: ADDRESSABLE, ...}

economics:                                 # Phase 1 §6.4 registry, verbatim
  platform_discount_pct: 0.080
  cogs_ratio_mean: 0.750
  forward_freight_base: {METRO: 62, TIER1: 72, TIER2: 86, TIER3: 99}
  reverse_freight_multiplier: 1.09
  packaging: {LIGHT: 9, MEDIUM: 13, HEAVY: 21}
  pg_fee_rate: 0.018
  cod_handling_rate: 0.015
  cod_handling_fixed: 8
  cod_failed_attempt_fee: 14
  reverse_handling_base: 35
  shrink_rate_by_category: {FASHION: 0.12, BEAUTY: 0.10, GROCERY_FMCG: 0.20,
                            HOME_KITCHEN: 0.06, MOBILE_ACC: 0.04, ELECTRONICS: 0.03}
  support_ndr_rto: 18
  support_delivered: 2
  wc_annual_rate: 0.14
  wc_days_blocked_mean: 30
  ops_allocation_delivered: 10

fulfilment:
  preship_cancel_rate: {customer: 0.021, seller: 0.014, system: 0.005}
  max_delivery_attempts: 3
  dispatch_lag_days: {dist: lognormal, mu: 0.15, sigma: 0.45}

leakage_guard:
  safe_feature_whitelist: [pit_tenure_days, pit_orders_delivered, ...]   # LK-01 asserts against this
  hard_blocked: [delivery_attempts, actual_delivery_days, delivery_delay_days,
                 rto_reason, rto_reason_class, order_status, is_delivered,
                 contribution_margin, clv_estimate, hist_rto_rate_final,
                 hist_cod_share_final, hist_orders_final, ndr_code]

seed:
  master: 20260115
  substreams: [date, geography, seller, product, customer, latent, history,
               session, cod, payment, conversion, order, cancel, rto,
               delivery, reason, economics]
```

### 13.3 Two hard rules on this file

1. **`intercept_solved` fields are machine-written only.** A human editing them means the calibration is no longer reproducible. The generator writes them and stamps a `calibration_run_id`.
2. **Slopes are immutable across the project.** If a calibration target can only be hit by moving a slope, that is a *finding* — it means the Phase 1 assumption set is internally inconsistent — and it must be escalated and documented, not silently fixed.

---

## 14. GENERATION ORDER — THE IMPLEMENTATION SPEC

| # | Module | Reads | Writes | Blocking dependency |
|---|---|---|---|---|
| 01 | `load_config.py` | `params.yaml` | validated config object, seed substreams | — |
| 02 | `gen_dates.py` | config | `dim_date` | 01 |
| 03 | `gen_geography.py` | config | `dim_geography` | 01 |
| 04 | `gen_sellers.py` | config | `dim_seller` | 01 |
| 05 | `gen_products.py` | config, sellers | `dim_product` | 04 (products need a seller) |
| 06 | `gen_customers.py` | config, geography | `dim_customer` (pre-history cols), `truth_customer_latent` | 03 (customers need a home geo) |
| 07 | `gen_customer_history.py` | customers, latents | `dim_customer.pre_window_*` | 06 (**history must be generated *from* the latents**) |
| 08 | `gen_sessions.py` | customers, products, geo, dates | `fct_checkout_session` (Stage-1/2 cols) | 02,05,07 |
| 09 | `gen_state_snapshots.py` | sessions, history | `fct_customer_state_at_session` | 08 (**strict chronological pass**) |
| 10 | `gen_cod_choice.py` | sessions, state, latents, product, seller, geo | `intended_payment_method`, `truth.p_cod_intent` | 09 (needs point-in-time features) |
| 11 | `gen_payment_attempts.py` | sessions w/ prepaid intent | `fct_payment_attempt`, `switched_to_cod_after_failure` | 10 |
| 12 | `gen_conversion.py` | sessions, payment outcomes | `checkout_abandoned`, `abandon_step`, `final_payment_method` | 11 (**failure-driven abandonment**) |
| 13 | `gen_orders.py` | converted sessions | `fct_order` (Stage-3 cols) | 12 |
| 14 | `gen_cancellations.py` | orders, sellers | `is_cancelled_preship`, `cancel_actor`, `is_shipped` | 13 (**must precede RTO — denominator**) |
| 15 | `gen_rto_precheckout.py` | shipped orders + all safe features + latents | `truth.p_rto_precheckout`, `logit_rto_components` | 14 |
| 16 | `gen_delivery.py` | shipped orders, geo, seller | `fct_delivery_event`, `realised_delay_days`, post-dispatch shock, `truth.p_rto_final` | 15 |
| 17 | `gen_rto_outcome.py` | `p_rto_final` | `rto_flag`, `is_delivered`, `actual_delivery_days`, `delivery_attempts`, `outcome_resolved_date` | 16 |
| 18 | `gen_rto_reasons.py` | RTO orders + drivers | `rto_reason`, `rto_reason_class`, `ndr_code` | 17 |
| 19 | `gen_economics.py` | orders, outcomes, products, geo | `fct_order_economics` | 18 (**costs are outcome-conditional**) |
| 20 | `gen_customer_rollup.py` | all orders | `dim_customer.hist_*_final`, `clv_estimate`, `analytics_segment` | 19 |
| 21 | `write_truth.py` | everything | `data/truth/_truth.json`, truth tables | 20 |
| 22 | `load_postgres.py` | all parquet/CSV | PostgreSQL schemas `analytics` + `truth`, indexes, views | 21 |
| 23 | `validate.py` | PostgreSQL | `reports/data_validation_report.md`, PASS/FAIL | 22 |

**Calibration wrapper:** modules 08→13 (COD) and 15→17 (RTO) each run inside a bisection loop on the intercept. The loop must **reuse the same seed substreams on every iteration** so that only the intercept varies. Failing to do this makes calibration a random walk.

---

## 15. DATA VOLUME

| Table | Rows | Est. size | Justification |
|---|---:|---:|---|
| `dim_date` | 90 | <1 MB | 90-day window (D1) |
| `dim_geography` | 500 | <1 MB | ≥100 per tier ⇒ stable tier-level estimates and a credible fairness audit |
| `dim_seller` | 1,200 | <1 MB | ~83 orders/seller ⇒ seller-level SLA rates are estimable |
| `dim_product` | 8,000 | 2 MB | ~12.5 orders/product ⇒ category and rating-band analysis works; product-level does not (correctly) |
| `dim_customer` | 55,000 | 12 MB | ~1.82 orders/customer in window + pre-window history ⇒ enough repeat behaviour for H3 without a burn-in period |
| `fct_checkout_session` | **147,059** | 35 MB | `100,000 ÷ 0.68`. The north-star denominator |
| `fct_customer_state_at_session` | 147,059 | 30 MB | 1:1 |
| `fct_checkout_event` | ~700,000 | 90 MB | Mean 4.8 events/session |
| `fct_payment_attempt` | ~78,000 | 12 MB | Prepaid-intent sessions × 1.19 attempts |
| `fct_order` | **100,000** | 30 MB | The Phase 1 headline |
| `fct_delivery_event` | ~232,000 | 35 MB | 2.4 per shipped order |
| `fct_order_economics` | 100,000 | 28 MB | 1:1 |
| `truth_customer_latent` | 55,000 | 6 MB | Hidden |
| `truth_order_probability` | 147,059 | 45 MB | Hidden; JSONB component breakdowns |
| **Total** | **~1.62M rows** | **≈ 330 MB** | |

**Is this enough statistical power for what Phase 1 asks?**

| Requirement | Check |
|---|---|
| Segment analysis by risk tier | High-risk ≈17% of 100K = **17,000 orders**. Comfortable |
| RTO rate by geo tier × payment method | Smallest cell (Metro × prepaid RTO) ≈ 22,000 × 0.38 × 0.041 ≈ **340 RTO events**. Adequate; report CIs |
| Logistic regression, ~25 features | 16,500 positive events ÷ 25 features = **660 events per parameter**. Far above the rule-of-thumb 10 |
| Time-based validation split | 60 days train / 30 days test = ~67K / ~33K orders. Fine |
| A/B simulation with 3 arms | 147K sessions ÷ 3 = **49K per arm**. Phase 5 will show this is *underpowered* for a ₹1.50 MDE on CM/CSS — **which is a correct and useful finding**, and is why Phase 1 §11.4 flags CUPED and stratum-level power |

**Is it too big?** No. 330 MB loads into PostgreSQL in under a minute and into pandas in a few seconds. The `fct_checkout_event` table is the only one worth making optional if generation time becomes annoying.

> **Deliberate note on the A/B power result:** do not inflate the dataset to make the experiment "work." Phase 1 §11.4 already predicts high variance on CM/CSS. Discovering that 49K sessions/arm cannot detect ₹1.50 — and then showing that CUPED plus stratified analysis closes most of the gap — is a *better* portfolio outcome than a dataset sized to guarantee significance.

---

## 16. RANDOMNESS AND REPRODUCIBILITY

### 16.1 Seed architecture — independent substreams

Do **not** use one global `np.random.seed()`. Use `SeedSequence` spawning:

```python
from numpy.random import SeedSequence, default_rng

root = SeedSequence(config.seed.master)          # 20260115
names = config.seed.substreams                    # ordered, fixed list
children = root.spawn(len(names))
RNG = {name: default_rng(child) for name, child in zip(names, children)}

# usage
RNG["customer"].normal(...)
RNG["rto"].binomial(...)
```

**Why this specific design:**

| Property | Consequence |
|---|---|
| Each module draws from its own stream | Changing `n_products` from 8,000 to 9,000 does **not** shift the customer latents. Without this, every parameter change reshuffles the entire population and no sensitivity analysis is interpretable |
| Substream order is fixed in `params.yaml` | Adding a new substream must go at the **end** of the list, never the middle, or every downstream stream shifts |
| Seed is separate from coefficients | Changing the seed produces a **different sample from the same DGP**. Changing a coefficient produces **a different DGP**. These must never be confused |

### 16.2 The reproducibility contract

| Requirement | Implementation |
|---|---|
| Byte-identical regeneration | Same `master_seed` + same `params.yaml` SHA-256 ⇒ identical output. Asserted by **DQ-01**, which hashes `fct_order` and compares to a stored manifest |
| Different population, same physics | Change `seed.master` only ⇒ new draws, same relationships. Validation targets must still pass (with wider tolerance) |
| Detect accidental DGP drift | `_truth.json` records the params hash. If the hash changes but the version doesn't, the run is rejected |
| Multi-seed robustness | The harness runs the full pipeline at **5 seeds** and reports the spread on every calibration target. If COD share ranges 58–66% across seeds, the DGP is too noisy and needs tightening |

> **Interview point:** "Reproducibility isn't just hygiene here — it's what makes the sensitivity analysis meaningful. If changing the number of products also reshuffles which customers are risky, I can't tell whether a result moved because of the parameter or because of the dice. Independent substreams mean one thing changes at a time."

---

## 17. VALIDATION FRAMEWORK

**62 tests** in seven families: VOL (4) · CAL (11) · EC (7) · BR (11) · LK (6) · DQ (16) · GT (7).

**HARD** failures block the dataset. **SOFT** failures are logged and require written sign-off.

> **Count corrected.** This section previously said "42", which the family counts never summed to. The count then moved on the rulings: **+CAL-10** (reason-weight immutability, A4) · **+CAL-11** (selection share, A7) · **+LK-06** (declared shrinkage prior, A19) · **DQ-07 split into 07a / 07b / 07c** (A9). CAL-03/04 were downgraded HARD → SOFT (A7) but are still counted. See `docs/decision_register.md` A17.

### VOL — Volume (4 tests)

| ID | Test | PASS | Severity |
|---|---|---|---|
| VOL-01 | `fct_order` row count | ≥ 100,000 | HARD |
| VOL-02 | Session count | 145,000–150,000 | HARD |
| VOL-03 | Distinct customers with ≥1 order | ≥ 40,000 | SOFT |
| VOL-04 | Every dimension table at target ±1% | — | HARD |

### CAL — Calibration to Phase 1 targets (9 tests)

| ID | Test | Target | Tolerance | Severity |
|---|---|---|---|---|
| CAL-01 | COD share of orders | 62.0% | ±1.0pp | HARD |
| CAL-02 | Prepaid share | 38.0% | ±1.0pp | HARD |
| CAL-03 | COD RTO rate (shipped denom) | 24.0% | ±1.5pp | HARD |
| CAL-04 | Prepaid RTO rate | 4.1% | ±0.8pp | HARD |
| CAL-05 | Blended RTO rate | 16.5% | ±1.0pp | HARD |
| CAL-06 | Checkout conversion (orders/sessions) | 68.0% | ±2.0pp | HARD |
| CAL-07 | % of COD orders via payment-failure switch | 6.8% | ±2.0pp | SOFT |
| CAL-08 | Addressable share of RTO **cost** | 65% | ±5pp | SOFT |
| CAL-09 | **No slope coefficient differs from `params.yaml`** | exact | 0 | **HARD** |

### EC — Economics (7 tests)

| ID | Test | Target | Tolerance | Severity |
|---|---|---|---|---|
| EC-01 | Mean order value | ₹1,000 | ±₹25 | HARD |
| EC-02 | Median order value | ₹690 | ±₹60 | SOFT |
| EC-03 | Prepaid delivered CM @ ₹1,000 OV | +₹112 | ±₹4 | HARD |
| EC-04 | COD delivered CM @ ₹1,000 OV | +₹107 | ±₹4 | HARD |
| EC-05 | COD RTO cash loss @ ₹1,000 OV | −₹309 | ±₹12 | HARD |
| EC-06 | COD RTO economic cost @ ₹1,000 OV | −₹416 | ±₹15 | HARD |
| EC-07 | Annualised RTO exposure (×240) | ₹164 Cr | ₹150–₹180 Cr | SOFT — **report, don't tune** |

### BR — Behavioural relationships (11 tests)

Each is a directional test with an effect-size floor, so a *statistically significant but trivially small* relationship fails.

| ID | Test | PASS | Severity |
|---|---|---|---|
| BR-01 | New customers have higher COD share than established | ≥ +10pp | HARD |
| BR-02 | Prior-RTO customers have higher forward RTO | lift ≥ 1.8× | HARD |
| BR-03 | `pit_cod_share` predicts current COD selection | OR ≥ 2.5 per 0.5 increase | HARD |
| BR-04 | Lower seller rating → higher COD share | monotone across 4 bands, spread ≥ 4pp | SOFT |
| BR-05 | Lower product rating → higher COD share | spread ≥ 3pp | SOFT |
| BR-06 | COD share by order-value decile is **non-monotonic** (inverted-U) | peak in deciles 6–9, not 10 | SOFT |
| BR-07 | Payment failure precedes some COD orders | share ∈ [4%, 10%] | HARD |
| BR-08 | Lower address completeness → higher `ADDRESS_INCORRECT` reason share | monotone across quartiles | HARD |
| BR-09 | Longer promise → higher RTO, but **delay explains more** | delay coefficient's deviance contribution > promise's | HARD |
| BR-10 | Month-end COD RTO > mid-month COD RTO | ≥ +1.5pp | SOFT |
| BR-11 | Switch-COD orders RTO **less** than intent-COD orders | ≥ 5pp lower | HARD |

### LK — Leakage (5 tests) — all HARD

| ID | Test | PASS |
|---|---|---|
| LK-01 | `vw_risk_model_input` column list ⊆ `params.leakage_guard.safe_feature_whitelist` | exact subset |
| LK-02 | No column in `hard_blocked` appears in the risk-model training frame | zero matches |
| LK-03 | AUC of a model trained on safe features only | **< 0.85** (if ≥0.85, something leaked) |
| LK-04 | Point-in-time integrity: no `pit_*` value depends on an order with `order_ts ≥ session_start_ts` | zero violations, checked by re-derivation |
| LK-05 | `analyst` DB role has zero privileges on schema `truth` | permission check |

### DQ — Data quality (12 tests) — all HARD unless noted

| ID | Test | PASS |
|---|---|---|
| DQ-01 | Reproducibility: SHA-256 of `fct_order` matches manifest on re-run | identical |
| DQ-02 | No duplicate primary keys in any table | zero |
| DQ-03 | No orphan foreign keys | zero |
| DQ-04 | No negative prices, values, or costs | zero |
| DQ-05 | `order_ts ≥ session_start_ts` for all orders | 100% |
| DQ-06 | `outcome_resolved_date ≥ order_date` | 100% |
| DQ-07 | **Reconciliation:** last-session `pit_*` + that session's outcome = `dim_customer.hist_*_final` | 100% of customers |
| DQ-08 | `rto_flag = TRUE` implies `is_shipped = TRUE` and `is_delivered = FALSE` | 100% |
| DQ-09 | `is_cancelled_preship = TRUE` implies `is_shipped = FALSE` and `rto_flag = FALSE` | 100% |
| DQ-10 | Every order has exactly one economics row | 100% |
| DQ-11 | `INSUFFICIENT_CASH_AT_DELIVERY` occurs only on COD orders | 100% |
| DQ-12 | Null rate on required columns | 0%; SOFT for optional columns |
| DQ-13 | `payment_rail IS NULL` ⟺ `payment_method = 'COD'` | 100% |
| DQ-14 | Orders resolved after day 90 are flagged `is_censored` | present and ≥3% of late-window orders |

> **DQ-14 matters more than it looks.** Phase 1 §11 requires a 30-day maturation window and calls early reads *"systematically biased toward orders that resolved fast."* Orders placed on day 85 cannot have resolved by day 90. The dataset must **contain that censoring** so Phase 10 can demonstrate the bias rather than assert it.

### GT — Ground-truth recovery (7 tests) — §9.3, run in Phase 5

---

## 18. THE VALIDATION REPORT

`reports/data_validation_report.md`, auto-generated by module 23.

| Section | Contents |
|---|---|
| **1 — Dataset summary** | Row counts, date range, run manifest (seed, params hash, generator version, wall time), file sizes |
| **2 — Distribution checks** | Histograms/deciles for order value, ratings, tenure, order counts; tier/category/rail mix vs config |
| **3 — Target calibration** | The CAL table with target, actual, delta, tolerance, PASS/FAIL, plus the solved intercepts |
| **4 — Behavioural relationship checks** | The BR table plus the eleven diagnostic charts (COD by tenure bucket, RTO by prior-RTO count, COD by value decile with the inverted-U visible, etc.) |
| **5 — Economic checks** | The EC table; full CM waterfall for the three canonical orders; **empirical** mean RTO cost distribution vs the ₹416 exemplar; annualised exposure with its band |
| **6 — Leakage checks** | LK results; the exact column list of `vw_risk_model_input`; the LK-03 AUC with its 0.85 ceiling |
| **7 — Data quality checks** | DQ results; null/duplicate/orphan/range tables; the DQ-07 reconciliation |
| **8 — Ground-truth recovery** | Planted vs recovered coefficient table with 95% CIs; the naive-vs-adjusted COD effect chart — **the single most important figure in the project** |
| **9 — Final decision** | PASS / CONDITIONAL PASS / FAIL, with the rule below |

### PASS / FAIL rule

| Verdict | Condition | Action |
|---|---|---|
| **🟢 PASS** | All HARD tests pass; ≤2 SOFT failures | Proceed to Phase 3 |
| **🟡 CONDITIONAL PASS** | All HARD pass; 3–5 SOFT failures | Proceed **only** with each SOFT failure written into `docs/05_limitations.md` with a stated reason. A calibration miss on CAL-07/CAL-08 is a *finding* about the assumption, not a bug |
| **🔴 FAIL** | Any HARD failure, or >5 SOFT failures | **Do not proceed.** Fix the generator, or escalate the Phase 1 assumption that cannot be satisfied |

> **The one failure mode to guard against:** the temptation, on a HARD calibration failure, to nudge a slope until it passes. **CAL-09 exists specifically to make that impossible.** If COD share cannot reach 62% with the fixed slopes, that means the Phase 1 assumption set is internally inconsistent — and *that is a real result worth reporting*, not a bug worth hiding.

---

## 19. IMPLEMENTATION PROJECT STRUCTURE

```
checkout-rto-optimization/
│
├── README.md                       ← 400-word story + headline chart + how to run
├── Makefile                        ← `make generate`, `make validate`, `make load`, `make all`
├── pyproject.toml                  ← pinned deps: numpy, pandas, scipy, pyyaml,
│                                     sqlalchemy, psycopg2, statsmodels, scikit-learn
│
├── config/
│   ├── params.yaml                 ← §13. THE single source of truth
│   ├── params.schema.json          ← validates params.yaml on load (fail fast)
│   └── scenarios/                  ← sensitivity overrides for Phase 1 §7.3
│       ├── low_scale.yaml          (12M orders)
│       ├── high_scale.yaml         (36M orders)
│       ├── low_rto_cost.yaml       (₹300)
│       └── cod_share_50.yaml
│
├── src/
│   ├── config.py                   ← load, validate, spawn seed substreams
│   ├── dgp/
│   │   ├── dates.py  geography.py  sellers.py  products.py
│   │   ├── customers.py  latents.py  history.py
│   │   ├── sessions.py  state_snapshots.py
│   │   ├── cod_choice.py  payment_attempts.py  conversion.py
│   │   ├── orders.py  cancellations.py
│   │   ├── rto_precheckout.py  delivery.py  rto_outcome.py  rto_reasons.py
│   │   ├── economics.py  rollup.py
│   │   └── calibrate.py            ← the bisection wrapper for both intercepts
│   ├── io/
│   │   ├── writers.py              ← parquet + CSV emitters
│   │   └── postgres.py             ← schema DDL, COPY loader, index + view creation
│   └── validation/
│       ├── tests_vol.py  tests_cal.py  tests_ec.py  tests_br.py
│       ├── tests_lk.py   tests_dq.py   tests_gt.py
│       └── report.py               ← renders the §18 markdown report
│
├── scripts/
│   ├── 01_generate.py              ← runs modules 02–21 in order
│   ├── 02_load_postgres.py
│   ├── 03_validate.py
│   ├── 04_multi_seed_check.py      ← §16.2, five seeds
│   └── 05_run_scenarios.py         ← sensitivity loop over config/scenarios/
│
├── sql/
│   ├── 00_schema_analytics.sql     ← DDL for the 12 analytical tables
│   ├── 01_schema_truth.sql         ← DDL + REVOKE for the hidden schema
│   ├── 02_indexes.sql
│   ├── 03_views_core.sql           ← vw_funnel, vw_order_enriched, vw_rto_base
│   └── 04_view_risk_model_input.sql← §4.4 — the leakage firewall
│
├── data/
│   ├── raw/                        ← parquet, gitignored
│   ├── processed/
│   ├── truth/_truth.json           ← §9.1, committed (it's small and it's the point)
│   └── manifests/run_<seed>.json   ← hashes for DQ-01
│
├── notebooks/                      ← Phase 3+ (empty now)
├── tests/                          ← unit tests for the generator itself,
│                                     not the same as data validation
├── reports/
│   ├── data_validation_report.md
│   └── figures/
└── docs/
    ├── 00_phase1_blueprint.md
    ├── 01_phase2_data_architecture.md   ← this document
    ├── 02_data_dictionary.md            ← §3, extracted and machine-generated from the DDL
    └── 05_limitations.md
```

**Two structural choices worth flagging:**

- **`tests/` vs `src/validation/` are different things.** `tests/` unit-tests the *generator code* (does the shrinkage function behave at n=0?). `src/validation/` tests the *generated data* against business targets. Conflating them is common and makes both weaker.
- **`config/scenarios/` exists from day one** because Phase 1 §7.3's sensitivity table is a deliverable, not an afterthought. If sensitivity requires editing the main params file, it will not get done.

---

## 20. DELIVERABLES CHECKLIST

| # | Deliverable | Section | Status |
|---|---|---|---|
| 1 | Complete ER / data architecture | §1 | ✅ 15 tables, cardinality stated |
| 2 | Complete data dictionary | §3 | ✅ All columns, with leakage flags |
| 3 | Data availability timeline | §4 | ✅ 5 stages, SAFE/LEAKAGE registry, two-model discipline |
| 4 | Data-generating process | §5 | ✅ 19 steps with forced dependencies |
| 5 | COD probability model | §7 | ✅ Two-step logit, 23 terms, calibration algorithm |
| 6 | RTO probability model | §8 | ✅ Two-stage, 26 terms, selection arithmetic |
| 7 | Ground-truth specification | §9 | ✅ `_truth.json` schema + 7 recovery tests |
| 8 | Payment-failure mechanism | §10 | ✅ Full state machine + parameters |
| 9 | RTO-reason distribution | §11 | ✅ 10 reasons, conditional generation, 65/35 emergent |
| 10 | Economics generation model | §12 | ✅ Every cost line + reconciliation to Phase 1 |
| 11 | Parameter registry | §13 | ✅ Full `params.yaml` skeleton |
| 12 | Generation order | §14 | ✅ 23 modules with blocking dependencies |
| 13 | Validation framework | §17–18 | ✅ 62 tests, 7 families, PASS/FAIL rule |
| 14 | Claude Code folder architecture | §19 | ✅ |
| 15 | Implementation checklist | §20.1 | ✅ below |

### 20.1 Implementation checklist for Phase 2B

**Before writing any generator code**
- [ ] Confirm or reject deviations **D1** (quarter window) and **D2** (mean-pinned AOV) — everything re-derives from these
- [ ] Write `params.yaml` in full and validate it against `params.schema.json`
- [ ] Write `sql/00_schema_analytics.sql` and `sql/01_schema_truth.sql` **first** — the schema is the contract
- [ ] Stand up the seed-substream harness and unit-test that changing `n_products` does not alter customer latents

**Generator, in order**
- [ ] Modules 02–07: dimensions, latents, pre-window history. **Checkpoint:** latents must correlate with history in the specified directions before proceeding
- [ ] Modules 08–09: sessions and state snapshots. **Checkpoint:** run LK-04 (point-in-time integrity) here, not at the end
- [ ] Modules 10–12: COD intent, payment attempts, conversion. **Checkpoint:** run the COD calibration loop; confirm CAL-01 and CAL-07
- [ ] Modules 13–14: orders, cancellations. **Checkpoint:** DQ-09 (cancel/ship consistency)
- [ ] Modules 15–18: RTO two-stage, delivery, outcome, reasons. **Checkpoint:** run the RTO calibration loop; confirm CAL-03/04/05 and the §8.3 selection arithmetic against `_truth.json`
- [ ] Module 19: economics. **Checkpoint:** EC-03 through EC-06 against the ₹1,000 exemplar
- [ ] Modules 20–21: roll-up and truth file. **Checkpoint:** DQ-07 reconciliation

**Load and validate**
- [ ] Load PostgreSQL; create views; **REVOKE all on schema `truth` from `analyst`**
- [ ] Run all 62 tests; generate the §18 report
- [ ] Run the 5-seed robustness check; record the spread on every calibration target
- [ ] Run the four sensitivity scenarios; confirm the exposure band from Phase 1 §7.3 reproduces

**Gate to Phase 3**
- [ ] 🟢 PASS or documented 🟡 CONDITIONAL PASS
- [ ] `_truth.json` committed
- [ ] `docs/02_data_dictionary.md` generated from the live DDL, not hand-maintained
- [ ] Any SOFT failure written into `docs/05_limitations.md` with a reason

---

## 21. THREE THINGS I WOULD CHALLENGE ABOUT THIS DESIGN

Phase 1 §21 asks me to act as a skeptical interviewer throughout. Turning that on my own architecture:

**1. "You planted the answer, so of course the analysis finds it."**
Partly fair, and the mitigation is structural rather than rhetorical. Three of the strongest drivers — `latent_trust`, `latent_liquidity`, `latent_intent` — are permanently hidden, so the analysis **cannot** fully recover the truth, and test GT-03 is designed to confirm it doesn't. `review_count` carries a planted null so over-fitting is detectable. And the Stage-2 shock caps AUC at ~0.76 by construction. What the project demonstrates is not "my analysis is right" but "here is precisely how far off a competent analysis lands, and in which direction."

**2. "Your 62% COD and 24% COD-RTO are calibration targets you chose. Isn't the whole thing circular?"**
The *levels* are chosen; the *relationships* are not. Only two intercepts are calibrated, every slope is fixed a priori from the Phase 1 priors, and CAL-09 makes slope-tuning a hard failure. The business decisions in this project — the ₹7-vs-₹95 order value, the 25.7% break-even, whether targeting beats a flat policy — depend on the relationships and the cost structure, not on the levels. Phase 1 §7.3 already shows the recommendation surviving the full sensitivity range.

**3. "Single-line orders and no returns model. Isn't that too simple to be credible?"**
Yes, and it's a deliberate trade. Neither appears in any Phase 1 hypothesis, metric, intervention or the sizing model, and both would roughly double the fulfilment and economics code for zero narrative gain. The honest position is to name them in `docs/05_limitations.md` and be able to say what would change: multi-line orders would introduce partial-RTO, which splits freight attribution and would likely *raise* the per-order RTO cost slightly.

---

**END OF PHASE 2A — ARCHITECTURE DESIGN.**

**Do not generate data. Do not write generator code. Review §0.3 first — deviations D1 and D2 need your sign-off before anything downstream is valid.**
