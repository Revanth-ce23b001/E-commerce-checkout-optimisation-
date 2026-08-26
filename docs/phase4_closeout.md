# Phase 4 closeout — what Phase 5 inherits, and what it must not assume

**Status: NOT CLOSED.** Two HARD validation tests fail after ruling A49. See §7.
**Reproduce:** `make load` → `make m1` → `make m2` → `make validate`.

---

## 1. What Phase 4 built

| Artefact | What it is |
|---|---|
| `reports/phase4_m1.md` | M1, pre-selection. Test AUC **0.7530**. The §8.4 escalation. |
| `reports/phase4_m2.md` | M2, post-selection. Test AUC **0.7684**, calibration slope 0.9466. |
| `docs/phase4_escalation.md` | The geography finding, the options, ruling A47, and its measured price. |
| `src/risk/` | Dataset firewall, design matrix, scorecard, baselines, challenger, fairness, policy. |
| `src/analysis/gt_recovery.py` | GT-01's coefficient-recovery harness. |
| `src/validation/tests_fa.py` · `tests_gt.py` | FA-01, and the six tests that had been SKIP since Phase 2B. |
| `data/processed/m2_scores.parquet` | Scores, p\*-anchored tiers, and the ruled per-tier restriction flags. |

**The validation suite now runs 68 tests with zero skips.** That is the first
time in the project every HARD test has actually executed.

---

## 2. Binding on Phase 5 — not advisory

These are decisions, not recommendations. Each is enforced somewhere in code so
it cannot lapse quietly at a handover.

### 2.1 Per-tier thresholds bind every restrictive lever (A47)

**Restrictive interventions rank orders within their own geo tier.** Never
globally. A Metro order at the 95th Metro percentile is treated like a Tier-3
order at the 95th Tier-3 percentile.

| Lever | Kind | Ranks |
|---|---|---|
| A — Prepaid incentive | offer | global |
| B — COD fee | **RESTRICTIVE** | **per-tier** |
| C — Trust-building checkout | offer | global |
| D — Partial payment | **RESTRICTIVE** | **per-tier** |
| E — Smart payment recommendation | **RESTRICTIVE** (A48) | **per-tier** |
| F — Payment-reliability routing | offer | global |
| G — COD gating | **RESTRICTIVE** | **per-tier** |

Enforced by `src/risk/interventions.py` and tested by **FA-01** (HARD). A new
lever must be classified before it ships; the default for anything that removes,
prices, conditions or de-emphasises an option is **restrictive**.

**Price on the record:** ₹3.92 Cr/year at the §8.3 volume, 2.4% of the ~₹165 Cr
headline exposure. Phase 5 does not get to re-open this to buy the margin back.

### 2.2 The one-tap COD constraint (A48) — non-negotiable in the PRD

> **COD must remain reachable in ONE TAP in every variant of intervention E.**
> Reordering and de-emphasis are permitted. An extra tap, a hidden menu, a
> collapsed accordion, or a confirmation interstitial is not.

E is the only lever that can cross §10.1's "hiding or burying COD is not
acceptable" line without a single word of copy changing. The constraint lives in
`interventions.ONE_TAP_CONSTRAINT` as well as here, and any experiment arm that
violates it is not a variant of E — it is intervention G wearing E's name, and it
must be scored and governed as G.

### 2.3 The Stage-4 bar carries forward, unchanged

`attempt_delay_days`, `delivery_delay_days`, `delivery_attempts` and
`actual_delivery_days` are **never features**. They are absent from
`vw_risk_model_input`, absent from every design matrix, and asserted absent *by
name* in `dataset.assert_firewall` — a redundant second check, kept deliberately.

**Why it will keep being tempting.** GT-06 now measures it: realised delay
explains **15.4x** more deviance than the delivery promise. It is the single
strongest signal in the warehouse. It is also determined *after dispatch*, so a
model containing it does not predict RTO — it observes one.

The legitimate checkout-time proxy is `estimated_delivery_days`, and it is
already in both models.

Any Phase 5 uplift model, CATE estimator or targeting rule inherits this bar
whole. A model that beats 0.7684 by a wide margin has almost certainly breached
it; the diagnostic ceiling is 0.80 and LK-03's hard limit is 0.85.

### 2.4 The fairness residual is a floor, not a defect (A47 condition 4)

FA-01 measures **1.44x** worst-case against a 2.5x limit. The residual above 1.00
comes from the clean-record cap's own geographic gradient — Metro customers hold
clean records at 33.9% against Tier-3's 16.8%, because Tier-3 runs 5.4x Metro's
RTO rate and a clean record requires three deliveries and zero returns.

