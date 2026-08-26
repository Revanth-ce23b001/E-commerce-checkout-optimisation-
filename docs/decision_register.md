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

### A27 - Distributions the spec requires but never quantified · **APPROVED 2026-08-24**

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

**Approved 2026-08-24.** The `[A27 PROPOSED]` tags stay in `params.yaml` as provenance:
they mark values that came from this register rather than from the source spec.

---

### A28 - Distributions for modules 08-12 · **APPROVED 2026-08-24, with two conditions**

Same status and same pattern as A27: required by the spec, never quantified by it. Written
into `params.yaml` under `distributions.*` tagged **`[A28 PROPOSED]`**.

| Block | What it sets | Why this value |
|---|---|---|
| `session.allocation` | Sessions allocated uniformly across customers | Repeat-visit propensity is **not** latent-driven. No Phase 1 hypothesis involves visit frequency, and making it latent-driven would open a second, unasked-for path from `latent_intent` into the point-in-time features — quietly strengthening the confounding beyond what was planted. |
| `session.address_completeness` | Beta(6.5, 2.2), mean ~0.747 | Drawn **independently of tier and of every latent**. Spec 3.6 names no drivers; geography's access channel is already `serviceability_score`. Keeping it independent also keeps the cheapest intervention lever clean — if address quality were a tier proxy, "fix the addresses" would silently become a geography policy. |
| `session.quantity_weights` | {1: 0.90, 2: 0.075, 3: 0.025}, mean 1.125 | Single-line orders (spec 1.5). Heavily weighted to 1 so `gmv = list_price x quantity` stays near the pinned mean. See A29. |
| `session.hour_of_day_weights` | 24 relative weights, evening-heavy | Feeds the bank-downtime clustering, which needs a real hour distribution to cluster against. |
| `session.away_geography_rate` | 0.06 | Most sessions ship home; some are gifts, travel, work addresses. |
| `session.discount_*` | Price-sensitivity weight 0.012/z, noise sd 0.020 | Spec 12.2 names deal-seeking selection as a driver of `discount_pct` but gives no magnitude. Deliberately small: this is selection into promotions, not a pricing model. |
| `risk_tier_rules` | HIGH at 0.257, MED at 0.165 | The HIGH cut is **p\***, the break-even RTO probability from blueprint 6.6, so the rule baseline and the economic threshold agree by construction rather than by coincidence. |
| `conversion.address_hurdle_share` | 0.35 | Payment-page-weighted, because that is where real checkouts shed most traffic. The split is exact — `p_address x p_payment == p_convert` — so it costs no accuracy. |
| `conversion.joint_solve_passes` | 3 | beta_0 and alpha_0 are interdependent; see the build status for the observed drift. |

None of these feeds a planted causal coefficient.

**Approved with two conditions, both met.**

**(a) `address_completeness` independent of `geo_tier` must be a STATED property, not
an accidental one.** The generation checkpoint now reports the realised correlation
between `address_completeness_score` and `geo_tier`, and the trade is written up as
**L1** in `docs/limitations.md`: real Tier-3 addresses probably *are* worse, and we are
trading that realism for an intervention whose effect is cleanly attributable rather
than entangled with a fairness problem it does not have.

**(b) Confirm which level was made uniform.** Stated explicitly, and it is not the day
level:

| Level | Allocation |
|---|---|
| Across **customers** | **Uniform.** Per-customer session count is ~Poisson. This is the one that is not latent-driven. |
| Across **days** | **NOT uniform** — driven by `dim_date.demand_index`: weekday rhythm, month-end ×1.08, salary-week ×1.06. |
| Within a **day** | Evening-weighted via `session.hour_of_day_weights`. |

The temporal structure is intact, which BR-10 (month-end COD RTO lift) and DQ-14
(censoring concentrated in the late window) both depend on. Recorded as **L3**.

---

### A29 - `order_value` is Stage-2 SAFE but depends on Stage-3 `quantity` · **RESOLVED — spec amendment needed**

**Problem.** Spec 4.2 lists `order_value` as a **Stage-2 SAFE** risk feature — knowable before
payment-method selection. But 3.10 defines `order_value = gmv - discount_amount` with
`gmv = list_price x quantity`, and marks `quantity` **Stage 3**. A Stage-2 column cannot
depend on a Stage-3 one. One of the two markings has to move.

**Decision.** `quantity` moves to **Stage 2** and is drawn in module 08. It is a cart
attribute — the customer chose how many units before reaching the payment page — so Stage 2
is where it actually belongs. `order_value` then becomes genuinely Stage-2 and its use in
both the COD logit and the risk model is legitimate.

**Consequence that had to be fixed immediately.** `category_mean_gmv` is the target mean
**GMV per order** (decision A5). With `gmv = list_price x quantity` and E[quantity] = 1.125,
drawing list prices at the GMV target overshoots mean GMV by 12.5% — about **₹125 against
EC-01's ±₹25 tolerance**. Module 05 therefore divides the category means by E[quantity].
That is implementing A5 correctly, not changing it.

**Specification change required.** 3.10: `quantity` availability Stage 3 -> Stage 2.

---

### A30 - `pit_avg_order_value` has no pre-window source · **RESOLVED — limitation recorded**

**Problem.** `pit_avg_order_value` is a listed SAFE feature, but `dim_customer` (spec 3.5) has
no pre-window order-value column, so there is nothing to average for a customer whose only
history predates the window.

**Decision.** It builds from **in-window orders only** and is NULL until the customer has
one. Consistent with decision A18: unknown is unknown.

**Rejected alternative.** Inventing a `pre_window_avg_order_value` column would add a schema
field the spec does not have, and drawing pre-window order values purely to average them
would be an unflagged assumption feeding a model feature.

**Limitation to record in `docs/05_limitations.md`.** The feature is weaker than the data
dictionary implies, and it is NULL for most sessions early in the window. It carries no
coefficient in either 7.2 or 8.2, so no planted relationship is affected — but an analyst
reading the dictionary would not expect the sparsity.

---

### A31 - VOL-01, VOL-02 and CAL-06 are jointly knife-edge · **RULED 2026-08-24 · APPROVED**

**Problem, verified arithmetically at full scale.** Three HARD tests interact:

| Test | Requirement |
|---|---|
| VOL-01 | `fct_order` >= 100,000 |
| VOL-02 | session count in [145,000, 150,000] |
| CAL-06 | conversion 68.0% +/- 2.0pp, i.e. [66.0%, 70.0%] |

Session count today is `target_orders / checkout_conversion_target` = **147,059**. At that
count, VOL-01 needs conversion >= **exactly 68.00%** — the midpoint of CAL-06's band. **Any
conversion below 68.00% fails VOL-01, even though CAL-06 explicitly permits it.**

This is not hypothetical. The realised conversion is **0.6762**, comfortably inside CAL-06
(error −0.38pp against a ±2.0pp tolerance), and it produces **99,441 orders — VOL-01 fails.**

**Feasibility.**

| Conversion | Sessions needed for VOL-01 | Within VOL-02 cap? |
|---:|---:|---|
| 0.6600 (CAL-06 floor) | 151,516 | ❌ exceeds 150,000 |
| 0.6667 | 149,993 | ✅ just fits |
| 0.6762 (realised) | 147,886 | ✅ |
| 0.6800 | 147,059 | ✅ |

So there is a **jointly infeasible sliver**: any conversion in **[66.00%, 66.67%)** satisfies
CAL-06 and cannot satisfy VOL-01 and VOL-02 together at any session count.

**Recommended option.** Set the session count to the **top of the VOL-02 band, 150,000**,
instead of deriving it from the conversion midpoint. That makes VOL-01 hold for any
conversion >= 66.67%, covering all but the bottom 0.67pp of CAL-06's band, and at the
realised 0.6762 it yields **101,430 orders**. Then either narrow CAL-06 to [66.7%, 70.0%] or
document the residual sliver as a known joint constraint.

**Rejected: raising `target_orders` or tightening CAL-06 to force it.** Conversion is
emergent from seven fixed slopes and one calibrated intercept. Tuning anything to make three
mutually-constraining HARD tests agree is exactly the failure mode CAL-09 exists to prevent.

**Ruling — logged as a specification error.** Session count was derived from a point
estimate of conversion and then made HARD. It is an **input knob**, not a business target.

1. **`n_sessions` becomes a free parameter, set to 155,000** — not 150,000. All five
   intercepts are re-solved once the A1 day loop closes, so conversion moves again;
   150,000 sits on the old VOL-02 cap with zero headroom. 155,000 clears VOL-01 across
   the *entire* CAL-06 band: 102,300 orders at 0.660, 105,400 at 0.680, 108,500 at 0.700.
2. **VOL-01 (>=100,000 orders) stays HARD.** It is the case-study headline.
3. **VOL-02 reframed.** The `[145,000, 150,000]` band is deleted.
   - **VOL-02a (SOFT):** `n_sessions` in [145,000, 170,000] — sanity, not a target.
   - **VOL-02b (HARD):** `|orders/sessions - reported conversion| < 0.001` — an
     internal-consistency check, not a level check. A level test on an input knob was
     testing the knob rather than the data.
4. **CAL-06 unchanged** at [66.0%, 70.0%]. The infeasible sliver disappears once the
   session count is no longer pinned to the midpoint.

**Applied.** `scale.n_sessions` · `volume_targets` block · `sessions.py` reads the knob
instead of deriving it · CLAUDE.md scale invariant restated · `dev_small.yaml` scales
`n_sessions` in the same proportion (it was inheriting the full 155,000, which would have
given the dev scenario 55 sessions per customer instead of 2.8).

---

### A32 - The annualisation factor must be DERIVED · **RULED 2026-08-24 · APPROVED**

**Consequence of A31 that was not in the modules 08-12 report.** `annualization_factor:
240` was a fixed literal in `params.yaml`, computed as 24,000,000 / 100,000. With the
session count now a free knob producing ~105,000 orders, a fixed 240 would have inflated
the annual opportunity by ~4.8% — the ₹165 Cr headline drifting to ~₹173 Cr purely because
a knob unrelated to the business had moved.

**Ruling.**

| Quantity | Status |
|---|---|
| `population_annual_orders: 24000000` | **FIXED** business assumption |
| `annualization_factor` | **DERIVED at validation time**: `population_annual_orders / COUNT(fct_order)` |
| The literal `240` | **Removed.** Survives only as `annualization_factor_expected: 240`, reporting only, never used in a calculation |
| EC-07 (annualised exposure) | uses the derived factor |
| **EC-08 (HARD, new)** | derived factor within [200, 280]; outside that band the sample drifted far enough to warrant a look |

**Why it must be derived rather than pinned.** Total RTO cost × (population ÷ sample) is
*invariant to sample size*. Pinning the factor breaks that invariance and makes the
headline a function of a generator setting. Deriving it makes the headline a function of
the business.

**Applied.** `params.yaml` (literal removed, bounds added) · CLAUDE.md scale invariant
restated as "annualisation factor = 24,000,000 ÷ actual order count (≈230, DERIVED —
never hard-coded)" · `sample_to_quarter_factor` and `quarter_to_year_factor` removed too,
since 60 × 4 = 240 encoded the same 100,000-order assumption.

---

### A33 - Distributions for modules 13-17 · **RESOLVED — approved with three conditions**

Same status and pattern as A27 / A28. Tagged `[A33 PROPOSED]` in `params.yaml` under
`distributions.delivery`.

| Value | Why |
|---|---|
| `transit_multiplier_mean: 1.00`, `sd: 0.28` | Transit time as a multiple of the destination's `base_delivery_days`, so the promise and the reality share a scale. |
| `courier_reliability_transit_weight: 0.45` | Worse couriers are slower. **This is the load-bearing one**: it is what correlates `attempt_delay_days` with `courier_reliability_score`, so the two Stage-2 shock terms are not independent. Setting it to zero would make δ₁ and δ₂ orthogonal, which is unrealistic and would make the shock easier to decompose than it should be. |
| `attempt_gap_days: 2`, `rto_initiation_days: 2` | Spacing of failed attempts, and the wait before a return is raised. Drives how long an RTO takes to resolve, and therefore how much of the late window is censored (DQ-14). |
| `rto_return_days: LogN(1.35, 0.40)` | The return leg, ~4 days median. |
| `risk_tier_rules.m2_cod_escalates_one_tier` | The **third** rule of blueprint §9.3's baseline ("payment method + prior RTO + tenure"). It belongs to M2 only and must never touch the Stage-2 tier (decision A21). |

**Approved with three conditions. Condition (a) contradicted what was drafted, and is
flagged here rather than silently reconciled.**

**(a) `attempt_delay_days` must derive from `courier_reliability_score` AND
`seller_sla_breach_rate`.** ⚠️ **The draft only satisfied half of this.** Transit time
depended on courier reliability, but `dispatch_lag_days` was drawn from an independent
lognormal — so `seller_dispatch_late`, which δ₃ multiplies, had **no relationship to the
seller's actual SLA breach rate**. That meant the Stage-1 coefficient
`seller_sla_breach_rate = +1.20` and the Stage-2 δ₃ = +0.25 were describing unrelated
things, and δ₃ was effectively noise.

Fixed: dispatch lag now scales with the seller's z-scored SLA breach rate through a new
`seller_sla_dispatch_weight: 0.35` (log-space, so a +1sd breach-rate seller dispatches
1.42× slower). That parameter is *new* — required by the condition, not in the approved
draft — and is flagged as such.

**(b) `delivery_attempts` capped at 3, and an RTO must always exhaust the cap.** Already
true in the draft; now **asserted** rather than assumed. It is load-bearing: the NDR base
of 9.0 lands the realised mean on the Phase 1 registry value of ₹18 *only* because attempts
is exactly 3 on every RTO, so a silent drift here would quietly move EC-05 and EC-06.

