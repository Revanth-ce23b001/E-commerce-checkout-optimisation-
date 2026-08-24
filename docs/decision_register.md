# Decision Register — Phase 2B

Tracks every point where the specification is ambiguous, underspecified, or internally
inconsistent, and what was decided. **Nothing here is resolved silently.** An unflagged
assumption is the worst failure mode in this project.

Status values: **APPROVED** · **PROVISIONAL** (approved, may be revisited) ·
**PENDING** (awaiting decision — blocks work) · **OPEN** (raised, not yet ruled on).

---

## ⚠️ NUMBERING CORRECTION — read before cross-referencing

The rulings issued on 2026-08-24 labelled the censoring decision **A11**. In this
register censoring is **A10**; **A11** is the latent → pre-window history
parametrisation, which is a different and still-unruled question.

The ruling has been applied to **A10 (censoring)**, which is what it describes. The
outstanding load-bearing items are therefore **A7, A9 and A11** — not A7/A9/A10.
A11 is restated below alongside A7 and A9 for ruling.

---

## Resolved

### A1 — Point-in-time state has a circular dependency · PROVISIONAL

**Problem.** Spec §14 lists 23 sequential modules. Module 09 (`fct_customer_state_at_session`)
needs in-window order *outcomes*, which module 17 produces. With 2.67 sessions per customer,
repeat sessions are the common case, so this is not an edge case.

**Decision.** Option A — **day-by-day chronological simulation.** Process the window one
day at a time, running the full 10a→17 chain for each day's sessions before advancing.

**Rationale.** Minimum outcome resolution is 4 days, so no order placed on a given day can
affect any session that same day. Day-level batching is therefore *provably* as accurate as
per-session processing, while keeping the generator vectorised and calibration affordable.
Preserves point-in-time integrity (LK-04) by construction, and keeps `pit_cod_share` (+2.20)
and `pit_rto_rate_shrunk` (+2.80) live for repeat customers — without which H3, BR-02 and
BR-03 are untestable.

**Specification change required.**
- §14: modules 10a–17 execute inside a day loop; 02–09 and 18–23 remain single-pass
- §5.1: 19-step diagram annotated with the loop boundary
- `docs/data_generating_process.md`: document the 4-day safety argument

---

### A2 — Conversion / abandonment model · **RULED 2026-08-24 · APPROVED**

**Problem.** ~31 of every ~32 abandoning sessions per 100 had no specified cause. §10.1
covers only the payment-failure path (~1pp). CAL-06 is HARD at 68% ±2pp with nothing to
calibrate. §9.1's `_truth.json` references a third intercept, `conversion_model_alpha0`,
whose model does not exist.

**Ruling.** **Minimal model (7 slopes) + a third calibrated intercept.**

The third intercept is already sanctioned: spec §9.1 lists `conversion_model_alpha0` in
`calibrated_intercepts`. The gap was that the slopes were never specified — a spec
omission, not a licence to invent freely. Hence seven, not twenty-one.

The seven map directly to blueprint §3 Branch 5 (checkout friction): fee reveal, address
friction, promise length, device. Conversion is not a hypothesis-bearing surface in Phase 1,
so it does not get a 21-slope apparatus. Seven is enough to make `abandon_step` causally
meaningful.

| Term | Coefficient |
|---|---:|
| `pit_is_new_customer` | −0.35 |
| `log_order_value` | −0.18 |
| `address_completeness` | +0.90 |
| `est_delivery_days_centered` | −0.07 |
| `shipping_fee_charged_gt0` | −0.45 (fee-reveal shock) |
| `device_web` | −0.25 |
| `cart_size_ge3` | −0.12 |
| `noise_sd` | 0.30 |

**Applied.** `conversion_model` block written to `config/params.yaml`.

**CAL-09 extended.** `MODEL_BLOCKS = ("cod_model", "rto_model", "conversion_model")` in
`src/validation/tests_cal.py`. **Three intercepts may be solved; zero slopes may move.**
CLAUDE.md rule 1 says "only two numbers may be calibrated" — that count is now **three**
and CLAUDE.md needs the one-line amendment. Flagged, not edited: it is the guardrail file.

