# Decision Register — Phase 2B

Tracks every point where the specification is ambiguous, underspecified, or internally
inconsistent, and what was decided. **Nothing here is resolved silently.** An unflagged
assumption is the worst failure mode in this project.

Status values: **APPROVED** · **PROVISIONAL** (approved, may be revisited) ·
**PENDING** (awaiting decision — blocks work) · **OPEN** (raised, not yet ruled on).

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

**Specification change required.**
- §11.1 formula amended to `score(r) = log(base_weight[r]) + Σ driver_weight[r][d] × driver_value[d]`
- `docs/data_generating_process.md` must explicitly document **the transformation and its
  interpretation**: that it is a correction to the *mathematical implementation* so the
  specified base weights actually represent the intended zero-driver probabilities — **not**
  a change to the intended distribution. *(Explicit condition of approval.)*

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

**Economics preserved unchanged.**

| Line | Value |
|---|---|
| Prepaid delivered CM | +₹112 |
| COD delivered CM | +₹107 |
| COD RTO cash loss | −₹309 |
| COD RTO total economic cost | −₹416 |
| Break-even RTO probability p\* | 25.7% |
| Annual GMV | ₹2,400 Cr |
| RTO exposure | ₹164.1 Cr ≈ ₹165 Cr |

**Specification change required.**
- EC-01 restated to test **mean GMV per order** = ₹1,000 ±₹25
- `params.yaml` key `mean_order_value` → **`mean_gmv_per_order`**
- §12.1 category means relabelled as **GMV**, not `order_value`
- Mean `order_value` ≈ ₹920 added as a reported, non-tested descriptive figure
- `docs/economics.md` states the distinction with a reconciliation table
- **Underlying economics not changed**

---

## Pending — these block Stage 3

### A2 — Conversion / abandonment model · PENDING

**Problem.** ~31 of every ~32 abandoning sessions per 100 have no specified cause. §10.1
covers only the payment-failure path (~1pp). CAL-06 is HARD at 68% ±2pp with nothing to
calibrate. §9.1's `_truth.json` references a third intercept, `conversion_model_alpha0: 0.784`,
whose model does not exist — and CLAUDE.md rule 1 permits only two calibrated intercepts.

**Direction given.** Option B (behavioural model with fixed slopes), **rule not yet amended**.

**Awaiting.** Approval of the coefficient set, and a ruling on the intercept question.

| Sub-decision | Options |
|---|---|
| Scope | Full (21 slopes) · **Minimal (7)** · None |
| Intercept | Route 1 (third calibrated intercept, amend rule) · Route 2 (normalisation to stated funnel rates) · Route 3 (no solve, CAL-06 → SOFT) |
| Slope #12 `z(latent_trust) → PAYMENT_PAGE` | Recommend **zero** — potential conflict with §6.2's H7 exclusion |

**Key finding.** A deterministic solve of *some* kind is unavoidable. Because
`mean(logistic(c + βx)) ≠ logistic(c)`, setting hurdle offsets to their nominal logits
yields ≈61.3% conversion — **CAL-06 misses by ≈4.7pp**. The only question is what the solved
number is called and what it is pinned to.

**Classification of the 21 proposed slopes: A = 0, B = 2, C = 19.** Nineteen are newly
invented assumptions with no anchor in either source document. This asymmetry is the main
argument for the Minimal variant.

---

### A4 — RTO reason driver weights · PENDING

**Problem.** Brief §6 requires `rto_reasons.driver_weights`. §13.2 does not contain it.
§11.1 gives only qualitative arrows. Without magnitudes, BR-08 (HARD) fails outright.

**Direction given.** Option B (draft for approval), then **frozen**.

**Proposed.** 22 coefficients + 2 hard gates across 8 reasons (2 deliberately flat).
Classification: **A = 16, B = 6, C = 2, D = 0.**

**Awaiting.** Approval of the matrix; ruling on the 2 C-class coefficients
(reason 3 `pit_is_new_customer` +0.20; reason 6 `z(realised_delay_days)` +0.25 —
**recommend zero**, neither is required by any test); confirmation of CAL-10.

**Governance recorded.** Once approved the weights are **frozen**. They will **not** be
tuned to make CAL-08 pass. A CAL-08 miss is reported as a finding in `docs/validation.md`.
If the addressable share falls below 50%, escalate rather than log.

---

### A6 — Which planted COD effect is canonical? · PENDING

**Problem.** §8.3 derives the headline 13.4pp effect at γ₀ = −3.15, but §8.2 and §9.1 state
the solved intercept as ≈ −3.25 / −3.247, which gives 12.4pp.

**Direction given.** Option D — report all three (at-baseline at solved γ₀, average marginal
effect, naive gap); canonical = at-baseline at solved γ₀; do not force 13.4pp.

**Awaiting.** Approval of the GT band changes.

