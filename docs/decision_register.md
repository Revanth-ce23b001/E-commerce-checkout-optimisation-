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

The ruling was applied to **A10 (censoring)**, which is what it describes. The
correction was accepted on 2026-08-24 and this register's numbering is now canonical.
A7, A9 and A11 were then restated and have all been ruled.

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

**CAL-09 extended.** Now **five** blocks, once A11 added the two pre-window models:
`cod_model` · `rto_model` · `conversion_model` · `pre_window_cod_model` ·
`pre_window_rto_model`. **Five intercepts may be solved; zero slopes may move.**
CLAUDE.md rule 1 was amended accordingly (approved 2026-08-24) and now states the
principle rather than a count, so adding a model block does not require re-editing it.

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


### A7 - Three HARD RTO targets, one knob · **RULED 2026-08-24 · APPROVED**

**Logged as instructed: a specification inconsistency — three HARD targets against one
degree of freedom.** CAL-03, CAL-04 and CAL-05 cannot all be HARD when γ₀ is the only free
parameter, because moving γ₀ shifts all three rates together and the COD/prepaid *split* is
not tunable at all.

**Ruling — five parts, all applied.**

1. **CAL-05 (blended 16.5% ±1.0pp) stays HARD.** It is the one target that is a true
   function of γ₀. The calibrator solves against it alone.
2. **CAL-03 and CAL-04 downgraded to SOFT, widened to ±2.5pp**, and marked
   `emergent: true` in `params.yaml` so nobody later mistakes them for inputs. The
   validation report must label them **EMERGENT, NOT CALIBRATED**.
3. **No second knob.** Widening the gap would require moving latent slopes in the RTO
   model, which CAL-09 forbids. The gap is what the fixed `is_cod = +1.60` plus selection
   produces. That is the honest answer.
4. **CAL-11 (HARD) — the new gate, and the real one.**

   ```
   selection_share = (naive_gap - AME) / naive_gap
   PASS if selection_share in [0.25, 0.45]
   ```

   The entire analytical payoff is "naive analysis overstates the COD effect by roughly a
   third". At 8% the confounding is too weak to analyse; at 60% the planted coefficient is
   barely doing anything. **Either way the dataset fails to support the case study,
   regardless of whether the rate levels hit their targets.** HARD precisely where CAL-03/04
   no longer are: those measure *levels*, which one knob steers; this measures *structure*,
   which is what the project is about.
5. **GT-02 reformulated relatively:** `PASS if naive_gap > AME AND CAL-11 passes`. The
   absolute `[18.5, 21.5]`pp band is **dropped entirely**, not swapped for another
   hard-coded number.

**Applied.** `calibration_targets` severities and tolerances · `selection_share_gate` block ·
`ground_truth.gt_02.rule` · `calibration_search.rto_model.target` · `cal_11_selection_share`
in `src/validation/tests_cal.py`, with tests covering the pass case and both failure
directions.

---

### A9 - DQ-07 reconciliation invariant · **RULED 2026-08-24 · APPROVED**

Diagnosis accepted, but end-to-end coverage of the point-in-time logic is not given up.
**Split into three rather than dropped to SOFT.**

| Test | Severity | Assertion |
|---|---|---|
| **DQ-07a** | HARD | Resolved-only reconciliation. Last-session `pit_*` counts + outcomes of all orders resolved between that session and window end = `hist_*_final` **restricted to resolved orders**. |
| **DQ-07b** | HARD | **Full ledger identity.** `COUNT(fct_order WHERE customer_id = c) + pre_window_orders = hist_orders_final`, for every customer, no exceptions. Independent of resolution state — which is exactly why it is satisfiable where the original was not. |
| **DQ-07c** | SOFT | Excluded count is non-zero and equals the count of orders with `outcome_resolved_date > window_end` OR `is_censored = TRUE`. Asserts the exclusion is explained by the censoring model rather than by a bug. |

DQ-07b is the part that recovers most of what the original was reaching for: a genuine
end-to-end check that no order was dropped or double-counted anywhere in the pipeline, and it
holds regardless of what resolved when.

**Status.** Ruled and specified. Implementation waits for the tables these read (modules
13-20), like every other data-level test.

---

### A11 - Latent to pre-window history · **RULED 2026-08-24 · APPROVED · IMPLEMENTED**

Approved exactly as proposed: **re-use the existing COD and RTO latent slopes, add only
pre-window intercepts. No new business assumptions.**