**(c) `dispatch_lag_days` independent of anything customer-level.** Satisfied. The new
dependency is on a **seller** attribute; no customer trait enters, so no unintended path
into the outcome is opened.

---

### A34 - EC-01: mean GMV or mean order_value? · **RULED 2026-08-24 · A5 STANDS**

⚠️ **The A31 follow-on instruction reverses decision A5, which was approved on
2026-08-24.** Flagging rather than resolving, because the two readings imply different
economics and A5's rationale is still on the record.

| | **A5 as approved** | **The new instruction** |
|---|---|---|
| EC-01 tests | mean **GMV** per order = ₹1,000 | mean **order_value** = ₹1,000 |
| implies mean GMV | ₹1,000 | ₹1,000 / 0.92 = **₹1,087** |
| implies mean order_value | ₹920 | ₹1,000 |
| `E[list_price]` | ₹1,000 / E[quantity] | ₹1,000 / 0.92 / E[quantity] |

**What A5 was approved on.** Blueprint §1.1 states **₹2,400 Cr annual GMV over 24M
orders** — exactly ₹1,000 of GMV per order — and §6.5's worked example opens with
"GMV ₹1,000". Every locked figure (+₹112, +₹107, −₹309, −₹416, p\* = 25.7%) is computed
starting from a ₹1,000 **GMV** order.

**What changes if EC-01 moves to order_value.** Mean GMV becomes ₹1,087, so annual GMV
becomes 24M × ₹1,087 = **₹2,609 Cr, not ₹2,400 Cr** — the blueprint's own headline breaks
by 8.7%. And the five locked CM figures were computed at GMV ₹1,000, so each would need
recomputing at GMV ₹1,087; they are currently HARD tests EC-03…EC-06.

**Current implementation: A5 as approved.** `calibration_targets.mean_gmv_per_order`
tests GMV at ₹1,000; module 05 divides the category means by E[quantity] only. Both means
are reported in the checkpoint so the choice can be made against real numbers.

**Ruling: A5 stands. ₹1,000 is GMV.** Phase 1 §6.5 is unambiguous —
GMV ₹1,000 − 8% platform discount = net revenue ₹920 — and 24M × ₹1,000 = ₹2,400 Cr is
the figure the whole ₹165 Cr model rests on.

**Root cause, logged as a Phase 2A internal inconsistency.** §3.10 labels `order_value`
as "the ₹1,000 AOV quantity" and §12.1 pins mean `order_value` at ₹1,000. **Both are wrong
against Phase 1 §6.5.**

| Test | Severity | Assertion |
|---|---|---|
| **EC-01** | HARD | mean **GMV** per order = ₹1,000 ±₹25 |
| **EC-01b** | SOFT | mean **order_value** per order ≈ ₹920 ±₹30 — reported |

**Specification change required.** §3.10 and §12.1: remove the mislabel.

The earlier instruction to divide by (1 − discount) is withdrawn; the A29
implementation was already correct.

---

### A35 - H11 prior is likely to be rejected · **RECORDED — a finding, not a miss**

CAL-07 lands at **5.75%** of COD orders caused by prepaid payment friction, against a
target of 6.8% ±2pp (SOFT) — comfortably passing — and against Phase 1's **pre-registered
prior of 8–15%**, which it falls below.

**This is the expected outcome and it is a finding.** Spec §10.3 says so explicitly: the
parameters come from plausible external PG-failure ranges, not from the prior, and
blueprint §4 says *"nothing signals genuine analytical work more than a documented wrong
prior"*. H11 was flagged as the most likely candidate.

**The honest write-up:** *payment-friction-driven COD is real but smaller than
hypothesised. It is still the first thing to ship, because it is free — but it does not
reframe the project.*

**The sharper finding sits next to it (deviation D5).** Switch-COD orders carry −0.45 in
the RTO logit, so they should fail materially less often than intent-COD. Fixing payment
reliability does not merely move volume to prepaid — it recovers *the better half* of COD.
That is a stronger business argument than the raw 5.75%.

---

### A36 - EC-01 is measured on ORDERS, and conversion selects on order value · **RULED · IMPLEMENTED**

**Problem, measured at full scale.** `conversion_model.log_order_value = -0.18` means
expensive carts convert less often. EC-01 is measured on the **order** population, which
is therefore a *selected* sample of the session population — selected on precisely the
variable EC-01 tests.

| | mean GMV | mean order_value |
|---|---:|---:|
| all sessions | 1,004.15 | 922.73 |
| **converted (= orders)** | **962.70** | **884.34** |
| selection effect | **−4.13%** | −4.16% |

The E[quantity] correction from A29 works: sessions land at 1,004. The order population
then drifts 4.1% low, which is **outside EC-01's ±₹25 (±2.5%) tolerance**.

**Options.**

- **(a) Accept and report.** EC-01 misses by ~₹37 and the miss is explained. Honest, but
  EC-01 is HARD, so this would need EC-01 downgraded or its tolerance widened.
- **(b) Compensate at the session level.** Draw session values from a distribution
  centred at **₹1,043** so the ORDER population averages ₹1,000. Arguably this is
  implementing spec §12.1 correctly — that table describes the *order* value distribution,
  and orders are what it should reproduce. Exactly analogous to the E[quantity] correction
  already accepted under A29.
- **(c) Set `conversion_model.log_order_value` to zero.** Rejected: it is an approved
  slope, CAL-09 forbids moving it, and it is the checkout-friction mechanism that makes
  `abandon_step` meaningful.

**Ruling: (b), as a SIXTH calibrated level — not a hand-edit.**
`distributions.product_price_scalar_solved`, machine-written and bisection-solved, applied
multiplicatively to every `category_mean_gmv`. Hand-editing the category means would
destroy the audit trail and would have to be redone every time conversion moved.

It joins the **joint** solve rather than running after it: conversion depends on order
value, order value shifts COD exposure, and COD share feeds back into conversion.

**Implemented.** The scalar is applied in closed form inside the day loop — only three
terms across the three logits read order value, and `log(v·s) = log(v) + log(s)` — so a
level that would otherwise have required rebuilding the design matrix costs three vector
operations per pass. CAL-09's ledger check is joined by a reported *implied per-category
scalar spread*, which is what would move if a ratio had been edited.

---

### A37 - The AUC ceiling is 0.87, not 0.74–0.79 · **RULED · SOLVED, NOT PICKED**

**The spec's own coefficient set, at the spec's own `noise_sd = 0.85`, does not produce
the AUC the spec asks for.** Measured at full scale, the AUC of `truth.p_rto_precheckout`
used as a score against realised `rto_flag` is **0.8745** — above GT-05's
[0.74, 0.79] band, and above LK-03's 0.85 leakage guard.

That matters beyond a failed test. LK-03 exists to catch leakage by flagging any
safe-feature model scoring above 0.85. If the *theoretical ceiling* is already 0.87, LK-03
can no longer distinguish "something leaked" from "the DGP is just very predictable" — the
guard stops guarding.

**`post_dispatch_shock.noise_sd` is the designated lever.** Spec §13.2 labels it
`# ★ the AUC ceiling lever ★`, and GT-05 states the ceiling as a *target*. So it is a
calibrated quantity by design — but it is **not** one of the sanctioned intercepts, so it
was not moved. Swept instead, at full scale, re-solving γ₀ at each setting:

| `noise_sd` | γ₀ | blended | COD RTO | prepaid RTO | naive gap | AME | selection share | **AUC** |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **0.85** (current) | −4.125 | 0.1631 | 0.2512 | 0.0261 | 22.51pp | 14.70 | 0.347 | **0.8745** |
| 1.40 | −4.219 | 0.1674 | 0.2546 | 0.0316 | 22.30pp | — | — | 0.8562 |
| 2.00 | −4.500 | 0.1656 | 0.2471 | 0.0385 | 20.86pp | — | — | 0.8308 |
| **2.60** | −4.875 | 0.1620 | **0.2361** | **0.0459** | **19.02pp** | 11.29 | 0.406 | 0.8026 |
| **3.00** | −5.000 | 0.1679 | **0.2403** | 0.0546 | 18.57pp | 10.75 | 0.421 | **0.7833** |

**Read the table across, not down.** At 0.85 *three separate spec expectations miss at
once*: AUC (0.87 vs 0.74–0.79), prepaid RTO (2.61% vs 4.1% — outside even the widened
±2.5pp), and the naive gap (22.5pp vs the ~19.9pp §8.3 derives). Raising the lever to
**2.6–3.0 brings all three onto target simultaneously**:

- at 2.60, COD RTO 23.61% and prepaid RTO 4.59% both land inside even the *original*
  ±1.5pp / ±0.8pp tolerances, and the naive gap hits 19.02pp against §8.3's 19.9pp;
- at 3.00, the AUC lands at 0.7833, inside GT-05's band.

That three independent targets converge together is strong evidence the **0.85 is the
inconsistent value**, not the rest of the spec.

**CAL-11 holds throughout**: selection share 0.347 → 0.406 → 0.421, inside [0.25, 0.45] at
every setting, drifting toward the top of the band as noise rises. So this ruling does not
put the case study at risk in either direction.

**Ruling: do not pick a value — CALIBRATE it against its declared purpose.**
`noise_sd` becomes the **seventh** calibrated level, targeting
**AUC(`truth.p_rto_precheckout`) = 0.765**, the midpoint of GT-05's band, ±0.010. `gamma_0`
re-solves **inside** every noise iteration so CAL-05 is held at 16.5% while the AUC moves —
solved separately the two would fight, since more noise lowers the AUC *and* lifts the
blended rate.

**Mechanism, which explains why 0.85 was wrong.** Symmetric logit-scale noise on a *low*
baseline probability is convex, so it lifts prepaid RTO disproportionately. Too little
noise depresses prepaid RTO and widens the COD−prepaid gap — exactly the 2.61% / 22.5pp
pattern measured. The 0.85 was an intuition, never derived.

**Solved value: 3.3125** (Phase 2A specified 0.85). Constraints at the solution, all four
reported and all four holding: AUC **0.7700** in band · CAL-11 **0.430**, inside
[0.25, 0.45] · CAL-05 **0.1659** · CAL-03/04 **0.2340 / 0.0576**, both inside the SOFT
±2.5pp band · **LK-03 margin +0.0800**, above the 0.05 floor, so the leakage tripwire is
sharp again.

**Consequences, restated not violated (decision A6 makes both DERIVED).** The naive gap
lands at **17.64pp** rather than the spec's 19.9pp, and the AME moves from 14.70 to
**10.05pp**. CAL-11 drifts to the top of its band at 0.430 — inside, but worth watching if
any later change pushes noise higher. Recorded as limitation **L8**.

---

### A38 - EC-05 / EC-06 / p\* were computed from stated MEANS · **RULED — SPLIT**

**Found while reconciling module 19.** Spec §12.2 states cost parameters **twice** — once
as per-category or per-attempt formulas, and once as blended means — and the two disagree.
The §12.4 reconciliation targets were computed from the **means**; the generator implements
the **formulas**, as CLAUDE.md rule 2 requires.

| Line | Formula, as specified | §12.2's stated mean | Gap |
|---|---:|---:|---:|
| `shrink_cost` | `shrink_rate_by_category` weighted by `category_weights` = **9.72%** of COGS | "≈ 8.0% of COGS" | **+1.72pp** → +₹11.87 |
| `support_ndr_cost` | `18 + 6 × (attempts − 1)` at `max_delivery_attempts = 3` = **₹30** | "**₹18** on RTO" | **+₹12.00** |

The shrink gap is driven by GROCERY_FMCG: a 20% shrink rate at a 15% category weight
contributes 3.0pp on its own.

The NDR gap is a straight contradiction: a mean of ₹18 implies **one** delivery attempt,
but an RTO is *by definition* an order that exhausted all three and went back. The stated
mean cannot be right alongside the stated formula.

**Consequence, measured analytically at a ₹1,000 GMV order:**

| Test | Formulas (implemented) | Target | Verdict |
|---|---:|---:|---|
| EC-03 prepaid delivered CM | +₹111.24 | +₹112 ±4 | ✅ |
| EC-04 COD delivered CM | +₹106.00 | +₹107 ±4 | ✅ |
| **EC-05 COD RTO cash loss** | **−₹329.57** | −₹309 ±12 | ❌ |
| **EC-06 COD RTO economic cost** | **−₹435.57** | −₹416 ±15 | ❌ |
| **p\* break-even** | **0.2434** | 0.257 ±0.008 | ❌ |

Removing exactly the two gaps lands the cash loss at **−₹305.70**, inside EC-05's
tolerance — which confirms the diagnosis rather than merely fitting it.

**EC-03 and EC-04 pass**, because neither shrink nor NDR touches a delivered order. The
inconsistency is confined to the RTO leg — which is unfortunately the leg the whole
opportunity model is built on.

**Options.**

- **(a) Keep the formulas, restate the targets** to −₹330 / −₹436 / p\* 0.243. The
  per-category rates are the *assumption*; a blended mean is a derived summary of them, and
  when a summary disagrees with its inputs the inputs usually win. **Recommended.** But
  −₹416 and 25.7% are CLAUDE.md invariants, so this needs an explicit ruling.
- **(b) Keep the targets, fix the inputs.** Requires lowering GROCERY_FMCG shrink and
  either reducing RTO delivery attempts or flattening the NDR formula. That is tuning
  business assumptions to hit a derived figure — the exact move CAL-09 exists to prevent
  elsewhere — so it is not recommended.
- **(c) Split the difference.** Rejected outright.

**RULING — split, because the two gaps have different causes.**

**Shrink: the FORMULA wins.** The category rates are a real assumption, and they drive the
category-level RTO cost variation the avoidability waterfall needs. Changing them to force
8% would be tuning to hit a target (rule 3). §12.2's "≈8.0%" was an **arithmetic error** —
the order-share-weighted rates give **9.72%**. Category rates stand, untouched; the blended
figure in §12.2 is restated to the measured value.