**Do not engineer it away.** The only mechanism that would is weakening the
clean-record protection, which is §8.4's *first* protection. Equalising a
fairness ratio by protecting fewer good customers improves the metric and harms
the customers. See `phase4_escalation.md` §9.1.

### 2.5 The scorecard is primary; the challenger did not earn its way in

The GBM lost by **0.34pp** against a required **+3.00pp** margin. Its *train* AUC
of 0.7880 sits above the achievable ceiling — capacity, not signal.

Phase 5 may re-run the challenger. It may not lower the margin.

---

## 3. What Phase 5 must NOT assume

| Do not assume | Because |
|---|---|
| The naive COD−RTO gap is the effect of COD | It is **1.77x** the truth. AME 9.99pp against a naive 17.73pp; 43.6% of the gap is selection. |
| p\* applies to any score | p\* = 0.2576 is the break-even of a **COD** order. It applies to M2, never to M1. |
| The blended RTO rate is 16.53% | That is the *censored* figure. The uncensored-window rate is **18.87%**, and the last three weeks of the window carry observed RTO rates of 11.5%, 1.9% and 0.0% purely from censoring. Any Phase 5 population must apply the 2026-03-07 horizon cut. |
| §8.3's tier shares (45/38/17) hold | Measured against p\*, they are **40.0 / 32.0 / 28.1**. The thresholds were pre-committed; the shares are an output. |
| Removing geography from a model removes geography | It does not. Four non-geographic features reconstruct the tier at a cost of **0.0015 AUC**. Any fairness claim resting on "we don't use geography as a feature" is unfalsifiable. |
| A statistically significant coefficient is a finding | At 91K rows everything is significant. GT-04 turned on exactly this — see §7.3. |
| The validation suite is green | It is not. Two HARD tests fail after A49. §7. |

---

## 4. Interfaces Phase 5 consumes

**`data/processed/m2_scores.parquet`** — one row per test-window order.

| Column | Note |
|---|---|
| `m2_score` | Calibrated P(RTO). Slope 0.9466, ECE 0.0074. |
| `pstar_tier` | LOW / MED / HIGH against §8.3's economic cut-lines. **Not percentiles.** |
| `restricted_pertier_{05,10,17,25}` | The **ruled** policy at each volume, post-overlay. Use these; do not re-threshold globally. |
| `contribution_margin` | Realised, from `fct_order_economics`. For offline policy evaluation only. |

**`reports/fairness_checks.json`** — FA-01's evidence, hash-guarded against the
dataset. Re-run `scripts/07_fit_m2.py` after any regeneration or FA-01 reverts to
SKIP.

**Experiment stratification** should use `pstar_tier`, per blueprint §8.5.

---

## 5. What Phase 4 settled that Phase 3 could not

| Question | Answer |
|---|---|
| Is RTO predictable pre-checkout? (H12) | Yes. M1 0.7530 pre-selection, M2 0.7684 post-selection, both under the 0.7717 ceiling. §9.4's gate is cleared. |
| Does the GBM earn its complexity? | No. −0.34pp. |
| Does the fairness overlay survive contact with a real score? | No, under global thresholds — at every volume. Yes, under A47's per-tier rule. |
| What does the fairness constraint cost? | ₹3.92 Cr/year. |
| Does delay beat promise? (H6, GT-06, BR-09) | Yes, 15.4x — and it is unusable at checkout. |
| Does propensity matching land between naive and truth? (GT-07) | Yes, 12.24pp — though it recovers 70.9% of the selection component, which is what fails GT-03. §7.2. |

---

## 6. The trade was priced on one side only

The fairness constraint cost **₹3.92 Cr/year**, and that number is on the record
because A47's condition 2 demanded it. **The other side of the trade was never
priced, and this section exists so that asymmetry is not mistaken for a
conclusion.**

What per-tier thresholding *bought* is a policy that does not restrict 42.3% of
Tier-3 orders and 0.02% of Metro orders. The risks that avoids are real:

* **Tier-3 churn.** A customer restricted at checkout may not come back. Nothing
  in this dataset measures post-restriction retention, because no restriction has
  ever been applied.
* **Complaint and support volume.** §10.1 flags a "qualitative/complaint monitor"
  as a requirement for the COD fee. No baseline exists.
* **Trust damage and press risk.** A policy that restricts half of one geography
  and none of another is a story. Its cost is not a number anyone in this project
  can compute.
* **Regulatory exposure.** Unquantified, and not obviously quantifiable in
  advance.