| Quantity | Rule |
|---|---|
| `pre_window_orders` | Neg-binomial per `distributions.pre_window_orders`, capped by `tenure_days`. **NOT latent-driven** — order frequency is in no Phase 1 hypothesis. |
| `pre_window_cod_orders` | Per prior order: `logit = pi_cod0 + latent_trust(-0.55) + latent_liquidity(-0.45) + latent_intent(+0.40) + latent_price_sensitivity(+0.12) + geo_tier[tier]`. Calibrated to 62% ±2pp (SOFT). |
| `pre_window_rto_count` | Per prior order: `logit = pi_rto0 + is_cod(+1.60) + latent_intent(+0.70) + latent_liquidity(-0.55) + latent_trust(-0.30) + geo_tier[tier]`. Calibrated to 16.5% ±2pp (SOFT). |
| `pre_window_delivered` | `orders - rto_count - preship_cancels` |
| `pre_window_prepaid_success` / `_payment_failures` | Derived from the prepaid subset using `payment_failure.*`. No new coefficients. |

**Why this must not be shortcut.** Pre-window and in-window behaviour now share the same
latent slopes, which is precisely why prior RTO predicts future RTO (H3 / BR-02) and prior
COD predicts future COD (BR-03). Break it and the confounding becomes noise.

**Enforcement, not just intention.** The re-used slopes are recorded into the **parent**
ledger block (`cod_model` / `rto_model`), so the `CoefficientLedger` duplicate-value check
makes a pre-window/in-window divergence impossible — it raises at generation time, long
before validation. `pre_window_cod_model.coefficients` and `pre_window_rto_model.coefficients`
are schema-constrained to stay **empty**, so a second divergent copy of a slope cannot be
added by hand either.

**Five calibrated intercepts now.** cod · rto · conversion · pre_window_cod · pre_window_rto.
All are LEVELS. Zero slopes move. CAL-09 extended to all five blocks.

**Implemented and verified.** `src/generators/history.py`; both intercepts converge; all
seven latent-to-history correlation signs match the planted coefficients. See the Stage-2
checkpoint in the build status below.

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

## New - raised while applying the rulings

### A25 - `abandon_step` attribution · **RULED 2026-08-24 · APPROVED**

Deterministic, largest-negative-contribution, with a fixed precedence that overrides it.
Evaluated top to bottom; first match wins:

| Order | Step | Rule |
|---|---|---|
| 1 | `PAYMENT_FAILURE` | Set by module 11c. **Immutable, always wins.** |
| 2 | `FEE_REVEAL` | `shipping_fee_charged > 0` **and** it is the largest negative contributor |
| 3 | `PAYMENT_PAGE` | Otherwise, if abandonment occurred at or after that step |
| 4 | `ADDRESS` | Otherwise |

No new randomness and no new coefficient. **Applied** to
`conversion_model.abandon_step_precedence`. Outstanding: document the rule in
`docs/data_generating_process.md` when that file is written.

---

### A26 - Impossible session state · **RULED 2026-08-24 · APPROVED**

Approved: split the address hurdle out to run **before** payment attempts.

| Module | Step |
|---|---|
| **11a** | address-step abandonment draw |
| **11b** | payment-page-reach / fee-reveal abandonment draw |
| **11c** | payment attempts — only sessions reaching the payment page **and** intending prepaid |
| **12** | assemble final conversion state + `abandon_step` |

`ses_funnel_monotone` **stays** in the DDL. Under this ordering it should now be
*unreachable* rather than merely unviolated — which is the stronger property: the constraint
becomes a proof that the ordering is right, not a net catching a bug.

**Applied** to `conversion_model.hurdle_order` and `calibration_search.*.loop_modules`.
Outstanding: the generation-order tables in `docs/` when modules 08+ are written.

---

### A27 - Distributions the spec requires but never quantified · **OPEN — needs sign-off**

Modules 02-07 could not run without these, and they are almost certainly what the
never-recorded "A24 — 13 distributional/structural gaps" was pointing at. They are written
into `params.yaml` under `distributions.*`, each tagged **`[A27 PROPOSED]`**, rather than
buried as literals in `src/`.

| Block | What it sets | Why this value |
|---|---|---|
| `demand` | Weekday multipliers, month-end x1.08, salary-week x1.06, 4% noise | Spec says "weekly seasonality + month-end" with no magnitudes. Weekend-heavy, mild salary-cycle lift. |
| `geography` | Serviceability / courier reliability / delivery days / `cod_cultural_index` by tier | Serviceability falls monotonically with tier (access). **`cod_cultural_index` deliberately does not** — Tier-1 peaks, Metro is lowest — so norms stay a separate channel from access (spec 3.2). |
| `seller` | Tenure, rating count, SLA breach (mean ~0.083), cancellation (~0.021), tier thresholds | Both means match the spec worked examples. Tier requires clearing **both** the rating and SLA bar, so it cannot become a rating alias. |
| `product` | Base discount (mean ~0.082), returnability and weight band by category | Discount mean matches `economics.platform_discount_pct = 0.080`. Grocery returnability is low (0.35) because perishables mostly are not returnable. |
| `customer` | Tenure, age, channel, saved-instrument model | The saved-instrument rate rises with trust and liquidity rather than being flat, because customers who lack an instrument are exactly the ones a prepaid-shift intervention would target — that is the H7 ceiling. |
| `pre_window` | `min_days_between_orders: 21`; `model_switch_to_cod: false` | The cap enforces the brief 9.5 tenure constraint. Switch-COD is **not** modelled pre-window: it is a live in-session behaviour, and faking it would inflate CAL-07 with orders that never had a session. |