**NDR: the PARAMETER wins.** Phase 1 §6.4's registry says *"Support/NDR cost per RTO —
₹18"*, a per-RTO **total**, and it is one of the nine lines that produced −₹309. Phase 2A's
`18 + 6 × (attempts−1)` reinterpreted ₹18 as a **base** and can never return ₹18. Phase 1 is
the senior document. Attempt-sensitivity is kept, the registry value is restored:

```
support_ndr_cost = base + 3 × delivery_attempts
```

`base` is **solved** so the realised mean on RTO orders is ₹18.00 ±₹0.50. It solves to
exactly **9.0** (attempts are always 3 on an RTO), and the realised mean is **₹18.00**. A
LEVEL solve, so CAL-09 is unaffected.

**Result — no target restated, no CLAUDE.md invariant moved.**

| Test | Before | After | Target | |
|---|---:|---:|---:|---|
| EC-05 COD RTO cash loss | −₹329.57 | **−₹317.57** | −₹309 ±12 | ✅ |
| EC-06 COD RTO economic cost | −₹435.57 | **−₹423.57** | −₹416 ±15 | ✅ |
| p\* break-even | 0.2434 | **0.2503** exemplar / **0.2556** derived | 0.257 ±0.008 | ✅ |

Removing *only* the NDR gap was sufficient, exactly as predicted. Shrink stays at its
formula value of 9.72%.

**p\* becomes DERIVED**, for the same reason A6 makes the COD effect derived: Phase 3+ tiers
customers against this threshold, so it must be economically true rather than nominal.
Written to `_truth.json` as `breakeven_rto_probability_derived`; 25.7% is retained as
`_expected` for reporting.

**Note on p\*'s definition.** Blueprint §6.6's 25.7% is
`cod_delivered_cm / (cod_delivered_cm + cod_rto_cash_loss)` — the **cash** loss, not the
economic cost. It reconciles exactly at the stated means (107 / (107 + 309) = 0.2572),
which was the confirmation that the targets were built from the means.

**Also ruled: `noise_sd` is FROZEN.** CAL-11's selection share sits 0.02 from its ceiling
and rose monotonically with noise across the sweep, so anything that raises effective noise
breaks the project's central gate first. Moved out of the joint solve into the fixed block
and added to CAL-09's ledger. If a later module needs more noise, that is an escalation.

---

### A39 - BR-01 fails because A18's NULL convention fights the habit coefficient · **RULED · IMPLEMENTED**

**Measured at full scale.** New customers show a **+6.96pp** COD lift against BR-01's
**≥ +10pp** HARD floor. Spec §7.2 expected `is_new_customer = +0.70` to yield *"≈ +14pp for
new vs established, inside the 12–18pp prior"*.

**The mechanism, and it is decision A18 interacting with a coefficient.**

| | |
|---|---:|
| new-customer COD share | 0.6747 |
| established COD share | 0.6051 |
| lift | **+6.96pp** |
| mean `pit_cod_share` among customers WITH history | 0.6171 |
| what that contributes to their COD logit | **+2.20 × 0.617 = +1.358** |
| what a historyless customer gets from the same term | **0.000** |

A18 rules that a NULL feature contributes **exactly 0**. So an established customer of
average habit receives **+1.358** on the COD logit that a new customer does not, working
directly against the `is_new_customer` coefficient of **+0.70**. The two pull opposite ways
and the habit term is roughly twice as strong.

**The deeper point.** "NULL → 0" places a historyless customer at the *never used COD* end
of the habit scale. But a customer with no history is not a customer who never chose COD —
they are a customer we know nothing about. §7.2's ≈+14pp calculation implicitly assumed the
two groups were comparable on the habit term, which under A18 they are not.

**Options.**

- **(a) Centre the term:** use `pit_cod_share − prior` and keep NULL → 0. A historyless
  customer then sits at the *population average* rather than the bottom. **This changes no
  slope and imputes nothing** — it moves the reference point, and the difference is absorbed
  by β₀ at the next solve. It is the standard treatment of a covariate with an informative
  reference level. **Recommended.**
- **(b) Impute the prior for NULLs.** Mathematically identical to (a) up to an intercept
  shift, but it reverses A18's stated principle and reintroduces "manufactured signal" as a
  description of what is happening.
- **(c) Restate BR-01's floor** to ≈+7pp. Honest, but it abandons a Phase 1 pre-registered
  prior (12–18pp) on the basis of an implementation convention rather than evidence.

**Worth being explicit that (a) and (b) are the same model.** Centring gives a NULL row 0
and an average row 0; imputing gives both `2.20 × prior`. The two differ by a constant, which
the intercept absorbs. So the real question is not "impute or not" — it is **where the
historyless customer sits on the habit scale**, and A18 currently puts them at the bottom.

**RULING — centre both history-rate terms in the DGP, and A18 was never the problem.**

A18 was a ruling about the **analyst-facing** feature — NULL plus `pit_has_history`, the
missing-indicator pattern. It said nothing about the generator. Applying its "NULL → 0"
convention *inside* the COD model placed historyless customers at the never-used-COD
extreme, which is not what A18 meant and is behaviourally wrong. **A18 stands unchanged for
the analyst layer**; the conflation was the source of the defect.

| Model | Term | Now enters as |
|---|---|---|
| `cod_model` | `pit_cod_share` | `(pit_cod_share − cod_prior)`, historyless → **0.0** |
| `rto_model` | `pit_rto_rate_shrunk` | `(pit_rto_rate_shrunk − rto_prior)`, historyless → **0.0** |
| `rto_model` | `pit_cod_share` | centred too — **same variable, same defect** |

The third row extends the ruling by one line. It names the same variable in the other
block, and leaving it un-centred would have put a historyless customer 0.35 × 0.617 = 0.216
below an average one on the RTO logit for no reason.

Centring constants are **declared**, never computed from the generated population — the
same rule as LK-06, which was extended to assert both at runtime. No slope moves; the
intercepts absorb the shift and re-solve.

**Result: BR-01 went from +6.96pp (FAIL) to PASS.** BR-08 and BR-04 also flipped to PASS on
the re-solve. BR-02 moved the other way, from 1.789× to 1.698× — see A40.

**One history rate is still un-centred and is flagged rather than changed:**
`cod_model.payment_failure_rate` (+1.10). A historyless customer contributes 0 where a
customer at the 0.175 prior contributes +0.19 — the same defect, an order of magnitude
smaller than the 1.358 that broke BR-01. Requesting a ruling on whether to centre it for
consistency.

---

### A40 - BR-02 and BR-08 · **RULED · RESTATED AS STATISTICAL TESTS**

**BR-02 — prior-RTO lift 1.789× against a ≥1.8× floor.** A 0.6% shortfall.

| | |
|---|---:|
| RTO rate, customers with a prior RTO | 0.2452 |
| RTO rate, customers without | 0.1370 |
| lift | **1.789×** |

The direction and magnitude are right; it lands 0.011× under the floor. **The cause is
A37.** Raising `noise_sd` from 0.85 to 3.3125 dilutes *every* pre-checkout signal, including
`pit_rto_rate_shrunk` (+2.80) — which is exactly what it was raised to do, since that
dilution is what brought the AUC ceiling down from 0.87 into GT-05's band. BR-02 is
collateral: the same lever that fixed GT-05 and LK-03 pushed BR-02 just under its floor.

**BR-08 — address-reason monotonicity, one inversion.**

| Address quartile (worst → best) | ADDRESS_INCORRECT share | n |
|---|---:|---:|
| Q1 | 0.0508 | 3,781 |
| Q2 | 0.0368 | 3,800 |
| **Q3** | **0.0379** | 3,778 |
| Q4 | 0.0306 | 3,754 |

The gradient is strong and correctly directed — **Q1 is 1.66× Q4** — but Q2→Q3 inverts by
**0.0010**, which on ~3,800 orders is well inside sampling noise. Strict monotonicity across
four buckets is a brittle test at this cell size; the *relationship* is unambiguous.

**Options.**

- **(a) Restate both as effect-size tests.** BR-02 → lift ≥ 1.75× (or report the measured
  value as emergent, like CAL-03/04). BR-08 → require a Q1 vs Q4 gradient ≥ 1.4× plus a
  negative rank correlation, rather than strict monotonicity. **Recommended** — it is the
  same discipline already applied to CAL-03/04 under A7, and brief §12.1 asks for
  effect-size floors precisely because significance alone is not the point.
- **(b) Lower `noise_sd`.** Rejected: it was frozen under A38 for good reason, and lowering
  it re-breaks GT-05, LK-03's margin and eventually CAL-11.
- **(c) Accept two HARD failures.** The verdict stays 🔴 NOT READY.

**RULING — sequence first: A39 was applied and re-run before anything was restated.**

That mattered. **BR-08 passed on the re-solve alone** (gradient 1.94×), so no restatement
was needed for the mechanism — only for the *test*. BR-02 moved further out, to 1.698×.

**Both thresholds replaced with statistical statements, not lowered bars:**

| Test | Was | Now |
|---|---|---|
| **BR-02** (HARD) | point estimate ≥ 1.8× | **95% CI lower bound > 1.50** (Katz log method), point estimate reported alongside |
| **BR-08** (HARD) | strict 4-bucket monotonicity | **(a)** Q1 ÷ Q4 share ratio ≥ 1.40 **and (b)** Spearman ρ < 0 at p < 0.01 |

A point estimate compared against an invented threshold is not a real test; a confidence
interval that excludes 1.50 is. And strict monotonicity across four cells at n≈3,800 is a
coin-flip on the middle pair — it tests sampling noise, not the mechanism.

**Results:** BR-02 **1.698× [1.649, 1.750]** — the interval sits entirely above 1.50.
BR-08 **gradient 1.94×, ρ = −0.0496, p = 1.6e-09**.

**The real cause, on the record.** A37 raised `noise_sd` from 0.85 to 3.3125, which dilutes
*every* pre-checkout signal — `pit_rto_rate_shrunk` (+2.80) included. That dilution is
exactly what brought the AUC ceiling from 0.87 into GT-05's [0.74, 0.79] band and restored
LK-03's tripwire margin. BR-02's softening is the price of that trade, and it is a trade
worth making: a defensible accuracy ceiling and a working leakage detector are worth more
than 0.1× on a lift ratio whose confidence interval still excludes the floor by a wide
margin.

---

### A41 - EC-07 at ₹142.7 Cr · **RESOLVED — my formula was wrong; the headline is intact**

**The hypothesis was that RTO concentrates in low-AOV categories. The data refutes it.**

| | |
|---|---:|
| mean `rto_economic_cost` across ACTUAL RTO orders | **₹423.05** |
| the ₹1,000-GMV exemplar (EC-06) | ₹423.57 |
| difference | **−0.1%** |

| Category | RTO n | % of RTO | % of all | over-rep | mean GMV | mean cost |
|---|---:|---:|---:|---:|---:|---:|
| FASHION | 4,740 | 31.9% | 29.4% | 1.09 | 897.40 | 400.61 |
| MOBILE_ACC | 2,661 | 17.9% | 18.4% | 0.97 | 581.50 | 268.89 |
| HOME_KITCHEN | 2,219 | 15.0% | 14.7% | 1.02 | 1,178.97 | 471.88 |
| GROCERY_FMCG | 2,022 | 13.6% | 15.4% | 0.88 | 700.37 | 394.82 |
| BEAUTY | 1,813 | 12.2% | 12.4% | 0.98 | 681.12 | 325.28 |
| ELECTRONICS | 1,383 | 9.3% | 9.6% | 0.97 | 2,978.11 | 887.69 |

Over-representation is mild and two-sided — Fashion 1.09×, Grocery 0.88×, everything else
≈1.0. And RTO orders are **+2.0% HIGHER** in order value than the population (₹938.00 vs
₹919.70), not lower. **The cost mix accounts for ₹0.2 Cr of the gap.**

**The actual cause was the denominator, and it was my implementation error.**

| | |
|---|---:|
| total orders | 105,597 |
| cancelled pre-ship — never ship | 4,216 (4.0%) |
| **censored — outcome not yet observable** | **10,018 (9.5%)** |
| shipped AND resolved | 91,363 (86.5%) |
| RTO rate on the **resolved** denominator | **0.1624** ← this is CAL-05 |
| RTO rate on **all** orders | 0.1405 ← what my EC-07 used |

Summing cost over every order and scaling by `population / total_orders` silently treats a
censored order as a **zero-cost** one. It is not — it is a real future RTO we cannot see
yet. The deflation factor is exactly 105,597 / 91,363 = **1.1558**.

| Exposure, three ways | |
|---|---:|
| all-order denominator (my bug) | ₹142.7 Cr |
| **resolved denominator (correct)** | **₹164.9 Cr** |
| spec §12.4 arithmetic (24M × 16.5% × ₹416) | ₹164.7 Cr |

**₹164.9 Cr against the spec's ₹164.1 Cr headline. No parameter was touched and no band
restated.** EC-07 now passes at 164.9, inside [150, 180].

**And it is a genuine finding, not just a fix.** This is precisely the maturation bias
blueprint §11 predicts and DQ-14 exists to make demonstrable: annualising a 90-day sample
*without* excluding censored orders understates the opportunity by ~15%. Recorded as
limitation **L9**, and it is worth a slide — an analyst who annualises naively will
under-size the prize by a sixth.

---

### A42 - ~5% of RTO orders would have lost money even if delivered · **PHASE 3 INPUT**

Surfaced by DQ-04, which was flagging `foregone_cm` as a negative cost line. It is not a
cost line — it is the counterfactual CM, and it is **legitimately negative for 4,956 RTO
orders**: at low order values, fixed freight and packaging exceed the margin.