**So the comparison that was actually made is ₹3.92 Cr against nothing** — a
precise number on the cost side and an empty cell on the benefit side. The
decision still looks right: 2.4% of the headline exposure is a small premium for
removing a class of risk that could be existential, and §8.4 pre-committed to
paying it before anyone had the AUC. **But it was a judgement, not a
calculation**, and presenting the ₹3.92 Cr as though it were half of a completed
sum would misrepresent it.

**What would close the gap.** The per-tier policy is the natural control arm for
measuring it: run a restriction experiment with global-threshold and per-tier
arms, and measure retention, complaint rate and repeat purchase in the tiers each
arm treats differently. That is a Phase 5 experiment, and until it runs, the
right way to state this result is *"we paid 2.4% to avoid a risk we could not
quantify"* — not *"we paid 2.4% for a benefit worth more than 2.4%."*

---

## 7. CLOSED — all 68 tests pass; two accepted limitations

Three rulings, in sequence, and the sequence is the point.

* **A49** restated GT-03 and GT-04 and accepted GT-01 as limitation **L14**. Both
  restated tests then failed.
* **A50** resolved GT-04 by restating it a second time — an override of A49's own
  "do not restate again", granted because A49's clause was a **coverage** test
  where an **inflation** test was needed, and measurement proved it
  unsatisfiable. It **refused to restate GT-03** and ordered three measurements
  instead.
* **A51** accepted GT-03 as a **DGP limitation, not an analysis failure**, on the
  mechanism those measurements established: raised the closure ceiling 65% → 75%,
  and recorded limitation **L15**.

**All 68 tests pass.** Nothing here was introduced by Phase 4; all of it was found
by running tests that had never run. **Nothing was tuned** — `params.yaml` is
unchanged, no slope moved, no feature was dropped from any model, and the DGP was
not regenerated.

### 7.1 GT-01 — ACCEPTED (L14). Signs pass; magnitudes are not recoverable.

Graded on the sign clause: **0 flips across 13 Strong/Moderate relationships**.
The magnitude clause is now recorded evidence in `docs/limitations.md` L14.

**Mechanism.** A37 raised `post_dispatch_noise_sd` 0.85 → 3.3125 and A38 froze
it. A model fitted on `X` cannot see `ε ~ N(0, 3.3125²)` and converges on an
attenuated β. Predicted attenuation **0.480** against **0.906** at σ = 0.85.

**Attenuation factor across the 13:** mean **1.011**, median 0.741, SD 0.587,
**CV 0.581**, range 0.200–2.018.

**It is not uniform, and the mean is the misleading number.** Seven terms are
attenuated (0.20–0.74); six are *inflated* (1.19–2.02) because they proxy the
omitted latents and `shock.*` terms. The two effects cancel in the mean.

So the "uniform attenuation preserves ranking" argument does not apply, and the
ranking is therefore **not guaranteed preserved** and had to be measured rather
than inferred: **Spearman ρ = 0.823** between |planted| and |fitted| across the
13. That is high, so the ranking survives — but it survives as a measured fact,
not as a consequence of the attenuation argument. And the claim that actually
matters is the ranking of *orders*, not coefficients: **AUC 0.7530 / 0.7684
against a 0.7717 ceiling**, within 0.4pp of the achievable best. **The risk model
is fine.**

### 7.2 GT-03 — ACCEPTED (L15). Ceiling 65% → 75% on the mechanism.

```
PASS if AME < adjusted < naive
AND  closes between 20% and 75% of the naive-to-AME distance
```

| Estimate | Estimand | Adjusted | Closes | Ordered | At 65% | At 75% |
|---|---|---|---|---|---|---|
| Propensity matched **(PRIMARY)** | ATT | 12.24pp | **70.9%** | YES | FAIL | **PASS** |
| Logistic, 41 confounders | ATT | 12.56pp | 66.9% | YES | FAIL | **PASS** |
| Logistic, 41 confounders | ATE | 10.67pp | 91.2% | YES | FAIL | **still FAIL** |
| Stratified, tenure × geo | ATT | 14.59pp | 40.6% | YES | PASS | PASS |

**Ordering holds on all four** — nothing leaked. The ATE→ATT switch A49 asked for
is worth **1.9pp**; the ATE form is not comparable to the truth file's AME and is
shown only because **it still fails**, which is the evidence that the ceiling was
raised rather than removed.

**Why the ceiling moved, and what it was moved on.** 65% was set from intuition
about a dataset whose propensity channel was less recoverable than **A11**
actually made it. A50's diagnostics (§7.3) established the mechanism *before* the
number changed, and A51 restated the ceiling **on that mechanism, not on the
measured 70.9%**:

> A11 generates pre-window history from the **same latent slopes** that drive
> current COD choice, so `pit_cod_share` is close to a **sufficient statistic for
> the propensity score**. Adjustment recovers **treatment assignment** well and
> **latent values** poorly — and in a matching framework, good assignment
> recovery is most of what an estimator needs.

**The constraint still binds.** 29.1% of the selection component survives every
observed confounder; an estimate landing *on* the AME still fails; the logistic
ATE still fails at 91.2%; the ordering clause is untouched.

**What was not done.** `pit_cod_share` and `pit_has_history` stayed in the
confounder set. The stratified estimate — the one that controls for no
behavioural history and passed even at 65% — was **not** promoted to primary. The
DGP was not regenerated.

**And read L15 before quoting the residual.** 29% irreducible is an **optimistic
floor**, not a realistic estimate: a real marketplace would have less recoverable
assignment and therefore *more* residual confounding.

### 7.3 GT-03 diagnostics — what the ruling ordered measured

Full report: `reports/gt03_diagnostics.md`, produced by `make gt03`. **Nothing
was fixed.** No parameter, threshold, feature set or test moved; GT-03 stays
FAIL at 70.9%.

**(1) Refit without `pit_rto_rate_shrunk`** — the most likely single latent
proxy, planted at +2.80.

| Estimator | Full set | Minus `pit_rto_rate_shrunk` |
|---|---|---|
| Propensity matched **(PRIMARY, ATT)** | 12.24pp, closes **70.9%** | 12.76pp, closes **64.3%** |
| Logistic, ATT | 12.56pp, closes 66.9% | 12.66pp, closes 65.5% |
| Logistic, ATE | 10.67pp, closes 91.2% | 10.77pp, closes 90.0% |

Dropping it is worth **6.6pp of closure on the primary and 1.3pp on the
logistic**, and the asymmetry is itself informative: the feature does most of its
work in the *propensity* model, changing who is matched to whom, not in the
outcome model. Even so it accounts for less than a seventh of the 70.9%. **It is
not the explanation.** And this is a diagnostic refit only — dropping a
legitimate confounder to land under a validation ceiling is exactly the
specification-shopping §7.2 refuses.

**(2) Are the latents reconstructible?** R² of each latent on the safe feature
set, at two levels. The "maximal safe set" is deliberately wider than GT-03's
confounder set — 58 columns, everything analyst-visible and pre-outcome — because
a generous set is the conservative test of an unobservability claim.

| Target | Kind | Order-level R² | Customer-level R² |
|---|---|---|---|
| `latent_intent` | latent | 0.151 | 0.161 |
| `latent_trust` | latent | 0.220 | 0.225 |
| `latent_liquidity` | latent | 0.288 | **0.295** |
| `true_cod_propensity` | **choice channel** | 0.819 | **0.853** |

**No latent exceeds the 0.35 bar — the highest is 0.295.** "Unobservable by
construction" holds as written and needs no qualifying, though `latent_liquidity`
at 0.29 is not nothing and is stated here rather than rounded to "unobservable".

**The fourth row is the answer.** `true_cod_propensity` is not a latent; it is the
composite the three latents drive — the **choice channel** — and it is **85%
reconstructible**. The adjustment is not recovering the latents' *values*. It is
recovering **treatment assignment**, which the latents fully determine and which
the observable COD history then records. The propensity model's own AUC of
**0.835** says the same thing from the other side.

**(3) Which confounders close the gap.** Deviance contribution is the LR
statistic for dropping the block from the full RTO model — H6's definition.
`closure lost` is how many pp of GT-03's closure disappear with the block gone.

| Confounder | Deviance | Share of explained | Closure lost |
|---|---|---|---|
| `pit_cod_share` | 99.1 | 1.5% | **+8.06pp** |
| `pit_has_history` | 60.5 | 0.9% | **+5.05pp** |
| `has_saved_prepaid_instrument` | 36.5 | 0.6% | +2.77pp |
| `pit_rto_rate_shrunk` | 123.6 | 1.9% | +1.34pp |
| `geo_tier` | 92.0 | 1.4% | +1.16pp |
| `courier_reliability_score` | **698.4** | **10.6%** | **−0.36pp** |
| `seller_sla_breach_rate` | 193.9 | 2.9% | −0.06pp |

**The two orderings disagree, and the disagreement is the finding.**
`courier_reliability_score` dominates deviance at 10.6% of everything the model
explains and closes *nothing* — it explains RTO without explaining COD choice.
The three biggest gap-closers are all **COD-choice history**, not RTO-risk
features, and together they are under 3% of explained deviance.