**Sequencing recorded.** Payment-failure abandonment (module 11) is generated **before**
this model runs. The conversion model governs address-step and payment-page abandonment
only. PAYMENT_FAILURE abandonment must not be double-counted. → this raises **A26**.

---

### A3 — RTO reason softmax cannot produce the target distribution · PROVISIONAL

**Problem.** Spec §11.1 defines `P(reason) = softmax(base_weight + Σ driver_weight × driver)`.
But §13.2's `base_weights` sum to exactly 1.00 — they are *probabilities*. Feeding
probabilities into a softmax as *logits* collapses the intended 22%/14%/…/3% into a flat
9.3%–11.3% band, producing a **51.5%** addressable share instead of 65%.

**Decision.** Option A — apply `base_score[r] = log(base_weight[r])` before the softmax.
Human-readable percentage weights stay in `params.yaml`.

**Rationale.** The log transform makes the softmax reproduce the specified weights **exactly**
at zero drivers, while leaving drivers free to move the split — preserving §11.1's requirement
that the 65/35 split *emerges* rather than being hard-coded. Worth ≈₹22 Cr on the avoidable
pool (₹106.7 Cr vs ₹84.5 Cr).

**Verified in code.** With the log transform and all drivers at zero, the ADDRESSABLE share
computes to exactly **0.6500**.

**Specification change required.**
- §11.1 formula amended to `score(r) = log(base_weight[r]) + Σ driver_weight[r][d] × driver_value[d]`
- `docs/data_generating_process.md` must explicitly document **the transformation and its
  interpretation**: that it is a correction to the *mathematical implementation* so the
  specified base weights actually represent the intended zero-driver probabilities — **not**
  a change to the intended distribution. *(Explicit condition of approval.)*

---

### A4 — RTO reason driver weights · **RULED 2026-08-24 · APPROVED & FROZEN**

**Problem.** Brief §6 requires `rto_reasons.driver_weights`. §13.2 does not contain it.
§11.1 gives only qualitative arrows. Without magnitudes, BR-08 (HARD) fails outright.

**Ruling.** Ship the 22-coefficient matrix. **Zero both C-class coefficients.**

The two C-class coefficients are declared at `0.00` rather than deleted, so the record shows
they were considered and ruled to zero:

| Reason | Coefficient | Ruling |
|---|---|---|
| `ADDRESS_INCORRECT_INCOMPLETE` | `pit_is_new_customer` | **0.00** |
| `COURIER_OPERATIONAL_FAILURE` | `attempt_delay_days_z` | **0.00** |

**Shape as written.** 22 declared coefficients across 8 reasons · 20 live · 2 ruled to zero ·
2 reasons deliberately flat (`CUSTOMER_UNAVAILABLE_GENUINE`, `OTHER_UNCLASSIFIED`) ·
2 hard gates (`INSUFFICIENT_CASH_AT_DELIVERY` requires COD; `NEVER_ORDERED_LOW_INTENT`
suppressed at `pit_orders_delivered ≥ 5`). Verified against the written file.

**No class-level renormalisation.** The softmax normalises across the ten reasons *within*
each RTO order — that is the only normalisation applied. The ADDRESSABLE/STRUCTURAL split is
then **measured** from the realised draws, never forced. CAL-08 (SOFT) measures it on RTO
**cost**, not order count, per §11.3. **If it lands at 61/39 it is reported, not tuned.**
If it falls below 50%, escalate rather than log.

**CAL-10 approved as a new HARD test and is now ACTIVE.**
`rto_reasons.frozen_hash = 35774eca8875a357…` — sha256 over
`{base_weights, driver_weights, class_map}`. Confirmed PASSing. Any later edit fails it.

**Governance.** The weights are **frozen**. They will **not** be tuned to make CAL-08 pass.