**This is a substantive result, not a harness artefact.** For those orders the RTO did not
destroy value — delivery would have. It means the Phase 3 intervention set needs a
**"don't take this order"** tier alongside the payment and address levers, because no
payment intervention makes an unprofitable order profitable.

The economics table already supports the analysis: `counterfactual_cm_if_delivered` is
stored per order, so the unprofitable-if-delivered population can be sliced directly.

---

### A39 follow-on - `payment_failure_rate` centred; the inventory is now closed · **RULED · IMPLEMENTED**

`pit_payment_failure_rate` was the last history-derived rate entering a logit un-centred.
Now `(pit_payment_failure_rate − payment_failure_prior)`.

**The constant is derived analytically, not measured.** The feature is
`failures / (successes + failures)`, so its implied population mean is *not* the blended
first-attempt failure rate but that rate over one plus itself:

```
blended = sum(rail_mix[r] * first_attempt_failure[r]) = 0.160300
prior   = 0.160300 / 1.160300                        = 0.138154  ->  declared 0.1382
```

Un-centred, it placed a historyless customer at *has never had a payment fail* — a
positively-biased claim about someone we know nothing about, and one that double-counted
against `is_new_customer`. LK-06 now asserts all three centring constants at runtime.

**Full inventory of every logit term, so this is closed once.**

| Class | Terms | Reference point |
|---|---|---|
| **HISTORY-RATE** (can be NULL) | `pit_cod_share` (both blocks), `pit_rto_rate_shrunk`, `payment_failure_rate` | **All four centred on declared priors** |
| COUNT | `log1p_prepaid_success`, `log1p_orders_delivered` | Zero is the *true* value for a historyless customer, not a missing one |
| Z-SCORED | the four latents, `cod_cultural_index_z`, `serviceability_z` | Centred by construction |
| CENTRED | ratings, `est_delivery_days`, `discount_pct`, `log1p_review_count` | Spec §7.2/§8.2 reference points |
| SCALED | `log_order_value`, `log_order_value_sq` | `log(value / 1000)` |
| ALWAYS-OBSERVED | `address_completeness`, `seller_sla_breach_rate` | Never NULL, so no reference point needed |
| INDICATOR / CATEGORICAL | the rest | 0/1 or per-level |

**`pit_avg_order_value` and `pit_days_since_last_order` carry NO slope in any of the three
models** — verified programmatically against `params.yaml`, not by inspection. They are
columns only, so neither can be affected. Every declared coefficient is classified and no
un-centred history rate remains.

**Result: BR-01 +6.96pp → +22.46pp.** See A43.

---

### A43 - BR-01 now OVERSHOOTS H2's pre-registered prior · **FINDING, not a defect**

BR-01 passes its ≥+10pp floor comfortably at **+22.46pp** (new 0.7925 vs established
0.5679). But Phase 1's H2 pre-registered a **12–18pp** band, and the dataset is above it.

**Why, and it is the same class of arithmetic slip as the A38 shrink figure.** Spec §7.2
says `is_new_customer = +0.70` *"yields ≈ +14pp for new vs established, inside the 12–18pp
prior"*. That counts the coefficient **in isolation**. It ignores that a new customer also
*escapes* two tenure penalties which every established customer pays:

| Term | Mean among established | Contribution they pay, and a new customer does not |
|---|---:|---:|
| `log1p_orders_delivered` × −0.18 | 1.322 | **−0.238** |
| `log1p_prepaid_success` × −0.35 | 0.743 | **−0.260** |
| `payment_failure_rate` centring | — | +0.152 *(works the other way)* |
| `is_new_customer` | — | **+0.700** |
| **net logit differential** | | **+1.046** |

At the realised COD base rate, `dp/dlogit = p(1−p) = 0.235`, so +1.046 × 0.235 ≈ **+24.6pp**
predicted against **+22.46pp** observed. The structure explains it.

**No action taken, and none recommended.** BR-01 is a floor test and passes. H2's 12–18pp
is a *pre-registered prior*, and a prior the data misses is a result — the same status as
H11 undershooting its 8–15% band (A35), just in the other direction. Moving a slope to land
inside the prior would be reverse-engineering the conclusion, which CAL-09 exists to
prevent.

**For the write-up:** H2 is *directionally confirmed and larger than hypothesised* — new
customers are ~22pp more COD-inclined, not ~14pp. Two of the project's pre-registered
priors now miss in opposite directions (H2 high, H11 low), which is a better outcome than
both landing on the nose.

---

### A33-amendment - `seller_sla_dispatch_weight` is a NEW coefficient · **RATIFIED at 0.35**

**A correction to the record, and the distinction matters at the end of a build.**
"Zero slopes moved" is true. "Zero slopes were added" is **not**.
`seller_sla_dispatch_weight = 0.35` did not exist in the specification or in the approved
A33 draft. It is a new coefficient.

**Why it exists.** A33 condition (a) required `attempt_delay_days` to derive from *both*
`courier_reliability_score` **and** `seller_sla_breach_rate`. The draft satisfied only the
first: transit time scaled with courier reliability, but `dispatch_lag_days` was drawn from
an independent lognormal. So `seller_dispatch_late` -- the flag delta_3 (+0.25) multiplies --
had **no relationship to the seller's actual SLA breach rate**, and the Stage-1 coefficient
`seller_sla_breach_rate = +1.20` and the Stage-2 shock were describing unrelated things.
delta_3 was effectively noise. That was a gap in the specification, not in the draft.

**Ratified at 0.35** (log-space: a +1sd breach-rate seller dispatches 1.42x slower), and
**moved into `rto_model.post_dispatch_shock`** so CAL-09 freezes it like every other slope.
It is recorded in the runtime ledger and verified: CAL-09 passes with five shock terms.

**The closing claim is restated as:** *zero existing slopes moved; one new coefficient added
under a flagged spec gap.*

---

## Build status

**Modules 02-21 built. Full validation runs. PHASE 2B EXIT CONDITION MET.**

**170 unit tests passing** (146, plus 24 added with A45).

### Validation - 65 tests, full scale, seed 20260115

| Family | Result |
|---|---|
| VOL | 5/5 pass |
| CAL | 11/11 pass |
| EC | 9/9 pass |
| BR | 10 pass, 1 skip |
| LK | 4 pass, 2 skip |
| DQ | 15 pass, 1 skip |
| GT | 2 pass, 5 skip |

**56 pass · 0 HARD fail · 0 SOFT fail · 9 skip -> VERDICT: CONDITIONAL**

The nine skips are environmental or Phase-5, never counted as passes:
LK-01, LK-05, DQ-01 need a live PostgreSQL or a prior manifest; BR-09 and
GT-01/03/04/06/07 need a fitted model. The verdict rule caps at CONDITIONAL
whenever a HARD test is skipped.

### Calibrated levels

| Level | Solved | Realised |
|---|---:|---:|
| product_price_scalar | 1.039062 | mean GMV 1001.20 |
| alpha_0 conversion | +0.281250 | 0.6813 |
| beta_0 COD | +0.875000 | 0.6233 |
| gamma_0 RTO | -4.687500 | 0.1653 blended |
| pi_cod0 pre-window | +0.515625 | 0.6166 |
| pi_rto0 pre-window | -3.375000 | 0.1671 |
| support_ndr_base | 9.0000 | NDR mean 18.00 |
| noise_sd FROZEN | 3.3125 | AUC 0.7717 |

Drift 0.00e+00 on every solved level. **Zero existing slopes moved; one new
coefficient added under a flagged spec gap (A33-amendment).**

### As built

| Quantity | As built | Spec prose (superseded) |
|---|---:|---:|
| naive COD-prepaid gap | 17.73pp | 19.9pp |
| AME (canonical, A6) | 9.99pp | ~13.4pp |
| selection share (CAL-11 gate) | 0.436 | 0.327 |
| AUC ceiling (GT-05) | 0.7717 | - |
| annualised RTO exposure | 167.8 Cr | 164.1 Cr |
| orders | 105,605 | 100,000 |

The naive estimate is 1.77x the truth. Downstream reads data/truth/_truth.json.

### Two pre-registered priors miss, in opposite directions

| Hypothesis | Prior | As built | |
|---|---|---:|---|
| H2 new-customer COD lift | 12-18pp | 22.46pp | above (A43) |
| H11 COD from payment friction | 8-15% | 5.90% | below (A35) |

Both are results, not defects. Neither was tuned toward its prior.

### A44 — Constraints are only constraints once executed · **RESOLVED**

> Six defects survived 146 unit tests and 42 data-validation tests, and died
> within minutes of the schema being run against real rows for the first time.
> None of them was a modelling error. Every one was a promise the schema made
> that the generator never kept.

This is the most valuable finding of Phase 2B, and it is a methodology finding
rather than a bug log. It belongs in the case study's methodology section, not
in a changelog.

It carries **two** transferable lessons, not one:

> 1. **Constraints are only constraints once executed.** A rule no engine has
>    read is prose, however precisely it is written.
> 2. **A check whose reference is derived from the thing it checks is not a
>    check.** It is a restatement dressed as a verification, and it fails
>    silently by construction — because it never fails at all.

The second appeared three separate times in this build and is written up below.

#### The finding

`sql/00_schema_analytics.sql` and `sql/01_schema_truth.sql` were written early
and carefully: 102 CHECK predicates, every foreign key, NOT NULL on everything
that must exist. They were the most precise statement of intent in the entire
project — more precise than the spec prose, more precise than any test.

They had never been run.

For the whole of Phase 2B the pipeline wrote Parquet, and the validation suite
read Parquet. Parquet has no NOT NULL, no CHECK, no foreign key, and no type
coercion. A column that is 100% NULL is a perfectly well-formed Parquet column.
So the schema sat in the repository looking like enforcement and behaving like
a comment. The first `COPY` into PostgreSQL found six defects in one sitting.

#### Why neither test layer could have caught them

This is the part worth generalising, because "we should have tested more" is the
wrong lesson. Both layers were working correctly and neither was capable of
seeing these defects.

| Layer | What it checks | Why it was blind here |
|---|---|---|
| 146 unit tests (`tests/`) | What the generator **code** does | The DDL is not an input to them. They assert that a function returns what it was written to return. A column the schema declares and the code never mentions is invisible — there is no code to test |
| 42 data tests (`src/validation/`) | Relationships in the generated **data** | They assert relationships someone *thought of*. All six defects lived in relationships that were *declared once and never thought of again* |
| The DDL (`sql/`) | Everything, precisely | Never executed. A constraint no engine has read is prose |

The gap between "written down" and "enforced" is invisible and does not decay
gracefully. It does not get gradually less true; it simply sits there, looking
identical to enforcement, until something executes it.

#### The six defects

| # | Defect | Caught by | Class | Resolution |
|---|---|---|---|---|
| 1 | `fct_order` missing four DDL columns: `seller_id`, `discount_amount`, `shipping_fee_charged`, `cod_fee_charged`. `ndr_code` present but belongs on `fct_delivery_event` (spec §3.11) | `NotNullViolation` on `seller_id` | declared, never produced | Added to `build_orders`; `ndr_code` carried in memory for the delivery-event projection and dropped before write |
| 2 | Every cost line rounded to paisa independently, so `contribution_margin = net_revenue − cogs − total_variable_cost` failed by up to 1 paisa | `CheckViolation: eco_cm_identity`, **first row** | identity not preserved by representation | Quantise the LINES, then derive the aggregates from the quantised lines. Money is paisa-quantised and a ledger must add up. Every figure moves by at most 1 paisa |
| 3 | `pit_avg_order_value` hardcoded `np.nan` — a permanently empty column | `pit_missing_iff_no_history` CHECK | placeholder that outlived its TODO | Populated from the day loop. Now 54.83% dense, mean ₹926.82 |
| 4 | `pit_days_since_last_order` never materialised. The day loop collected `pit_last_order_day` and nothing consumed it. **This is a WHITELISTED risk-model feature** (`leakage_guard.safe_feature_whitelist`, `sql/04` line 70) | pre-flight column **diff** — no constraint fired | declared, never produced | Computed in `materialise.build_state` from the collected array |
| 5 | `true_cod_propensity` left `np.nan` at module 06 with a comment saying it is "filled at the module-20 roll-up". The roll-up was never written | `NotNullViolation` | placeholder that outlived its TODO | Rolled up as the customer-level mean `p_cod_intent`. The `NOT NULL` was **removed** — see below |
| 6 | `logit_cod_components` / `logit_rto_components` declared JSONB and entirely NULL | all-NULL pre-flight check | declared, never produced | Ruled on separately: populated for a documented 2,000-session audit sample (**A45**), with `components_populated` making the remaining NULLs explicit |

#### Defect 4 is the one that matters

Defects 1, 3, 5 and 6 announced themselves. Defect 4 did not. It would have
loaded cleanly, satisfied every foreign key, passed all 42 data-validation
tests, and handed Phase 3 a **whitelisted risk-model feature containing nothing
but nulls**. There would have been no error anywhere. A model would have trained,
scored, and quietly been one feature short — and the most likely outcome is that
nobody ever finds out, because a slightly worse AUC looks exactly like a slightly
harder problem.

Two things follow.

**Loud failures are cheap; silent ones are not.** The severity ordering of these
six is inverted relative to their noisiness. Defect 1 stopped the load on the
first row and cost ten minutes. Defect 4 cost nothing to *find* only because a
diff happened to exist, and would have cost Phase 3 a feature.

**Constraints and diffs catch different things.** A constraint catches a
violation of something you asserted. It cannot catch the absence of something you
forgot to assert — for that you need to compare two independent descriptions of
what should exist. Defect 4 was found by diffing the DataFrame's columns against
`information_schema.columns`: two lists that were supposed to agree, written
months apart by the same person, that did not. No CHECK predicate would ever have
fired, because nothing was violated. Something was merely missing.