**13.3 vs 13.4 — resolved arithmetically.** Verified in code:
`logistic(−3.15 + 1.60) − logistic(−3.15) = 0.1750863 − 0.0410913 = 0.1339950` →
**13.3995pp**, which *truncates* to 13.3 and *rounds* to 13.4. §0.2's 13.3 / 6.6 is a
truncation error; §8.3, §9.1, GT-02, GT-03 and GT-07 all round correctly.
**Recommend correcting §0.2 to 13.4 / 6.5. Source spec not yet changed.**

**γ₀ sensitivity — verified in code:**

| γ₀ | Prepaid | COD | Effect | GT-03 `[15,19]` still valid? |
|---:|---:|---:|---:|:---:|
| −3.40 | 3.230% | 14.185% | 10.956pp | ✅ |
| −3.30 | 3.557% | 15.447% | 11.889pp | ✅ |
| −3.25 | 3.733% | 16.111% | 12.378pp | ✅ |
| **−3.15** | 4.109% | 17.509% | **13.399pp** | ✅ |
| −3.05 | 4.522% | 19.000% | 14.478pp | ⚠️ marginal |
| **−3.00** | 4.743% | 19.782% | **15.039pp** | ❌ **BREAKS** |
| −2.90 | 5.215% | 21.417% | 16.201pp | ❌ |
| −2.80 | 5.732% | 23.148% | 17.415pp | ❌ |

**GT-03 becomes invalid once γ₀ > −3.00** — the band would contain the truth, so an estimate
that fully recovered the unobservable would *pass*, inverting the test's purpose. This is a
live risk: §8.3 assumes `logistic(γ₀) = observed prepaid rate`, but prepaid orders select on
low-risk characteristics, so γ₀ must sit *above* −3.15 for the observed rate to reach 4.1%.

**GT-02 does not break when γ₀ is solved** — correcting an over-reach in my earlier proposal.
Its band `[18.5, 21.5]` being narrower than CAL-03/CAL-04 tolerances permit is a separate
pre-existing issue, moved to A7.

---

## Open — raised, not yet ruled on

Load-bearing (want rulings before Stage 3):

| # | Issue |
|---|---|
| **A7** | Three HARD RTO targets (CAL-03/04/05), one knob (γ₀). The COD/prepaid split is emergent from the fixed +1.60 plus selection. Also now carries GT-02's band/tolerance mismatch |
| **A8** | `delivery_delay_days` is NULL on every RTO order by §3.10's definition, but BR-09 (HARD) and GT-06 need it to predict RTO. `realised_delay_days` is used by the shock but is not a column in any table |
| **A9** | DQ-07's reconciliation invariant is unsatisfiable — unresolved intermediate orders and DQ-14 censoring both break it |
| **A10** | Censoring semantics undefined. `is_censored` is required by DQ-14 but absent from §3.10. Whether censored orders carry an outcome changes the CAL-03/04/05 denominator |
| **A11** | Pre-window history must be "generated FROM the latents" (the source of the confounding), but no latent→history parametrisation exists |
| **A12** | `fct_checkout_event` (~700,000 rows) is a required table with no generator module |

Remaining: **A13** (bisection on realised draw vs expected share; ±0.004 infeasible at dev
scale) · **A14** (no γ₀ bracket; the stated `[−2,+2]` excludes −3.25) · **A15** (COD loop
specified over 08→13; module 08 has no β₀ dependence) · **A16** (switch-COD share: §7.3 says
3.5pp, §10.3 says 4.2%) · **A17** (test count is 57, not 42, in all three documents) ·
**A18** (division-by-zero / NULL imputation in the logits unspecified) · **A19** (shrinkage
prior source unspecified — risk of outcome-derived leakage) · **A20** (`pit_orders_resolved`
used in a formula but not a column) · **A21** (`pit_risk_tier_rule_based` needs
`payment_method`, which is Stage 3) · **A22** (`ndr_code` has no enumeration) ·
**A23** (§6.3 vs §12.3 contradict on cancelled orders; `cogs_value` undefined; working-capital
figure not reproducible from its own formula) · **A24** (13 distributional/structural gaps)

---

## Build status

**Stage 2 (foundation) — partially complete.** Stage 3 blocked.

| Component | Status |
|---|---|
| Dependency verification on Python 3.14 | ✅ All 11 packages import cleanly |
| Repo hygiene, `docs/` canonical paths, `.gitignore` | ✅ |
| Project skeleton, `pyproject.toml`, `Makefile` | ✅ |
| Seed substream harness + independence checkpoint | ✅ 17 tests |
| Config loader (schema-validate, SHA-256, DGP-hash guard) | ✅ |
| Logit assembler + coefficient ledger | ✅ 24 tests |
| CAL-09 (slope immutability) | ✅ Implemented early, per brief §3.5 |
| CAL-10 (reason-weight immutability) | ✅ Mechanism written; inert until A4 approved |
| Shrinkage helper | ✅ 15 tests |
| **`config/params.yaml` values** | ⛔ Blocked on A2, A4, A11, A18–A24 |
| **SQL DDL** | ⛔ Blocked on A8, A10 |
| **Generators, calibration, any data** | ⛔ Blocked |

**56 unit tests, all passing. No dataset generated.**