**BR-08 dependency.** `ADDRESS_INCORRECT_INCOMPLETE.address_completeness = −1.60` is the
dominant driver and is what makes the reason share rise monotonically as
`address_completeness_score` falls. BR-08 cannot be confirmed until data exists; it is a
data test, not a config test.

---

### A5 — Is ₹1,000 the GMV or the post-discount `order_value`? · APPROVED

**Problem.** §3.10 defines `order_value = gmv − discount_amount`. EC-01 pins mean
**order value** at ₹1,000; EC-03…EC-06 compute every CM figure starting from **GMV** ₹1,000.
Both cannot hold — they differ by the 8% discount.

**Decision.** Option 1 — **₹1,000 is mean GMV per order.** Mean `order_value` ≈ ₹920.

**Rationale.** Blueprint §1.1 states ₹2,400 Cr annual GMV over 24M orders — exactly ₹1,000 of
GMV per order — and §6.5's worked example opens with "GMV ₹1,000". Phase 1 consistently means
GMV. Preserving this keeps every locked economic figure intact. The alternative breaks four
HARD tests and moves p\* by 3pp.

**Applied.** `params.yaml` key is `mean_gmv_per_order`; `category_mean_gmv` /
`category_gmv_sigma` relabelled; mean `order_value ≈ 920` recorded under
`reported_not_tested`.

**Economics preserved unchanged.** Prepaid delivered CM +₹112 · COD delivered CM +₹107 ·
COD RTO cash loss −₹309 · COD RTO economic cost −₹416 · p\* 25.7% · annual GMV ₹2,400 Cr ·
RTO exposure ≈₹165 Cr.

---

### A6 — Which planted COD effect is canonical? · **RULED 2026-08-24 · APPROVED**

**This was a spec error, now confirmed.** §8.3 computed the marginal effect at
`logit(0.041) = −3.1523` — the **prepaid population baseline rate**. §8.2's "γ₀ ≈ −3.25" is
the **intercept with all covariates at zero**. Two different quantities, written as if
identical.

**Ruling — four parts, all applied.**

**1. γ₀ is not a spec constant.** It is whatever the calibrator solves to satisfy CAL-03
(24.0%), CAL-04 (4.1%) and CAL-05 (16.5%), which are HARD. γ₀ is **not** moved to −3.00 or
any other value to make a marginal effect land on 13.4pp.

**2. The canonical effect is the average marginal effect**, computed post-hoc over the actual
generated population of shipped COD orders:

```
AME = mean_i[ logistic(logit_i) − logistic(logit_i − 1.60) ]   over shipped orders, is_cod = 1
```

Written to `_truth.json` as
`planted_causal_effects.cod_on_rto.average_marginal_effect_pp`. Whatever it is — 12.4, 13.4,
14.1 — **that is the truth.** `src/models/logit.py::average_marginal_effect` already
implements this.

**3. GT-03 re-anchored relatively**, replacing the hard-coded `[15, 19]`pp band:

```
PASS if   AME < adjusted_estimate < naive_gap
AND       (adjusted − AME) / (naive − AME) >= 0.35
```

The adjustment must move toward the truth without reaching it, closing at most 65% of the
gap. This preserves the test's purpose at **any** AME value and removes the identified
failure mode: once γ₀ > −3.00 the old band would have *contained* the truth, so an estimate
that fully recovered the unobservable would have **passed** — inverting what GT-03 exists to
do.

**4. GT-02** now asserts `naive_gap > AME`, not `> 13.4`.

**Applied.** `ground_truth` block in `params.yaml`; `rto_model.intercept_solved` documented
as "NOT a spec constant".

**Recorded as instructed:** this was a specification inconsistency, resolved by making the
effect **derived rather than asserted**.

**Consequence for CLAUDE.md.** The invariants table pins "True marginal effect of COD on RTO
≈ 13.4pp" and "Selection share of the naive gap ≈ 33%". Under this ruling both become
*expected* values, not invariants — the AME is measured. Flagged for amendment; not edited.

---

### A8 — `delivery_delay_days` NULL on RTO · **RULED 2026-08-24 · APPROVED**