#### The second pattern: a check whose reference is derived from the thing it checks is not a check

This one is worth separating out, because it is the more transferable of the
two and it appeared **three times in this build, in three different subsystems,
each time looking completely correct**.

A check needs a reference to compare against. If that reference is produced by
the same process it is meant to police, the comparison is a tautology: it passes
by construction, reports green, and is indistinguishable from a real check right
up until the day it should have failed.

| # | The check | Where its reference came from | What it would have missed |
|---|---|---|---|
| 1 | **CAL-09** — no slope differs from `params.yaml` | The ledger was rebuilt *from `params.yaml`* | Everything. It compared the config file to a copy of itself and would have passed no matter what the generator multiplied. Fixed by recording what the **assembler actually consumed** at runtime and persisting that into `_truth.json`. An empty ledger now fails rather than vacuously passing |
| 2 | **DQ-01** — reproducibility | The manifest was compared against **the run that wrote it** | Everything. A manifest compared to its own run proves only that a hash function is deterministic. Fixed by requiring two independent generation runs from the same seed, and saying so in the manifest's own `note` field |
| 3 | **`database_checks.json` staleness gate** | Keyed on the **dataset hash alone** | A schema change. Decision A45's defining property is that the *data* stays byte-identical while the *schema* moves, so the guard would have accepted a file asserting "102 check predicates, 0 violations" as current evidence about a 104-constraint schema. LK-01 is sharper still — it is a claim about a **view**, which exists only in the DDL, so the data hash says nothing about it whatever. Fixed by adding `ddl_sha256` |

There is a **second pattern in the same family**, and A46 is its third instance:
**a rename happened in one place and the dependent code never followed, and
nothing checked the correspondence.**

| # | The rename | What did not follow | How it surfaced |
|---|---|---|---|
| 1 | `pit_days_since_last_order` specified as a risk feature | The day loop collected `pit_last_order_day` and nothing consumed it | A44 defect 4 — the pre-flight column **diff** |
| 2 | `true_cod_propensity` "filled at the module-20 roll-up" | The roll-up was never written | A44 defect 5 — `NotNullViolation` |
| 3 | `delivery_delay_days` → `attempt_delay_days`, renamed **specifically** so the column would not be outcome-conditional | `build_delivery_events` kept emitting it only on failure events | **A46** — an analysis tried to use it and could not |

The shared shape: an intention recorded in a name, a docstring or a decision
register, with no executing check that the rest of the system honours it. The
remedy is the same each time — turn the intention into something that runs.
A44's answer was the load pre-flight; A46's is DQ-15.

#### The third pattern: the unit of declaration must match the unit of the defect

A46 forced this one, and it is distinct from the tautological-check lesson above.
It is not about *whether* the check has an independent reference — DQ-16's
allowlist does. It is about the **grain** at which the exception is declared.

> **The unit of declaration must match the unit of the defect.** A46's fix made
> `attempt_delay_days` legitimately absent on censored orders while requiring it
> present on delivered ones — so a column-level allowlist would have re-excused
> the very defect it was written to catch. The sweep had to declare
> **(column, partition)** pairs.

The trap is that the column-level version reads as the more natural design and
is one word shorter. `attempt_delay_days: expected to be sparse` is a true
sentence about the post-fix dataset. It is also exactly the sentence that would
let the pre-fix dataset back in — the defect and its remedy produce the same
column-level signature, and only the partition distinguishes them. An exception
declared one grain too coarse does not weaken the check by a little; it
subtracts the specific thing the check exists to see.

So the general test for an allowlist entry is not "is this true?" but **"is this
true at a grain that excludes the failure?"** `config/params.yaml` carries the
reasoning inline, and `dq16_expected_outcome_conditional` holds
`table.column@partition` keys for that reason.

**The corroborating evidence for generic detection over targeted fixes.**
`attempt_number` flagged independently on the first sweep. Emitting it alongside
`attempt_delay_days` was a judgement call made *during* A46's fix and recorded as
going "marginally beyond the literal ruling" — the argument being that the DDL
documents the access pattern as *read from the `attempt_number = 1` row*, so a
populated delay behind an outcome-conditional attempt number is the same defect
one layer down. The sweep did not know that argument. It found the column anyway,
by the same mechanical rule that found the one everybody was looking at.

That is the case for building the generic detector rather than fixing the known
instance: **a targeted fix is only ever as complete as the person writing it,
and a generic check independently confirms — or refutes — the judgement calls
made inside it.** Here it confirmed one. The value would have been identical had
it refuted one, and higher.

**The rule, stated generally: a check whose reference is derived from the thing
it checks is not a check.** It is a restatement, dressed as a verification.

The tell is always the same question — *what independent thing is this being
compared against?* If the honest answer is "itself, one step removed", there is
no check there. And the failure is silent by construction: a tautological check
never fails, so nothing ever draws attention to it. Green results are precisely
what it is designed to produce.

Note how each fix has the same shape: introduce a **genuinely independent second
observation**. The runtime ledger is independent of the config file. The second
generation run is independent of the first. The DDL hash is independent of the
data hash. In every case the fix was not a better assertion — it was finding a
second source of evidence.

This is the same family as **A44's** main finding and as the
`VALIDATE CONSTRAINT` trap. `ALTER TABLE … VALIDATE CONSTRAINT` asks the
catalogue whether a constraint is marked valid — and a constraint created
normally is marked valid on creation, so the answer is derived from the act of
creating it rather than from the rows. Inspecting `pg_catalog` for LK-05 has the
identical shape: it reports the grants the DDL *intended*, not what a real login
is *refused*. Both were replaced with something that reads the actual data: an
anti-join per foreign key, `WHERE (predicate) IS FALSE` per check, and a genuine
denied `SELECT` as the `analyst` role.

Two lessons, then, from the same build, and they compose:

> **Constraints are only constraints once executed** — and **a check whose
> reference is derived from the thing it checks is not a check, however often it
> runs.**

The first is about a check that never ran. The second is about a check that runs
constantly and verifies nothing. The second is the more dangerous, because it
produces evidence.

#### What actually changed, as opposed to what was learned

The lesson is worthless on its own. Written in this register, "constraints are
only constraints once executed" enforces exactly as much as the DDL did before it
was run — nothing. What makes A44 real is that the checks now execute:

- **`scripts/02_load_postgres.py` pre-flight.** Before any `COPY`, every table's
  frame is diffed against `information_schema.columns`, and any declared column
  that is absent from the frame, or present and entirely NULL, **blocks the
  load**. Columns with a server-side default (the three `SERIAL` surrogate keys)
  are exempt.
- **`KNOWN_EMPTY` is a registry, not an escape hatch.** A genuinely known gap has
  to be named there, and every load then prints it. An exception that is
  invisible is indistinguishable from a bug. It is currently empty.
- **`PARTIAL_BY_DESIGN` (added with A45).** Stricter than the all-NULL check: a
  partially-populated column must be non-null in exactly the rows its flag
  column claims, and null in exactly the rest. The realistic failure — a sample
  silently collapsing to a handful of rows — passes an all-NULL check trivially
  and fails this one.
- **`scripts/04_verify_database.py` re-scans, it does not ask.** All 104 CHECK
  predicates are re-evaluated row by row and every FK is re-verified by
  anti-join. `ALTER TABLE … VALIDATE CONSTRAINT` was the obvious implementation
  and is worthless: a constraint created normally is already marked valid, so
  VALIDATE returns success without reading a row. That is the same
  intent-versus-enforcement trap as inspecting `pg_catalog` for LK-05, and the
  same trap as this whole decision.
- **`reports/database_checks.json` is hash-gated.** A stale results file reports
  SKIP rather than a fabricated PASS.

#### Defect 5's `NOT NULL` was removed, and that is a finding too

3,284 of 55,000 customers (6.0%) open no in-window session, so their mean
`p_cod_intent` has no denominator. Spec §3.13 declares the column's *type* only;
the `NOT NULL` was added when the DDL was written and was simply wrong.

Decision **A18** applies unchanged: a statistic with an empty denominator is
NULL, never imputed. Imputing 0.62 would invent 3,284 fictitious COD-average
customers *inside the truth table* — the one place in the project where a
fabricated value cannot be caught downstream, because it is the thing everything
else is checked against. This is the same pattern as **A30**
(`pit_avg_order_value`), and both are recorded in `docs/limitations.md`
(**L11**, **L2**).

The general form: **a constraint can be wrong.** Executing the schema does not
only find defects in the data — it finds defects in the schema. Five of the six
were the generator failing to meet the DDL. One was the DDL asserting something
about the world that is not true.

#### Scope

**Nothing here touched a slope, a level, or a business assumption.** Defect 2 is
a representation decision; the rest are columns that were specified and never
filled. The parts of the system that were *exercised* were correct. The parts
that were merely *described* were not — which is the whole finding, stated as
narrowly as it can be.

### A45 — `logit_*_components`: populate a documented sample · **RULED · APPLIED**

Defect 6 of A44, ruled separately because it was a design choice rather than a
bug: the two JSONB trace columns were declared in the schema and were entirely
NULL.

**Ruling: populate a documented 2,000-session stratified sample.** Not the full
table, and not a drop from the DDL.

#### Why the columns exist at all

They make **GT-01 auditable**. When Phase 5 regresses the generated data and
compares recovered coefficients against planted ones, "the regression matched"
is a materially weaker claim than "here is one order, and here is every additive
term that produced its probability." The second is checkable by hand by a
reviewer who does not trust the regression.

That is a *lookup*, one order at a time. It is never a scan. Populating all
155,000 sessions costs roughly **190 MB of JSONB** for a query nobody runs in
bulk — which is why full population was rejected.

#### The sample

| Stratum | Rows | Why anyone would open it |
|---|---:|---|
| `random_sessions` | 500 | Unbiased draw across all sessions, orders or not, so the sample is not only its own tail |
| `cod_rto` | 500 | A COD order that came back — the central case of the study |
| `prepaid_rto` | 500 | A prepaid order that came back — the comparison case |
| `high_risk_delivered` | 500 | Top-decile `p_rto_precheckout` that arrived safely — where the score was "wrong", and the honest face of the 0.77 ceiling |

Strata overlap is permitted and de-duplicated, so the realised count is **1,995**
of a 2,000 target — recorded in `_truth.json`, not assumed. 1,836 of those carry
an RTO trace; the remainder are sessions that produced no order and therefore
have no RTO logit to decompose, which is the same rule `tp_rto_pair` already
applies to `p_rto_precheckout`.

The rule lives in `params.yaml` under `truth_sampling:` and draws from its own
seed substream, **appended to the end** of the list per CLAUDE.md invariant 11.

#### `components_populated`

A new `BOOLEAN NOT NULL` column. Without it, "no components" and "not sampled"
are indistinguishable, and an ambiguous NULL is precisely the defect class A44
was written about. Two CHECK constraints make the flag a fact about the data
rather than a label that can drift from it:

```sql
components_populated = (logit_cod_components IS NOT NULL)
(logit_rto_components IS NOT NULL)
    = (components_populated AND p_rto_precheckout IS NOT NULL)
```

It is deliberately given **no DEFAULT**: the loader's pre-flight exempts
defaulted columns from its "declared but absent from the frame" check, so a
default would quietly excuse the generator from ever emitting it.

#### The reconstruction problem, and why the trace can be trusted

Three of the four strata are defined by *outcomes*, so the sample cannot be drawn
until the day loop has finished — and the trace therefore has to be rebuilt
afterwards rather than recorded as it runs.

That is the risk. A second implementation of the logit is free to drift from the
first, and **a drifted trace is worse than an empty column, because a reader
would trust it.** Two things prevent it:

1. **One implementation.** `cod_dynamic`, `stage1_dynamic` and
   `post_dispatch_shock` were refactored to build a dict of *named terms*; the
   day loop adds `sum_terms(...)` of that dict. The trace reads the same
   functions. There is no parallel copy to drift.
2. **The check executes.** `build_component_traces` re-derives every sampled
   probability from its own trace and raises unless it matches what the day loop
   stored, to 1e-9. Realised worst error: **8.88e-16** — machine epsilon.

Splitting a summed expression into named terms is only safe if it moves no bits,
because the day loop's arithmetic is what every calibrated intercept was solved
against. Accumulating into a zero array reproduces the inline expression's
left-to-right order exactly (`0.0 + x` is exact in IEEE-754). This is asserted in
`tests/test_components.py` against the original expressions written out by hand,
and **demonstrated end to end**: the `fct_order` content hash is byte-identical
across the refactor *and* the substream append.

```
before  49b066f06ed1fae63fb5ef65c88cbf5c1199f86c9267936d869b590357cd2aee
after   49b066f06ed1fae63fb5ef65c88cbf5c1199f86c9267936d869b590357cd2aee
```

That single comparison is also the first live proof of the append-only substream
rule: adding `truth_sampling` to the end of the list left every existing stream
byte-identical, exactly as `SeedSequence.spawn` promises.

#### What a trace looks like

Stage-1 terms are bare; the four post-dispatch shock terms are prefixed
`shock.`, so a reader can always tell which half was knowable at checkout. Each
trace carries its own totals, so the decomposition can be verified by adding the
named terms up without re-running anything.

```
__intercept__                     -4.687500      shock.courier_reliability_z_neg   +0.359330
latent_intent                     +0.872060      shock.attempt_delay_days          +0.826062
address_completeness              -1.075200      shock.seller_dispatch_late        +0.250000
pit_rto_rate_shrunk               +0.259778      shock.nu                          +4.600444
is_cod                            +1.600000      __total_precheckout__             -0.872270
...  (23 stage-1 terms)                          __total_final__                   +5.163567
```

`is_cod = +1.600000` is the planted invariant, sitting as one additive term among
23 — visible, and one line of SQL away:

```sql
SELECT DISTINCT (logit_rto_components->>'is_cod')::numeric, count(*)
FROM truth.truth_order_probability
WHERE logit_rto_components IS NOT NULL GROUP BY 1;
--   0.0 |  638      (prepaid)
--   1.6 | 1198      (COD)
```

#### Loader consequence

The two `KNOWN_EMPTY` registrations are gone; that registry is now **empty**.
They are replaced by `PARTIAL_BY_DESIGN`, which is a stricter check, not a softer
one: the column must be non-null in exactly the rows the flag claims and null in
exactly the rest. The realistic failure — the sample silently collapsing to a
handful of rows — passes an all-NULL check trivially and fails this one.

Limitation **L12** is restated accordingly: no longer "declared but empty", but
"populated for a documented 2,000-session audit sample; full population rejected
at 190 MB for a diagnostic that is never run in bulk."

#### A45 exposed a hole in the staleness guard, and it is an A44 hole

`reports/database_checks.json` carries the hash of the dataset it ran against, so
that a stale file reports SKIP rather than a fabricated PASS. That guard hashed
**only the data**.

A45 is precisely the change it could not see. The whole point of the refactor was
that `fct_order` came out byte-identical — while the schema gained a column and
two CHECK constraints. A data-only guard would therefore have accepted a results
file asserting "102 check predicates re-evaluated, 0 violations" as current
evidence about a schema that now has 104. **LK-01 is the sharper case**: it is a
claim about the columns of a *view*, and a view lives entirely in the DDL, so the
data hash says nothing whatsoever about whether the result is still true.

Two things can go stale independently. Both are now hashed: `ddl_sha256` covers
every `sql/*.sql` file, by name and by content, in name order.

Verified by execution rather than by assertion — the file's `ddl_sha256` was
poisoned while leaving the data hash correct:

```
[SKIP] LK-01   View columns subset of safe whitelist        not runnable
[SKIP] LK-05   analyst role has zero privileges on truth    not runnable
[SKIP] DQ-01   Reproducibility hash matches manifest        not runnable
65 tests | 56 pass | 0 HARD fail | 0 SOFT fail | 9 skip
```

The three degrade to SKIP, and the verdict text names them as "NOT passes". Five
tests in `tests/test_validation_gates.py` pin the hash's sensitivity, including
the realistic A45 shape: a new `.sql` file added alongside untouched ones.

This is the same finding as A44, one level up. A44 was "the schema was never
executed against data". This is "the guard on the schema's own evidence was never
executed against a schema change". Both were correct-looking, both were inert,
and both only became visible when something actually ran.

**No slope, level or business assumption moved.**

### A46 — `attempt_delay_days` was published only on failures · **RULED · OPTION (a)**

Found in Phase 3 while attempting H6. `fct_delivery_event.attempt_delay_days`
was populated on **15,084 of 15,084 returned orders and 0 of 76,166 delivered
ones**.

#### The distinction that decides the remedy

This is **not** a data-generating-process defect. Three things were checked
before ruling:

| Question | Evidence | Verdict |
|---|---|---|
| Was δ₂ = 0.22 multiplying an outcome-conditional variable? | `delivery_timeline()` runs on **all `ord_pos`**; the shock is added for every order; `draw_rto(...)` is on the *next line* | **No.** The causal order is delay → shock → probability → draw |
| Did the delay actually exist for delivered orders during generation? | The A45 component trace: **749 of 749** sampled delivered orders carry a non-zero `shock.attempt_delay_days`, mean implied delay **2.058 days** (returned: 3.072) | **Yes.** The 3.07-vs-2.06 gap is the planted effect as a difference in means |
| Is the shock term leakage-shaped? | Absent from `leakage_guard.safe_feature_whitelist` and from `vw_risk_model_input`; LK-01 passes | **No.** The firewall is intact |

**The generated data is sound; the exported view of it was incomplete.** The
defect is confined to the projection layer.

#### Root cause

The column was hung on an **event type that exists only for failures**. Under
decision A8 it is an *attempt-grain* fact — days between the promised date and
the first delivery attempt — and every shipped order has a first attempt. But
`build_delivery_events` emitted an attempt event only when the attempt failed; a
delivered order's successful first attempt was emitted as `DELIVERED` with
`with_delay=False`.

The rename from `delivery_delay_days` to `attempt_delay_days` existed
**specifically** to make the column outcome-independent. The rename happened.
The emission never followed it, and nothing checked the correspondence.

#### Ruling: option (a)

`with_delay=True` on the `DELIVERED` emit. Rejected alternatives:

* **(b) a new `DELIVERY_ATTEMPT_SUCCEEDED` event** — changes the delivery-event
  vocabulary and attempt-count semantics for no gain.
* **(c) move the column to `fct_order`** — A8 put it on `fct_delivery_event`
  deliberately because it is attempt-grain; order-grain would recreate the
  `delivery_delay_days` confusion the rename existed to prevent.

**`attempt_number = 1` is emitted with it.** This goes marginally beyond the
literal ruling and is load-bearing: the DDL documents the access pattern as
*"read from the `attempt_number = 1` row"*, so populating the delay without the
attempt number would leave that query outcome-conditional one layer down — the
same defect, moved rather than fixed. A delivered order's successful first
attempt **is** attempt 1. Nothing derives attempt *counts* from this table
(`fct_order.delivery_attempts` is generated independently), so no count moves.

#### Conditions, all discharged

See the closing report and `reports/data_validation_report.md`. In summary:
`fct_order` byte-identical; **DQ-15** added as a HARD test; the
`attempt_delay_days ≤ delivery_delay_days` reconciliation verified; full suite
re-run with CAL-11, the AUC ceiling and every EC figure unchanged.

#### DQ-15 — the check that was missing

```
attempt_delay_days IS NOT NULL for every shipped, non-cancelled order,
asserted separately across BOTH rto_flag arms, counts reported per arm.
```

The assertion is deliberately **not** "is this column ever populated" — that
passed throughout the defect's life. It is "**is its population independent of
the outcome**". Both arms are asserted separately so a regression that empties
one cannot hide behind the other being full.

**No slope, level or business assumption moved.**

### Remaining

| Item | Status |
|---|---|
| BR-09, GT-01/03/04/06/07 | Phase 5, need fitted models |
| `docs/data_generating_process.md` | still absent |

Modules 22-23 (PostgreSQL load, report render) are built. LK-01, LK-05 and
DQ-01 are verified against a live server; DQ-01 compares two independent
generation runs, not a manifest against the run that wrote it.

---

### A47 — §8.4 geography audit failed on M1 · **RULED 2026-08-26 · OPTION 3 + 3 CONDITIONS**

Raised in `reports/phase4_m1.md` §6. Full standalone record — finding, options,
ruling, measured cost — in **`docs/phase4_escalation.md`**. Summarised here so
the register stays the index of every decision.

#### What was escalated

M1 (test AUC 0.7530) restricted **52.6% of Tier-3 orders and 0.02% of Metro
orders** at the 17% volume §8.3 expects the High tier to occupy. §8.4's limit is
2.5x. The breach held at every volume tested.

The modeller escalated rather than deciding, which is what §8.4 requires: *"a
risk model in a consumer product is a policy, not just a classifier. I wrote the
fairness constraints before I wrote the model, because after you have the AUC
it's very hard to argue yourself out of using it."*

#### The measurement that made it an escalation rather than a fix

| model | features | test AUC | Tier-3 ÷ Metro |
|---|---|---|---|
| full model | 48 | 0.7530 | 2677x |
| no `geo_tier` dummies | 45 | 0.7515 | 427x |
| no geographic features at all | 41 | 0.6934 | 3.2x |

**Removing the protected attribute cost 0.0015 AUC and fixed nothing** —
`serviceability_score`, `courier_reliability_score`, `cod_cultural_index` and
`estimated_delivery_days` reconstruct the tier. A fully geography-blind M1 still
breaches §8.4 **and** falls below §9.4's 0.72 gate. There is no version of this
model that satisfies both constraints, so no feature-selection decision could
have resolved it.

#### Ruling: option 3 — restrict WITHIN geo tier

Rank orders against their own tier's distribution. The objection that this flags
genuinely low-risk Metro orders was **accepted as a real cost**, not rebutted:
the policy is deliberately less margin-optimal than the score permits, because a
policy restricting half of Tier-3 and none of Metro is not defensible to a
customer, a regulator or a journalist. That trade is the product decision, and
§8.4 pre-committed to it before the AUC existed.

Rejected alternatives, with reasons, are in `phase4_escalation.md` §5. Option 5
— re-anchoring the 2.5x limit against the measured 5.4x RTO spread — was left on
the record as available but not taken, because taking it *having just seen the
AUC* is the exact move §8.4 exists to prevent.

#### Conditions, all discharged

| # | Condition | Where | Result |
|---|---|---|---|
| 1 | Per-tier thresholds for RESTRICTIVE interventions only; offers use the global score | `src/risk/policy.py:INTERVENTIONS`, `phase4_m2.md` §6.1 | B / D / COD gating are per-tier; A / C / E / F global |
| 2 | Report the margin cost at equal restriction volume | `phase4_m2.md` §6.3 | **₹172,634** on a 22,520-order window = **₹3.92 Cr/yr**, 2.4% of the ~₹165 Cr headline |
| 3 | Report Tier-3's ABSOLUTE restriction rate, not just the ratio | `phase4_m2.md` §6.4 | falls from **42.3% to 15.0%** — and 96.8% of newly-restricted Metro orders score below p\*, which is stated as the cost rather than buried |

#### FA-01 — the eighth validation family

A ruling that is not tested is a ruling that regresses. **FA-01** (HARD,
`src/validation/tests_fa.py`) asserts the restrictive-intervention rate ratio,
**worst tier over best tier**, stays ≤ 2.5x at every volume in
[0.05, 0.10, 0.17, 0.25]. Measured post-overlay, which is what a customer
experiences. Worst-over-best rather than Tier-3-over-Metro so a policy
concentrating on some other tier cannot pass a check watching the wrong pair.

Current result: **PASS, worst 1.44x**. The same model under global thresholds
fails at every volume.

The residual 1.35x is not the score. Per-tier selection equalises exactly at
17.00% per tier; the §8.4 **clean-record cap** then vetoes 33.9% of Metro
selections against 16.8% of Tier-3, because Tier-3 runs 5.4x Metro's RTO rate and
therefore holds far fewer clean records. **The overlay has its own geographic
gradient**, and it favours Metro — worth knowing before designing another
carve-out of that shape.

#### Also applied under this ruling

* **M2 fitted** (`scripts/07_fit_m2.py`): test AUC **0.7684**, below the 0.7717
  ceiling it is now entitled to approach, calibration slope 0.9466.
* **Three-rule §9.3 baseline** (0.6806) reconstructed from view columns rather
  than by widening the firewall — `pit_risk_tier_rule_based` escalated one tier
  for COD, verified against the planted `order_risk_tier_rule_based` at
  **100.00%** agreement on all 22,520 test rows.
* **GBM challenger does not ship**: −0.34pp against §9.3's required +3.00pp. Its
  *train* AUC of 0.7880 sits above the achievable ceiling — capacity, not signal,
  which is what the margin exists to refuse to pay for.
* **Duplicated exclusion rationale removed** — the five `pit_*` count culls share
  one reason, now stated once above the table (`features.COUNT_CULL_NOTE`).

#### Count correction

The validation suite has **68 tests in eight families**, not the 62 CLAUDE.md
was quoting. The stale figure predated the VOL-02a/02b split (A31), EC-01b,
EC-08 and DQ-15/DQ-16 (A46) — five real tests that were never added to the
headline. CLAUDE.md now says the count is measured from the suite rather than
maintained by hand, because this is the second time it has drifted.

**No slope, level or business assumption moved.** `params.yaml` is unchanged.

---

### A48 — Intervention E is RESTRICTIVE, not an offer · **RULED 2026-08-26**

A47's condition 1 split the intervention library into sticks (per-tier) and
carrots (global). **E — Smart payment recommendation** was classified as an offer
on the reasoning that reordering payment methods removes nothing.

**That reasoning was wrong.** E does not only emphasise some options — it
**de-emphasises others**. For a payment method chosen by 62% of orders largely
out of habit, salience *is* the option. A lever §10.1 expects to move prepaid
share by 3-6pp through position alone is exercising the same power as a fee while
requiring none of a fee's disclosure.

**E ranks per-tier**, alongside B (COD fee), D (partial payment) and G (COD
gating).

#### The one-tap constraint — carried into the Phase 5 PRD as non-negotiable

> COD must remain reachable in **ONE TAP** in every variant of E. Reordering and
> de-emphasis are permitted; an extra tap, a hidden menu, a collapsed accordion
> or a confirmation interstitial is not.

§10.1 already draws this line — *"emphasis is acceptable; hiding or burying COD
is not"* — and E is the only lever that can cross it without a word of copy
changing. An arm that violates it is not a variant of E; it is intervention G
wearing E's name and must be governed as G.

The constraint lives in `src/risk/interventions.py:ONE_TAP_CONSTRAINT` as well as
in the PRD. A constraint that lives only in a document is a constraint that gets
lost at the next handover.

---

### A49 — GT-01, GT-03 and GT-04 on first execution · **RULED 2026-08-26 · 2 RESTATED, 1 ACCEPTED**

The six tests that had been SKIP since Phase 2B — GT-01/03/04/06/07 and BR-09 —
became runnable once Phase 4 produced fitted models. **The suite now executes 68
tests with zero skips, the first time in the project every HARD test has run.**

GT-06, GT-07 and BR-09 passed on first execution. GT-01, GT-03 and GT-04 failed.
This ruling restates two of them and accepts the third. **Nothing was tuned and
nothing was waived.**

#### The root cause, named once, covering GT-01 and GT-04