**Why proceeding on these is contained rather than reckless:** none of them feeds a
calibration target or a planted causal coefficient. They shape realism — names, tiers,
ratings, volume seasonality — not the relationships the project measures. The one with real
downstream reach is `geography.cod_cultural_index`, because it enters the COD logit at +0.30;
its tier ordering is the load-bearing choice there and is flagged above.

**They still need sign-off.** Requesting a ruling.

---

## Build status

**Stage 2 complete. Modules 02-07 built and checkpointed. Modules 08+ not started.**

| Component | Status |
|---|---|
| Dependency verification on Python 3.14 | ✅ All 11 packages import cleanly |
| Repo hygiene, `docs/` canonical paths, `.gitignore` | ✅ |
| Project skeleton, `pyproject.toml`, `Makefile` | ✅ |
| Seed substream harness + independence checkpoint | ✅ |
| Config loader (schema-validate, SHA-256, DGP-hash guard) | ✅ |
| Logit assembler + coefficient ledger | ✅ |
| Shrinkage helper | ✅ |
| `config/params.yaml` + `params.schema.json` | ✅ all rulings applied, schema-validated |
| `config/scenarios/dev_small.yaml` | ✅ scale only — not one coefficient changed |
| `sql/00_schema_analytics.sql` (12 tables) | ✅ parses clean — ⚠️ never executed, no PostgreSQL |
| `sql/01_schema_truth.sql` (2 tables + REVOKE) | ✅ parses clean — ⚠️ never executed |
| DDL dry-run (`scripts/02_load_postgres.py --dry-run`) | ✅ 34/34 statements parse |
| **CAL-09** slope immutability — five blocks | ✅ |
| **CAL-10** reason-weight immutability | ✅ ACTIVE, hash frozen at `35774eca…` |
| **CAL-11** selection-share gate (A7) | ✅ implemented + tested both directions |
| **LK-06** declared shrinkage prior (A19) | ✅ implemented + tested both directions |
| **DQ-07a / 07b / 07c** (A9) | 📋 specified; implementation waits for modules 13-20 |
| **02** `dim_date` | ✅ |
| **03** `dim_geography` | ✅ |
| **04** `dim_seller` | ✅ |
| **05** `dim_product` | ✅ |
| **06** `dim_customer` + `truth_customer_latent` | ✅ |
| **07** pre-window history + 2 calibrators | ✅ both converged |
| Modules 08-23 | ⬜ not started |

**110 unit tests, all passing. No full dataset generated.**

### Stage-2 checkpoint result (full scale, seed 20260115)

| Measure | Value |
|---|---|
| Pre-window orders drawn | 122,647 across 55,000 customers |
| `pi_cod0` solved | **+0.5156** → COD share **0.6166** (target 0.620 ±0.020) ✅ |
| `pi_rto0` solved | **−3.3750** → RTO rate **0.1671** (target 0.165 ±0.020) ✅ |
| Pre-ship cancel rate | 0.0395 |
| Customers with no pre-window history | **38.9%** |

**Latent → history correlations — all seven signs match the planted coefficients:**

| Latent | vs pre-window COD rate | vs pre-window RTO rate |
|---|---:|---:|
| `latent_trust` | −0.377 ✅ | −0.256 ✅ |
| `latent_liquidity` | −0.430 ✅ | −0.384 ✅ |
| `latent_intent` | **+0.294** ✅ | **+0.359** ✅ |
| `latent_price_sensitivity` | +0.133 ✅ | — |

`latent_intent` correlating positively with **both** is the confounding this project
depends on. Prior COD and prior RTO also correlate positively with each other, which is
what makes BR-02 and BR-03 detectable later.

All brief §9.5 consistency constraints pass with zero violations.

### Open items

| # | Item | Blocks |
|---|---|---|
| **A27** | Distribution values for modules 02-07, tagged `[A27 PROPOSED]` | Nothing today — but they are **unapproved** and the data already depends on them |
| A25 / A26 | Ruled; docs still need the generation-order tables updated | Modules 08+ documentation |
| A24 | Container item; its 13 sub-items were never recorded | Superseded in practice by A27 |

**Awaiting a ruling on A27 before this data is treated as anything but provisional.**