**Ruling.** These are two different variables and the spec collided their names.

| Variable | Definition | Availability | Role |
|---|---|---|---|
| `fct_order.delivery_delay_days` | `actual − estimated` | **Legitimately NULL on RTO** | Diagnosis only (H6). **Never a feature.** |
| `fct_delivery_event.attempt_delay_days` | promised date → **first delivery attempt** | Exists for **every shipped order**, RTO or not | The Stage-2 shock input that δ₂ = 0.22 multiplies |

**Applied.** `rto_model.post_dispatch_shock.realised_delay_days` renamed to
`attempt_delay_days`; the column added to `fct_delivery_event` in
`sql/00_schema_analytics.sql`; both names added to `leakage_guard.hard_blocked`. The shock
reads the `attempt_number = 1` row.

**Specification change required.** §8.2 shock table updated to `attempt_delay_days`.

---

### A10 — Censoring semantics · **RULED 2026-08-24 · APPROVED**
*(issued under the label "A11"; see the numbering correction above)*

**Ruling.** Orders whose outcome would resolve after day 90 carry:

```
is_censored = TRUE
rto_flag, is_delivered, actual_delivery_days, outcome_resolved_date = NULL
```

**RTO-rate denominators (CAL-03/04/05) = shipped AND NOT censored.** Censored count is
reported separately.

**DQ-14 stands:** censoring **must** be present, ≥3% of late-window orders. Blueprint §11
needs it to *demonstrate* maturation bias rather than assert it.

**Applied.** `censoring` block in `params.yaml`; `is_censored BOOLEAN NOT NULL` on
`fct_order` with two enforcing CHECK constraints — `ord_censored_null` (a censored order
carries no outcome at all) and `ord_resolved_complete` (a shipped, uncensored order **must**
carry one, which is what makes "shipped AND NOT censored" a *complete* denominator).

---

### A12 — `fct_checkout_event` has no generator · **RULED 2026-08-24 · APPROVED**

**Ruling.** It is a **projection**, not an independent stochastic process.

Emitted deterministically from resolved session state at the end of module 12, once
conversion and `abandon_step` are known: walk each session's realised path, write one row per
step reached, interpolate timestamps from `session_start_ts`.

**No new randomness. No new parameters. No new seed substream.** ~50 lines, not a module.

**Applied.** Table DDL written with the rule stated in its header comment. The substream list
stays at 17 entries — nothing appended, so every existing stream is untouched.

---

## Pending — these block Stage 3

> **Restated below for ruling, as requested.** A7 and A9 as they stood; **A11** in place of
> A10, which has now been ruled.

### A7 — Three HARD RTO targets, one knob · PENDING

CAL-03 (COD RTO 24.0% ±1.5pp), CAL-04 (prepaid RTO 4.1% ±0.8pp) and CAL-05 (blended 16.5%
±1.0pp) are all HARD, but the calibrator has exactly **one** free parameter, γ₀. It cannot
independently steer three quantities. The COD/prepaid *split* is not tunable at all: it is
emergent from the fixed `is_cod = +1.60` plus whatever selection the COD model produces.
Moving γ₀ shifts all three rates in the same direction together, so if the emergent gap is
not ≈19.9pp, no value of γ₀ satisfies CAL-03 and CAL-04 simultaneously — and CAL-05 is not
independent, being roughly `0.62 × CAL-03 + 0.38 × CAL-04`. This item now also carries GT-02's
band problem: its `[18.5, 21.5]`pp naive-gap band is *narrower* than the CAL-03/CAL-04
tolerances jointly permit (those tolerances admit a gap anywhere in roughly
`[16.6, 21.4]`pp), so a dataset can pass both HARD calibration tests and still fail GT-02
through no fault of its own.