Decision **A37** raised `post_dispatch_shock.noise_sd` from **0.85 to 3.3125** to
bring the achievable AUC ceiling into GT-05's band; **A38** froze it. The
generator draws `logit(p) = Xβ + ε` with `ε ~ N(0, 3.3125²)`. A model fitted on
`X` alone cannot see `ε` and converges on an attenuated `β / sqrt(1 + 3σ²/π²)` —
**0.480** at σ = 3.3125, against **0.906** at σ = 0.85.

**GT-01's 80%-inside-CI clause and GT-04's contains-zero clause were both written
against σ = 0.85 and never re-anchored when A37 moved it.** Limitation **L8**
already records that the spec's *prose* figures (13.4pp / 19.9pp / 33%) belong to
that superseded era. What L8 missed is that **two validation thresholds belong to
it too** — they are σ = 0.85 prose with a `_r(...)` around them. Nothing noticed,
because neither test had ever executed. **This is a σ = 0.85 threshold measuring
a σ = 3.3125 dataset.**

Cross-references: **A37** (the recalibration), **A38** (the freeze), **L8** (the
prose consequence), **L14** (the coefficient-magnitude consequence).

---

#### GT-04 — RESTATED. The threshold was wrong.

The brief called `review_count` a "planted null", but `params.yaml` plants
**−0.05, not 0**. At n = 91,250 a −0.05 logit coefficient is detectable, so a CI
excluding zero is the **correct** result. A test asserting otherwise asserts that
the estimator should fail to find something that is there.

Restated to what the clause was always for — confirming the estimator does not
**inflate** a negligible effect:

```
PASS if the 95% CI contains the planted -0.05
AND  the p10 -> p90 marginal effect is < 2.0pp
```

**Result: STILL FAILS, on clause 1.** Reported rather than absorbed, as the
ruling required.

| Clause | Measured | Verdict |
|---|---|---|
| CI contains −0.05 | [−0.03555, −0.00830] | **FAIL** |
| \|p10→p90\| < 2.0pp | 1.02pp | PASS |

**The direction matters and is the finding.** The CI sits *above* the planted
value: the estimate is **smaller** in magnitude than what was planted, not
larger. That is the same attenuation L14 measures, confirmed independently —
planted −0.05, fitted −0.0219, **ratio 0.439** against the predicted 0.480.

GT-04 exists to catch an estimator *inflating* a negligible effect into a
finding. It does the opposite. The clause that actually tests inflation passes.

**The general consequence:** under σ = 3.3125, **no test comparing a fitted
coefficient's CI to an un-attenuated planted value can pass at this sample
size.** The CI half-width is 0.0136; the attenuation gap is 0.0281, about twice
as wide. GT-01 and GT-04 are one failure measured two ways.

---

#### GT-03 — RESTATED. The threshold was stale.

The old `remaining >= 0.35` floor was written against a naive/AME gap that A37's
recalibration moved. Restated on the relative rule already ruled for GT-02:

```
PASS if AME < adjusted < naive
AND  the adjusted estimate closes between 20% and 65% of the naive-to-AME distance
```

**The 65% ceiling is unchanged.** The point of GT-03 is that adjustment must not
fully recover a partly-unobservable truth, and that constraint stands.

**Result: STILL FAILS, on closure.** AME 9.99pp, naive 17.73pp, selection
component 7.74pp. Estimand reported per the ruling; ATT is the comparable one.

| Estimate | Estimand | Adjusted | Closes | Ordered | Verdict |
|---|---|---|---|---|---|
| Propensity matched **(PRIMARY)** | ATT | 12.24pp | **70.9%** | YES | **FAIL** |
| Logistic, 41 confounders | ATT | 12.56pp | 66.9% | YES | FAIL |
| Logistic, 41 confounders | ATE | 10.67pp | 91.2% | YES | FAIL |
| Stratified, tenure × geo | ATT | 14.59pp | 40.6% | YES | PASS |

**Ordering holds on all four** — nothing leaked, nothing is inverted. Closure
breaches the 65% ceiling on every estimator except the stratified one.

**And that pattern is the finding, not an accident of specification.** The
stratified estimator is the only one that controls for **no customer behavioural
history** — 16 tenure × geo cells and nothing else. The more customer history you
control for, the more of the supposedly unobservable confounder you recover.
Decision **A11** generates pre-window history *from the latents*, so
`pit_cod_share` is a direct observable consequence of the unobservable rather
than merely correlated with it; it alone moves recovery from 73.8% to 90.0%.

Noted for the record: the **ATE→ATT** switch the ruling asked for is worth
**1.9pp** on the logistic estimate (10.67 → 12.56pp) and moves it from 91.2% to
66.9% closure — much closer to the ceiling, but still over it.

**Not restated a second time**, per the ruling's own instruction. The two obvious
fixes — grade on the stratified estimate, or drop `pit_cod_share` from the
confounder set — are both specification-shopping to hit a validation target,
forbidden by CLAUDE.md rule 3 and decision A7, and both would be done *after
seeing the result they failed*. Phase 3 §C.3 already refused exactly this.

---

#### GT-01 — ACCEPTED as limitation **L14**.

Zero sign flips across 13 Strong/Moderate relationships is the substantive
requirement and it passes. GT-01 is now graded on that clause; the magnitude
clause becomes recorded evidence in `docs/limitations.md` **L14**.

**Attenuation factor across the 13, as the ruling required:**

| Statistic | Value |
|---|---|
| Mean recovered ÷ planted | **1.011** |
| Median | 0.741 |
| SD | 0.587 |
| **Coefficient of variation** | **0.581** |
| Range | 0.200 – 2.018 |

**The ruling asked whether the ratio is roughly uniform. It is not — and the
mean of 1.011 is the most misleading number in this entry.** It reads as "no
attenuation at all". What it averages is two opposing effects:

* **Seven terms attenuated** toward zero as predicted (0.20 – 0.74).
* **Six terms inflated** above 1.0 (1.19 – 2.02) — precisely those that **proxy
  the omitted latents and `shock.*` terms**: `seller_sla_breach_rate`,
  `paid_via_switch`, the `geo_tier` contrasts, `pit_rto_rate_shrunk`.
  Omitted-variable bias pushes these up while noise attenuation pushes everything
  else down, and the two cancel in the mean.

**So the "uniform attenuation preserves ranking" inference does not apply**, and
the ranking is measured directly instead rather than assumed:

* Spearman ρ between |planted| and |fitted| across the 13: **0.823**.
* The ranking the risk model actually depends on is of *orders*, not
  coefficients, and it is measured by AUC: **0.7530** (M1) and **0.7684** (M2)
  against a **0.7717** ceiling — within 0.4pp of the best achievable.

**The conclusion the ruling reached holds. The stated reason for it does not**,
and L14 says so rather than repeating a convenient argument.

---

#### Outcome

| Test | Action | Result |
|---|---|---|
| **GT-01** | accepted as L14, graded on signs | **PASS** |
| **GT-03** | restated to closure ∈ [20%, 65%] | **FAIL** |
| **GT-04** | restated to CI ∋ −0.05 AND effect < 2.0pp | **FAIL** |

**68 tests, 66 pass, 2 HARD fail, 0 skip. Verdict 🔴 NOT READY.**

> ⚠️ **This outcome block is A49's, and it is SUPERSEDED. Do not quote it as
> current.** Ruling **A50** restated GT-04 a second time — A49's clause asked the
> fitted CI to contain the un-attenuated −0.05, which is unsatisfiable under
> A37's noise — and GT-04 now **passes**. GT-03 was **not** restated and still
> fails. Current state: **68 tests, 67 pass, 1 HARD fail, 0 skip, 🔴 NOT READY.**

**`phase4-complete` is NOT tagged.** The tag asserts a state; with two HARD tests
failing that assertion is false, and CLAUDE.md invariant 12 forbids declaring
success with a HARD failure outstanding. Both remaining failures are now
diagnosed to the coefficient, both are consequences of A37 and A11 — decisions
taken deliberately and approved — and neither has been tuned.

**No slope, level or business assumption moved.** `params.yaml` is unchanged.

---

### Methodology notes — two process catches worth recording

Neither changed a number. Both are recorded because the *class* of error recurs.

#### N1 — `git stash` used as a diagnostic inside a live working tree

While checking whether a `UnicodeEncodeError` in `scripts/03_validate.py`
predated the session's changes, `git stash` was run to compare against `HEAD`.
The command was chained behind a long-running validation run, timed out
mid-chain, and left the tracked edits stashed. Recovered with `git stash pop`
with nothing lost, and the encoding bug turned out to be pre-existing and was
fixed properly.

**The lesson is not "be careful with stash".** It is that a *diagnostic* should
never mutate the working tree: the question "did this bug exist before my
changes?" is answerable with `git show HEAD:path | python -`, `git diff`, or
reading the traceback, none of which move a file. A destructive command chained
behind a long-running one is also a command whose failure mode is invisible until
the timeout.

#### N2 — a cross-report figure asserted rather than measured

A draft of `phase4_escalation.md` §2 stated that M1's global rule "reads 52.6%
pre-overlay and 42.3% post-overlay". The 42.3% had been read off **M2's**
exposure table and attributed to M1. It was caught before publication, measured
directly, and M1's true post-overlay figure is **42.33%** — so the sentence
happened to be right, by coincidence, to one decimal place.

**A number that is right by coincidence is still an unmeasured number.** The
recurring failure it belongs to is comparing two tables that were computed under
different conventions — here, pre-overlay versus post-overlay — and assuming the
figures are interchangeable because they describe "the same thing". They did not:
M1 §6.1 audits the *score's* concentration before the §8.4 protections run; FA-01
and the M2 tables audit what a *customer experiences* after them. Both reports now
state which convention they use and quote the other.

---

### A50 — GT-04 restated a second time; GT-03 measured and left open · **RULED 2026-08-26 · 1 RESTATED (OVERRIDE), 1 OPEN**

A49 restated GT-03 and GT-04 and instructed that neither be restated again. Both
then failed. This ruling **overrides that instruction for GT-04 only**, and
**upholds it for GT-03**. The two go opposite ways and the reasons are different.

**Nothing was tuned. `params.yaml` is unchanged. No slope, level or business
assumption moved. No feature was dropped from any production model.**

---

#### A50.1 — GT-04: clause 1 was unsatisfiable, so it is dropped

**The override, and why it is granted.** CLAUDE.md's standing position — and
A49's explicit instruction — is that a test is not restated twice, because the
second restatement is where "re-anchor until it passes" begins. The override is
granted because **A49's clause was itself the error**, and the error was proved
by measurement rather than argued around:

> A49 wrote a **coverage** test where an **inflation** test was needed.

The generalisation that proved it is not specific to GT-04. Under A37's
`post_dispatch_noise_sd = 3.3125`, the estimator does not converge on the planted
beta, it converges on beta x 0.480. For this term the **CI half-width is 0.0136**
and the **attenuation gap is 0.0281** — the interval is about half as wide as the
distance it was asked to span. **No test comparing a fitted CI to an
un-attenuated planted value can pass at this sample size**, at any n this dataset
could plausibly have. A49's clause asked the estimator to recover a magnitude
A37's noise makes unrecoverable.

That is a different situation from a test that merely fails. A failing test is
evidence about the data; an unsatisfiable test is evidence about the test.

**The restated clauses.**

```
PASS if the fitted coefficient's sign matches the planted sign
AND    |p10 -> p90| marginal effect < 2.0pp
AND    the fitted magnitude does NOT exceed the planted magnitude
```

| Clause | Measured | Verdict |
|---|---|---|
| Sign matches planted −0.05 | fitted **−0.02193**, negative | PASS |
| \|p10→p90\| marginal effect < 2.0pp | **1.02pp** | PASS |
| \|fitted\| ≤ \|planted\| | 0.02193 ≤ 0.05, **ratio 0.439** | PASS |

**GT-04 now PASSES.**

**Clause 3 is the anti-inflation guard and it is one-sided by design.** GT-04
exists to catch an estimator turning a negligible planted effect into a finding.
Attenuation *below* the plant is the expected direction under A37 and is
harmless — it understates a null that was already negligible. A magnitude *above*
the plant is the over-fitting-dressed-as-a-finding, and that is the only side the
clause guards. The test is not vacuous: a sign flip, a p10–p90 effect at or above
2.0pp, or any |fitted| above 0.05 still fails it, and none of those is
attenuation.

**A side benefit worth recording:** the measured 0.439 sits close to L14's
predicted 0.480, so GT-04 now confirms GT-01's attenuation mechanism on a single
term independently, instead of failing for it.

Recorded in `src/validation/tests_gt.py:_gt_04` — with the full three-stage
history (original / A49 / A50) in the docstring, because a clause that has been
rewritten twice is exactly the clause a future reader will suspect of having been
tuned, and the record is the answer to that suspicion.

---

#### A50.2 — GT-03: NOT restated. The failure stands.

**Upheld.** The 65% ceiling is not superseded sigma = 0.85 prose. It is the
load-bearing constraint of the project: the adjustment must **not** fully recover
the truth, because `latent_intent`, `latent_trust` and `latent_liquidity` are
unobservable by construction. Closing **70.9%** of the naive-to-AME distance is a
**finding**, and a finding is not waived.

Three measurements were ordered before any further ruling. All three are in
`reports/gt03_diagnostics.md` (`make gt03`, `src/analysis/gt03_diagnostics.py`).
**Nothing was fixed.**

**(1) Refit without `pit_rto_rate_shrunk`** — the suspected latent proxy,
planted +2.80.

| Estimator | Full set | Minus the suspect |
|---|---|---|
| Propensity matched (PRIMARY, ATT) | 12.24pp, closes **70.9%** | 12.76pp, closes **64.3%** |
| Logistic, ATT | 12.56pp, closes 66.9% | 12.66pp, closes 65.5% |
| Logistic, ATE | 10.67pp, closes 91.2% | 10.77pp, closes 90.0% |