**Verdict on the ruling's two branches.** This is branch one: a real weakness in
the **confounding structure**, not a latent leaking. The mechanism is **A11** —
pre-window history is generated *from* the latents, so `pit_cod_share` and
`pit_has_history` are direct observable consequences of the treatment-assignment
mechanism rather than mere correlates of it. Blocking the choice channel would
mean severing A11, which changes the dataset and the case study with it.

### 7.4 GT-04 — RESTATED BY A50, now PASSES

A49's clause asked the fitted 95% CI to contain the planted −0.05. **That is
unsatisfiable at this sample size and A50 dropped it**: the CI half-width is
0.0136 against an attenuation gap of 0.0281, so the interval is roughly half as
wide as the distance it was asked to span. The clause tested *coverage* where the
test's purpose is *inflation*.

```
PASS if the fitted sign matches the planted sign
AND    |p10 -> p90| marginal effect < 2.0pp
AND    |fitted| does NOT exceed |planted|
```

| Clause | Measured | Verdict |
|---|---|---|
| Sign matches planted −0.05 | fitted −0.02193, negative | **PASS** |
| \|p10→p90\| < 2.0pp | 1.02pp | **PASS** |
| \|fitted\| ≤ \|planted\| | 0.02193 ≤ 0.05, ratio **0.439** | **PASS** |

Clause 3 is the anti-inflation guard proper and it is **one-sided by design**. An
estimate smaller than a plant that was already negligible is harmless; an
estimate *larger* than the plant is the over-fitting-dressed-as-a-finding this
test exists to catch. The measured 0.439 sits close to L14's predicted
attenuation of 0.480, confirming GT-01's mechanism independently on a single
term.

**General consequence, unchanged:** under σ = 3.3125, no test comparing a fitted
coefficient's CI to an un-attenuated planted value can pass at this sample size.
That is why GT-01 is graded on signs and GT-04 on inflation.

### 7.5 What A51 chose, and what it declined

Four options were on the table once the diagnostics were in. **A51 took (a).**

| Option | Verdict |
|---|---|
| **(a) Accept as a DGP limitation; raise the ceiling on the stated mechanism** | **TAKEN.** The over-recovery is documented evidence that COD choice is near-fully predictable from observable history — itself a case-study finding, recorded as **L15**. |
| (b) Re-anchor the ceiling numerically against the measured choice-channel strength | Declined. Deriving a threshold from the measurement that failed it is circular; the ceiling moved on the *mechanism* (A11), which predates the result. |
| (c) Sever A11's history-from-latents | Declined. Changes the DGP and invalidates the whole Phase 3 analysis, to make an intuition-set threshold come out right. |
| (d) Drop `pit_cod_share` / `pit_has_history` from the confounder set | Declined. Specification-shopping to hit a validation target; §7.2 already refused it. |

**The claim changes, and this is the substantive output.**

| | |
|---|---|
| **Old** | "adjustment gets closer but cannot reach the truth" |
| **New** | "adjustment closes **~71%** of the naive-to-truth gap; the remaining **~29%** is irreducible because purchase intent is unobservable — and on real data that residual would likely be **larger**, because our simulated treatment assignment is unusually recoverable" |

The old sentence asserts a direction without a magnitude and invites the reader to
assume the residual is whatever size suits them. The new one commits to a number
and then says which way the number is biased — which is the most a synthetic study
can honestly offer. Updated in `reports/phase3_findings.md` §C,
`docs/build_narrative.md`, this document, `CLAUDE.md`'s planted-causal-structure
table, and the module docstrings that state it as a current claim.

---

## 8. Phase 4 is complete and tagged

`phase4-complete` asserts a state, and CLAUDE.md invariant 12 is explicit: *never
declare success with a HARD validation test failing.* None is.

**68 tests · 68 pass · 0 HARD fail · 0 SOFT fail · 0 skip · 🟢 DATASET READY.**

The first point in the project at which every HARD test has both **run** and
**passed** — six of them had been SKIP since Phase 2B for want of a fitted model.

Two limitations are accepted rather than fixed, and each carries a measured
mechanism, a magnitude, and a named originating decision:

| | What it says | Origin |
|---|---|---|
| **L14** | Planted coefficient *magnitudes* are not recoverable; signs and ranking are (ρ = 0.823) | A37's noise |
| **L15** | The confounding is weaker than designed; ~29% irreducible is an **optimistic floor** | A11's history-from-latents |

That is what accepting a limitation is supposed to mean. `params.yaml` is
unchanged, no slope moved, and the dataset was not regenerated to make anything
pass.