**Recommended option.** Calibrate γ₀ against **CAL-05 (blended)** alone — it is the one
target that is a true function of γ₀ — then **report** CAL-03 and CAL-04 as emergent
outcomes. If either misses its tolerance, that is a finding about the Phase 1 assumption set
(the +1.60 coefficient and the selection structure cannot jointly produce a 19.9pp gap at the
required levels), escalated per CLAUDE.md rule 3 — **not** repaired by moving a slope. In
parallel, widen GT-02's band to `[16.5, 21.5]`pp so it is consistent with the tolerances
CAL-03/04 already grant.

### A9 — DQ-07's reconciliation invariant is unsatisfiable · PENDING

DQ-07 (HARD) asserts that for every customer, the **last** session's `pit_*` values plus that
session's outcome equal `dim_customer.hist_*_final`. Two independent things break it. First,
**unresolved intermediate orders**: `pit_*` counts only orders whose outcome had *resolved*
before the session timestamp, but `hist_*_final` counts every order in the window. A customer
whose second-to-last order is still in transit at their last session is missing from the
`pit_*` side and present on the `hist_*` side, so the identity fails by construction — and
with outcomes taking 4–25 days this is common, not rare. Second, **A10 censoring** makes it
strictly worse: a censored order has `rto_flag = NULL`, so it can be counted in neither an
RTO numerator nor a delivered count, yet it is a real order.

**Recommended option.** Restate DQ-07 as a **resolved-only** reconciliation:
`last_session_pit_* + (that session's outcome, if resolved) = hist_*_final` computed over
**resolved, uncensored orders only**, and keep it HARD in that form. Separately add a SOFT
companion check that the count of orders excluded for non-resolution or censoring is
non-zero and matches the censoring model — turning what is currently a broken invariant into
two tests that each assert something true. The alternative — dropping DQ-07 to SOFT — loses
the only end-to-end check that the point-in-time logic is right, so it is not recommended.

### A11 — No latent → pre-window history parametrisation · PENDING

Brief §9.5 and spec §14 module 07 both require that pre-window history be generated **FROM
the latents** — "this is what creates the confounding" — but neither document states *how*.
There is no parametrisation anywhere linking `latent_trust`, `latent_liquidity`,
`latent_intent` or `latent_price_sensitivity` to `pre_window_orders`,
`pre_window_cod_orders`, `pre_window_rto_count`, `pre_window_prepaid_success` or
`pre_window_payment_failures`. This is load-bearing in a way that is easy to miss: those five
columns seed `pit_cod_share` (COD coefficient **+2.20**, the strongest observable) and
`pit_rto_rate_shrunk` (RTO coefficient **+2.80**, the strongest observable). If history is
drawn independently of the latents, those two features become noise, the confounding never
forms, H3 and BR-02/BR-03 have nothing to detect, and the naive-vs-adjusted gap that this
entire project exists to produce collapses toward zero.

**Recommended option.** A minimal, explicitly-declared linkage: draw `pre_window_orders` from
the existing zero-inflated negative binomial but shift its mean on `latent_intent` and
`latent_trust`; then draw each prior order's COD flag from a logit using **the same COD
slopes already in `params.yaml`** (latents only, no point-in-time terms, since none exist
pre-window), and each prior order's RTO flag from a logit using the same RTO latent slopes
plus `is_cod`. Re-using the approved coefficients rather than inventing a second set means
**no new business assumptions** — only the two pre-window intercepts would be new, and both
can be pinned to the same 62% / 16.5% population targets rather than freely chosen. The
alternative — a fresh 10–15 coefficient history model — adds a large block of C-class
invented parameters to a module nobody will ever inspect.

`config/params.yaml` carries `latent_to_history: null` deliberately. Module 07 is **blocked**.
A placeholder value there would be exactly the unflagged assumption CLAUDE.md forbids.

---

## Resolved under delegated judgement (A13–A24)

Ruled per the 2026-08-24 instruction: *"apply your own judgement, log each decision with the
reasoning, and flag any that turn out to be load-bearing."* **Two turned out to be
load-bearing and are flagged.**