Worth **6.6pp of closure on the primary, 1.3pp on the logistic**. The asymmetry
is informative — the feature does most of its work in the *propensity* model,
changing who is matched to whom, not in the outcome model — but it accounts for
less than a seventh of the 70.9%. **It is not the explanation.** The refit is a
diagnostic; the feature stays in every model.

**(2) Latent reconstructibility.** R-squared of each latent on the safe feature
set, order-level and customer-level, on both GT-03's 41 confounders and a
deliberately wider 58-column set of everything analyst-visible and pre-outcome.

| Target | Kind | Order R² | Customer R² |
|---|---|---|---|
| `latent_intent` | latent | 0.151 | 0.161 |
| `latent_trust` | latent | 0.220 | 0.225 |
| `latent_liquidity` | latent | 0.288 | **0.295** |
| `true_cod_propensity` | **choice channel** | 0.819 | **0.853** |

**No latent exceeds the ~0.35 bar; the highest is 0.295.** The "unobservable by
construction" claim **holds as written and needs no qualifying** in any document.
`latent_liquidity` at 0.29 is not nothing and is stated rather than rounded away.

**The fourth row is the answer to the ruling's question.**
`true_cod_propensity` is not a latent — it is the composite the three latents
drive, the **choice channel** — and it is **85% reconstructible**. The
propensity model's own AUC of **0.835** says the same from the other side.

> The adjustment is not recovering the latents' **values**. It is recovering
> **treatment assignment**, which the latents fully determine and which the
> observable COD history then records.

**(3) Contribution by deviance**, alongside the closure each block is worth.

| Confounder | Deviance | Share of explained | Closure lost |
|---|---|---|---|
| `pit_cod_share` | 99.1 | 1.5% | **+8.06pp** |
| `pit_has_history` | 60.5 | 0.9% | **+5.05pp** |
| `has_saved_prepaid_instrument` | 36.5 | 0.6% | +2.77pp |
| `pit_rto_rate_shrunk` | 123.6 | 1.9% | +1.34pp |
| `geo_tier` | 92.0 | 1.4% | +1.16pp |
| `courier_reliability_score` | **698.4** | **10.6%** | **−0.36pp** |
| `seller_sla_breach_rate` | 193.9 | 2.9% | −0.06pp |

**The two orderings disagree, and the disagreement is the finding.** The ruling
asked for deviance; deviance alone would have answered the words and missed the
point, so closure is reported beside it. `courier_reliability_score` dominates
deviance at **10.6% of everything the model explains** and closes **nothing** —
it explains RTO without explaining COD choice. The three biggest gap-closers are
all **COD-choice history**, not RTO-risk features, and together they are under 3%
of explained deviance.

**Verdict on the ruling's two branches.** It is branch one — **a real weakness in
the confounding structure**, not a latent leaking. The mechanism is **A11**:
pre-window history is generated *from* the latents, so `pit_cod_share` and
`pit_has_history` are direct observable consequences of the treatment-assignment
mechanism rather than correlates of it. Blocking the choice channel means
severing A11, which changes the DGP and the Phase 3 analysis with it.

**GT-03 remains OPEN and FAILING.** Not restated, not waived, not tuned.

---

#### A50.3 — L14: attenuation is heterogeneous, so ranking is not guaranteed

L14 now states plainly that the CV of 0.581 makes the attenuation
**heterogeneous**, and that a heterogeneous rescaling **can reorder terms
arbitrarily** — so nothing about A37's noise *guarantees* that the recovered
ranking matches the planted one. It is an empirical question, and it was
measured:

* **Spearman rho between |planted| and |fitted| across the 13: 0.823.**

**That is high, so the ranking survives** — as a measured fact about this
dataset, not as a property of the mechanism. It could have come out low. The
residual disagreement is concentrated in two terms on the inflated-proxy side:
`seller_sla_breach_rate` (planted 1.20, fitted 2.42) overtakes `is_cod` (planted
1.60, fitted 1.08), and `seller_rating_centered` (ratio 0.20) sinks below terms
planted smaller than it.

The ranking the risk model actually depends on is of **orders**, not
coefficients: **AUC 0.7530 (M1) / 0.7684 (M2)** against a **0.7717** ceiling.
**The risk model is fine.**

L14 also records that rho must be **re-measured** if `post_dispatch_noise_sd`
ever moves — it is not implied by the CV and cannot be carried forward.

---

#### Outcome

| Test | Action | Result |
|---|---|---|
| **GT-01** | unchanged; L14 sharpened on ranking (A50.3) | **PASS** |
| **GT-03** | measured, **not restated** | **FAIL — open** |
| **GT-04** | restated (override): sign + effect size + no inflation | **PASS** |

**68 tests, 67 pass, 1 HARD fail, 0 SOFT fail, 0 skip. Verdict 🔴 NOT READY.**

**`phase4-complete` is NOT tagged.** GT-03 stays open, and CLAUDE.md invariant 12
forbids declaring success with a HARD failure outstanding.

> ⚠️ **This outcome block is A50's, and it is SUPERSEDED. Do not quote it as
> current.** Ruling **A51** accepted GT-03 as a DGP limitation on the mechanism
> these diagnostics established, raised the closure ceiling 65% → 75%, and
> recorded limitation **L15**. Current state: **68 tests, 68 pass, 0 HARD fail,
> 0 skip, 🟢 DATASET READY** — and `phase4-complete` **is** tagged.

---

#### Methodology note — N3: an unsatisfiable test is not a failing test

Two restatements of one clause is the shape of a tuned test, and the only thing
that distinguishes this from tuning is that the second restatement was justified
by a **measurement that generalises beyond the test being restated**: the CI
half-width against the attenuation gap is a property of A37's noise and the
sample size, not of `review_count`. It predicts, correctly, that GT-01 fails the
same way — and GT-01 was restated before GT-04 was, on independent evidence.

The rule that survives: **restating a test after seeing it fail requires showing
the clause could not have passed under any admissible outcome.** "It failed and
here is a clause it would pass" is tuning. "It failed, and here is the arithmetic
showing no correct estimator could pass it" is a defect in the test. The
difference is checkable, and it is what the register has to record.

---

### A51 — GT-03 accepted as a DGP limitation; ceiling restated to 75% · **RULED 2026-08-26 · ACCEPTED**

A50 refused to restate GT-03 and ordered three measurements instead. The
measurements came back and they settle it. **The 70.9% closure is the DGP being
more tractable than the threshold's author predicted — not the analysis
leaking.**

**The mechanism, named.** Decision **A11** generates pre-window history from the
**same latent slopes** that drive current COD choice. That makes `pit_cod_share`
close to a **sufficient statistic for the propensity score**. Hence the pattern
the diagnostics found and which nothing else explains:

| Target | Kind | Customer-level R² |
|---|---|---|
| `latent_intent` | latent | 0.161 |
| `latent_trust` | latent | 0.225 |
| `latent_liquidity` | latent | **0.295** |
| `true_cod_propensity` | **choice channel** | **0.853** |

**No latent exceeds 0.29; the choice channel reaches 0.85.** Adjustment recovers
**treatment assignment** well and **latent values** poorly — and in a
propensity-matching framework, good assignment recovery is most of what an
estimator needs. That is a coherent finding, not a defect. Nothing crossed the
firewall: `analyst` remains denied on schema `truth` with SQLSTATE 42501.

---

#### A51.1 — The ceiling moves to 75%, ON THE MECHANISM

```
PASS if  AME < adjusted < naive
AND      the adjustment closes 20%-75% of the naive-to-AME distance
```

**The distinction that makes this admissible, and the one to check if this entry
is ever doubted:** the ceiling was restated **on the stated mechanism, not on the
measurement**. It was set at 65% from intuition about a dataset whose propensity
channel was less recoverable than A11 actually made it. The diagnostics named
*why* the intuition was wrong before the number moved.

**The constraint still binds**, which is the test of whether the restatement is
real or cosmetic:

| Estimate | Estimand | Closes | Irreducible | Verdict at 75% |
|---|---|---|---|---|
| Propensity matched **(PRIMARY)** | ATT | **70.9%** | 29.1% | PASS |
| Logistic, 41 confounders | ATT | 66.9% | 33.1% | PASS |
| Logistic, 41 confounders | ATE | 91.2% | 8.8% | **still FAIL** |
| Stratified, tenure × geo | ATT | 40.6% | 59.4% | PASS |

An estimate landing **on** the AME still fails. The logistic ATE — the
specification that recovers most — **still fails at 91.2%**, so the ceiling has
not been widened until everything passes. The ordering clause is untouched.

**What was NOT done.** `pit_cod_share` and `pit_has_history` stayed in the
confounder set. The stratified estimate, which controls for no behavioural
history and passed at 40.6% under the old ceiling, was **not** promoted to
primary. The DGP was not regenerated. No slope, level or business assumption
moved; `params.yaml` is unchanged.

Recorded in `src/validation/tests_gt.GT03_CLOSED_BAND`, with the mechanism in the
comment above it — because a threshold that moved after a failure is exactly the
threshold a future reader will suspect of tuning, and the answer to that
suspicion has to sit next to the number.

**One drift hazard closed on the way.** `src/analysis/h1_decomposition.gt_03`
carried its own copy of the pass rule, still hard-coded at the original ">= 0.35
remaining". It was unused and had tracked neither A49 nor A51. It no longer
grades anything; it reports the quantities and the band lives in exactly one
place. A second encoding of a graded threshold is a threshold that drifts
silently — the same failure the build narrative §2 records about checks that
validate against their own output.

---

#### A51.2 — L15, and the claim change

**L15 is the substantive output of this ruling**, more than the threshold move.

> The confounding is **weaker than designed**. `pit_cod_share` inherits the COD
> latent slopes (A11), making treatment assignment **~85% reconstructible** from
> safe features even though no latent exceeds **R² 0.29**. A real marketplace
> would likely have **less** recoverable assignment and therefore **more**
> residual confounding, so **29% irreducible is an optimistic floor, not a
> realistic estimate**.

**The claim changes everywhere, and this is not cosmetic.**

| | |
|---|---|
| **Old** | "adjustment gets closer but cannot reach the truth" |
| **New** | "adjustment closes ~71% of the naive-to-truth gap; the remaining ~29% is irreducible because purchase intent is unobservable — and on real data that residual would likely be larger, because our simulated treatment assignment is unusually recoverable" |

The old sentence is not wrong, it is **unfalsifiable and uninformative**: it
asserts a direction without a magnitude, and it invites the reader to assume the
residual is large. The new one commits to a number and then says which way the
number is biased. Updated in `reports/phase3_findings.md` §C,
`docs/build_narrative.md`, `docs/phase4_closeout.md`, `CLAUDE.md`'s
planted-causal-structure table, and the two module docstrings that state it as a
current claim (`src/analysis/h1_decomposition.py`,
`src/validation/tests_gt.py`).

The Phase 2A spec prose (`docs/01_phase2_data_architecture.md` §17,
`docs/02_implementation_brief.md`) is **left as written**. It is a historical
source document, and the standing convention since **L8** is that everything
downstream quotes `data/truth/_truth.json` and the limitations register, never
the spec prose.

---

#### A51.3 — The fifth pre-registered threshold to miss the as-built data

Added to the run in `docs/build_narrative.md`: **A7, A34, A37, A38, A49, A51.**

Five of the project's pre-registered numbers did not survive contact with the
data they were written for, and the pattern across them is worth more than any
one of them: **a threshold set before the mechanism is understood is a prediction
about the mechanism**, and predictions are wrong at the usual rate. The ones that
held were the ones expressed as *structure* — orderings, sign clauses, "must not
fully recover" — rather than as levels. A7 made exactly this trade on purpose, by
moving hardness from three rate levels onto CAL-11's selection **share**, and
CAL-11 has never needed restating.

---

#### Outcome

| Test | Action | Result |
|---|---|---|
| **GT-01** | graded on signs (A49); L14 sharpened on ranking (A50.3) | **PASS** |
| **GT-03** | accepted as a DGP limitation; ceiling 65% → 75% on the mechanism | **PASS** |
| **GT-04** | restated to sign + effect size + no inflation (A50.1) | **PASS** |

**68 tests, 68 pass, 0 HARD fail, 0 SOFT fail, 0 skip. Verdict 🟢 DATASET
READY.**

**`phase4-complete` is tagged.** Every HARD test passes, so CLAUDE.md invariant
12 is satisfied. The two remaining accepted limitations, **L14** and **L15**, are
documented with their mechanisms, their measured magnitudes and their named
originating decisions — which is what accepting a limitation is supposed to mean.

---

#### Methodology note — N4: a threshold restated on a mechanism, not on a measurement

A50's note **N3** set the bar for restating a *failing* test: show that no
correct estimator could have passed it. GT-03 does not meet N3 — a correct
estimator *could* have closed under 65%, on a DGP whose propensity channel was
less recoverable. So a second, weaker gate is needed for the case where the test
is satisfiable but the threshold encoded a wrong belief about the mechanism:

> **A threshold may be restated after a failure only if the mechanism that
> explains the failure was measured first, is stated in the restatement, and
> would have justified the new threshold BEFORE the result was seen — and only
> if the restated constraint still excludes the outcome it exists to exclude.**

All four clauses are checkable after the fact, which is the point. Here: the
mechanism is A11 and it was measured before the number moved (A50); it is named
in the code beside the constant; A11 predates the threshold, so knowing it would
have argued for a higher ceiling in advance; and full recovery still fails, as
the logistic ATE's continuing 91.2% failure demonstrates.

**What this does not license.** "The data turned out different from what I
expected" is not a mechanism. The difference between N4 and tuning is whether the
explanation was *measured and would generalise* — A11's history-from-latents
predicts high propensity recoverability for any dataset built this way, and it
predicted the 0.853 figure before it was quoted as a justification.