### A13 — Bisection on realised draw vs expected share · RESOLVED
Bisect on the **realised draw**, using common random numbers. `src/config/seeds.py`
(`common_random_numbers`, `bernoulli_from_uniform`) already pre-allocates a fixed uniform
block indexed by entity, which makes the realised share a **monotone step function** of the
intercept — the condition bisection needs. Bisecting on the expected share would solve a
different quantity from the one CAL-01/CAL-05 measure.
Tolerance scales with n, because ±0.004 is finer than one order's worth at dev scale:
`tol = max(0.004, 1.5/√n_orders)` → 0.0047 at 100K, 0.021 at 5K.
Applied as `calibration_search.share_tolerance_floor` / `tolerance_n_scaling`.

### A14 — No γ₀ bracket; the stated [−2, +2] excludes −3.25 · RESOLVED
The `[−2, +2]` bracket in §7.3 belongs to **β₀** (COD), where it is correct — β₀ is expected
near +0.30. §8.3 gave **no** bracket for γ₀ and the COD one was being read across.
`cod_model.bracket: [-2.0, 2.0]`, `rto_model.bracket: [-6.0, 0.0]`,
`conversion_model.bracket: [-4.0, 4.0]`.

### A15 — COD loop specified over 08→13; module 08 has no β₀ dependence · RESOLVED
Loop body is **10→13**. Modules 08–09 (sessions, point-in-time state) are invariant to β₀ and
run once, cached. Pure efficiency correction, no behavioural effect — but it matters:
re-running module 09's chronological pass on every bisection iteration would dominate
calibration wall time. `calibration_search.cod_model.loop_modules: [10, 11, 12, 13]`.

### A16 — Switch-COD share: §7.3 says 3.5pp, §10.3 says 4.2% · RESOLVED — **not a contradiction**
The two are measured on different bases. §7.3's 3.5pp is the gap between mean
`P(COD_intent)` across **sessions** and observed COD share among **orders**; §10.3's 4.2% is
switch-COD as a share of **all orders**. The denominators differ because prepaid-intent
sessions that abandon leave the order population entirely. And 4.2% ÷ 0.62 = **6.8%** of COD
orders, which reconciles exactly with the H11 headline. Both recorded under
`reported_not_tested`; **CAL-07 (6.8% of COD orders, ±2pp, SOFT) is the only tested
quantity.**

### A17 — Test count is 57, not 42 · RESOLVED — ⚠️ **flagged, docs not edited**
VOL 4 + CAL 9 + EC 7 + BR 11 + LK 5 + DQ 14 + GT 7 = **57**. The "42" headline is an
arithmetic error carried through all three documents, and §17's DQ heading additionally says
"12 tests" while listing 14. The authoritative number is the sum of the family counts.
**CLAUDE.md and both spec documents need "42" → "57"** (and §17's DQ heading → 14). Not
edited: CLAUDE.md is the guardrail file, and the spec documents are the source of truth.

### A18 — Division-by-zero / NULL imputation in the logits · RESOLVED — **load-bearing** ⚠️
**One rule: every rate feature with a zero denominator imputes to its declared population
prior.** Never to zero — which would assert evidence that does not exist — and never to a
value computed from realised outcomes, which would leak. Priors are declared constants in
`params.imputation`: `pit_cod_share_prior 0.620`, `pit_rto_rate_prior 0.165`,
`pit_payment_failure_rate_prior 0.175`, `pit_avg_order_value_prior 920`.

**Why this is load-bearing.** A substantial minority of sessions belong to customers with no
resolved history — `pre_window_orders` carries 28% zero-inflation before in-window new
customers are counted, and the exact share is not knowable until data exists.
`pit_cod_share` carries **+2.20** and `pit_rto_rate_shrunk` **+2.80**, so whatever that share
turns out to be, the imputed value is multiplied by the two largest observable coefficients
in the project across all of it.
Imputing 0 instead of 0.620 would shift the COD logit by −1.36 for those sessions and be
absorbed into β₀ — silently changing what the calibrated intercept *means*. The choice is
constant across affected rows either way, so it does not distort *relative* risk; it does
change the intercept, which is why it is declared rather than assumed.

### A19 — Shrinkage prior source unspecified · RESOLVED — **load-bearing** ⚠️
The empirical-Bayes prior mean for `pit_rto_rate_shrunk` is the **declared constant**
`imputation.pit_rto_rate_prior = 0.165`, **not** the realised in-window RTO rate.

**Why this is load-bearing.** Computing the prior from realised outcomes would make every
customer's Stage-1 risk feature a function of the window's Stage-5 results — a subtle,
population-level leak that no column-name check would catch, and it would inflate LK-03's AUC
without any obviously wrong column appearing in the view. It would also make the feature
non-reproducible under calibration, since the prior would move on every bisection iteration.

### A20 — `pit_orders_resolved` used in a formula but not a column · RESOLVED
Added as `fct_customer_state_at_session.pit_orders_resolved INT NOT NULL`, plus
`pit_resolved_fits` and `pit_outcomes_fit` CHECK constraints. It is the denominator of
`pit_rto_rate_raw` and the `trials` input to shrinkage, so it is materialised rather than
recomputed. Added to `safe_feature_whitelist`.

### A21 — `pit_risk_tier_rule_based` needs `payment_method`, which is Stage 3 · RESOLVED
Blueprint §9.3's baseline is "payment method + prior RTO + tenure — three rules", but
`payment_method` cannot appear in a Stage-2 feature. **Split into two columns**, which maps
exactly onto §4.3's two-model discipline:

| Column | Table | Rules | Verdict |
|---|---|---|---|
| `pit_risk_tier_rule_based` | `fct_customer_state_at_session` | prior RTO + tenure | ✅ SAFE — the M1 baseline |
| `order_risk_tier_rule_based` | `fct_order` | + `payment_method` | ❌ HARD-BLOCKED from M1 — the M2 baseline |

### A22 — `ndr_code` has no enumeration · RESOLVED
Nine codes, mapped deterministically from `rto_reason` via `rto_reasons.ndr_code_map`, with a
matching CHECK constraint on `fct_delivery_event`. Deterministic rather than drawn, so the
NDR code carries no information the reason does not — it is a realism artefact, not a second
signal. `ndr_code` is hard-blocked.

### A23 — Cancelled-order economics · `cogs_value` · working capital · RESOLVED — ⚠️ one live risk
Three sub-items:

1. **Pre-ship cancelled orders carry zero on every cost line.** Nothing dispatched, nothing
   collected, prepaid refunded. `economics.preship_cancel_economics: zero_all_lines`.
2. **`cogs_value` defined** as `cogs_ratio × net_revenue_if_delivered` — the *counterfactual*
   goods value. It exists because on an RTO `net_revenue = 0` and therefore `cogs = 0`, yet
   shrink and working-capital costs are still proportional to goods value.
   `economics.cogs_value_basis: net_revenue_if_delivered`.
3. **The working-capital figure is not reproducible from its own formula — implemented as
   specified anyway.** `cogs_value × 0.14 × days_blocked/365` at a ₹1,000 GMV order gives
   `690 × 0.14 × 29.5/365 ≈ **₹7.9**`, not the spec's **₹11.8**. Reproducing ₹11.8 requires a
   base of ≈₹1,026, i.e. roughly *order value*, not COGS. Per CLAUDE.md rule 2 the formula is
   implemented as written and the **≈₹3.9 shortfall is reported as a finding**, not patched by
   moving `wc_annual_rate`. It flows into EC-05 (−₹309 ±₹12) and EC-06 (−₹416 ±₹15), both
   HARD; ₹3.9 sits inside both tolerances, so neither is expected to fail on this alone —
   but it consumes a third of EC-05's headroom.

### A24 — "13 distributional/structural gaps" · CARRIED FORWARD — honest note
This was raised in an earlier session as a container item, but **its thirteen sub-items were
never written down**. They cannot be reconstructed from the register, and inventing a list
now would be worse than admitting the gap. Each will be raised, numbered and logged
individually as its module is reached. Recording this rather than quietly closing the item.

---

## New — raised while applying the rulings

### A25 — `abandon_step` allocation rule is unspecified · OPEN

The A2 conversion model yields **one** probability and therefore **one** Bernoulli draw, but
`abandon_step` has four values (ADDRESS / PAYMENT_PAGE / PAYMENT_FAILURE / FEE_REVEAL). Module
11 supplies PAYMENT_FAILURE. Nothing states how the remaining three are assigned, and the
brief requires `abandon_step` to be *causally connected* to what happened rather than drawn
from a table.

**Recommended option — a deterministic attribution rule, no new randomness and no new
parameters.** On an abandoned session, attribute the step to the term that contributed the
largest negative amount to the conversion logit: `shipping_fee_charged_gt0` → **FEE_REVEAL**;
`address_completeness` → **ADDRESS**; otherwise → **PAYMENT_PAGE**. `address_completed` and
`payment_page_reached` then follow deterministically. This reuses the seven approved slopes
and invents nothing, but it *is* a rule the spec does not contain, so it is raised rather than
resolved.

### A26 — Conversion/payment sequencing produces an impossible session · OPEN — **blocks module 12**

Spec §14 runs module 11 (payment attempts) **before** module 12 (conversion), and the A2
ruling confirms that order. But the conversion model can then draw ADDRESS-step abandonment
for a session that has **already completed a successful payment attempt** — a session that
paid and then abandoned at the address step. The DDL's `ses_funnel_monotone` constraint
rejects such a row, correctly.

**Options.**
- **(a) Split the hurdle.** Evaluate the conversion logit's address hurdle as module 11a,
  *before* payment attempts, and the payment-page/fee-reveal hurdle after. Preserves both
  "failure causes abandonment" and funnel coherence. Cost: one logit, two thresholds — needs
  a splitting rule the spec does not give.
- **(b) Condition the draw.** Keep the single draw at module 12 but restrict the abandon-step
  label to be consistent with the realised payment path: a session with a successful payment
  attempt can only abandon at PAYMENT_PAGE/FEE_REVEAL, never ADDRESS. Cheapest; slightly odd
  semantics (paid, then abandoned).
- **(c) Reorder.** Run conversion before payment attempts. Rejected — it breaks the
  failure-causes-abandonment dependency that CLAUDE.md's generation order calls non-negotiable
  and that makes H11 answerable.

**Recommendation: (a).** It is the only option that leaves both invariants intact. Ruling
needed before module 12 is written; it does not block modules 02–11.

---

## Build status

**Stage 2 (foundation) — config and schema complete. Generators still blocked.**

| Component | Status |
|---|---|
| Dependency verification on Python 3.14 | ✅ All 11 packages import cleanly |
| Repo hygiene, `docs/` canonical paths, `.gitignore` | ✅ |
| Project skeleton, `pyproject.toml`, `Makefile` | ✅ |
| Seed substream harness + independence checkpoint | ✅ 17 tests |
| Config loader (schema-validate, SHA-256, DGP-hash guard) | ✅ |
| Logit assembler + coefficient ledger | ✅ 24 tests |
| CAL-09 (slope immutability) — **now covers 3 model blocks** | ✅ |
| CAL-10 (reason-weight immutability) | ✅ **ACTIVE and PASSING** — hash frozen |
| Shrinkage helper | ✅ 15 tests |
| **`config/params.yaml`** | ✅ **written and schema-validated** |
| **`config/params.schema.json`** | ✅ written |
| **`sql/00_schema_analytics.sql`** (12 tables) | ✅ written — ⚠️ not executed, no PostgreSQL available |
| **`sql/01_schema_truth.sql`** (2 tables + REVOKE) | ✅ written — ⚠️ not executed |
| `latent_to_history` parametrisation | ⛔ **A11 — module 07 blocked** |
| Generators, calibration, any data | ⛔ Blocked on A7, A9, A11 (+ A26 for module 12) |

**56 unit tests, all passing. No dataset generated.**

**Remaining blockers before generator work starts:** A7 · A9 · A11. A26 blocks module 12
only, and can be ruled later without holding up modules 02–11.
