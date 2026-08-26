# CLAUDE.md

Project guardrails. Auto-loaded every session. Keep this file short — it is the only context guaranteed to survive compaction.

---

## PROJECT

**E-commerce Checkout Optimization: Reducing RTO While Protecting Conversion and Contribution Margin.**

We are building a reproducible simulation of an Indian e-commerce marketplace (~100,000 orders) to support a Product Management case study on COD behaviour, RTO risk, checkout conversion, and contribution margin.

The objective is **not** "generate 100K fake rows." It is: plant a known ground truth — including truths that are *hard to recover* — so that later analysis can be checked against it. A dataset where the obvious analysis produces the obvious right answer is a failed dataset.

---

## SOURCE OF TRUTH

| File | Role |
|---|---|
| `docs/00_phase1_blueprint.md` | Business framing, unit economics, metrics, opportunity model |
| `docs/01_phase2_data_architecture.md` | **The implementation spec.** Primary reference |
| `docs/02_implementation_brief.md` | Build instructions, stage gates, validation suite |

Read the relevant section before implementing. Do not work from memory of a previous session.

**Current phase:** Phase 2B — synthetic data generation.
**Not in scope yet:** risk models, interventions, A/B tests, dashboards. Those are Phase 3+.

---

## INVARIANTS — never change without asking

### Scale
- **≥ 100,000 orders** (VOL-01, HARD) · 90-day window
- **Session count is an INPUT KNOB, not a target** (decision A31). Currently 155,000. It was
  147,059 = `target_orders / 0.68`, which made VOL-01 require conversion ≥ exactly 68.00% —
  the midpoint of CAL-06's own ±2pp band. Do not re-pin it to a conversion estimate.
- Population framing: 24,000,000 orders/year
- **Annualisation factor = 24,000,000 ÷ actual order count (≈230). DERIVED — never
  hard-coded.** The old ×240 silently encoded a 100,000-order sample, so changing the
  session count would have moved the ₹165 Cr headline for no business reason. Total cost ×
  (population ÷ sample) is invariant to sample size, which is exactly why it must be
  derived. EC-08 (HARD) asserts the derived factor lands in [200, 280].

### Calibration targets
| Metric | Target | Tolerance |
|---|---|---|
| COD share of orders | 62.0% | ±1.0pp |
| COD RTO rate (shipped denominator) | 24.0% | ±1.5pp |
| Prepaid RTO rate | 4.1% | ±0.8pp |
| Blended RTO rate | 16.5% | ±1.0pp |
| Checkout conversion (orders ÷ sessions) | 68.0% | ±2.0pp |
| **Mean** order value | ₹1,000 | ±₹25 |
| Median order value | ≈ ₹690 | ±₹60 |

**Mean, not median, is pinned at ₹1,000.** Order value is a right-skewed category-mixture lognormal.

### Economics (at a ₹1,000 order value)
| Line | Value |
|---|---|
| Prepaid delivered contribution margin | **+₹112** |
| COD delivered contribution margin | **+₹107** |
| COD RTO direct cash loss | **−₹309** |
| COD RTO total economic cost | **−₹416** |
| Break-even RTO probability p\* | **25.7%** |

### The planted causal structure

Only the first row is an input. Everything else is an **output** — measured after
generation, not asserted before it (decisions A6, A7). Treating an emergent
quantity as an invariant is how a slope gets nudged to hit it.

| Quantity | As built | Status |
|---|---|---|
| `is_cod` coefficient in the RTO logit | **+1.60** | **INVARIANT** — spec constant |
| True marginal effect of COD on RTO (AME) | **9.99pp** | **DERIVED** — measured post-hoc |
| Naive observed COD−prepaid gap | **17.73pp** | **EMERGENT** |
| Selection share of the naive gap | **0.4365** | **EMERGENT** — gated by CAL-11 at [0.25, 0.45] |
| Achievable risk-model AUC ceiling | **0.7717** | **DERIVED** — `noise_sd` calibrated to it (A37) |
| Share of the naive-to-truth gap a competent adjustment closes | **70.9%** (PSM, ATT) | **EMERGENT** — gated by GT-03 at [20%, 75%] |

⚠️ **The spec's prose figures — 13.4pp, 19.9pp, 33% — belong to `noise_sd = 0.85` and no
longer describe this dataset** (limitation L8, decision A37). The values above are measured.
**Everything downstream quotes `data/truth/_truth.json`, never the spec prose.**

The finding is unchanged in kind and sharper in degree: the naive estimate is **1.77× the
truth**, not "overstates by about a third".

⚠️ **State the recovery claim with its magnitude AND its direction of bias.** The old
phrasing — *"adjustment gets closer but cannot reach the truth"* — is retired: it asserts a
direction without a number and lets a reader assume the residual is whatever size suits
them. Say instead:

> **Adjustment closes ~71% of the naive-to-truth gap. The remaining ~29% is irreducible
> because purchase intent is unobservable — and on real data that residual would likely be
> LARGER, because our simulated treatment assignment is unusually recoverable.**

The second clause is not a hedge, it is a measured finding (**L15**, decision A51): no
latent is reconstructible from the safe feature set (max R² **0.295**), but
`true_cod_propensity` — the *choice* channel — comes back at R² **0.853**, because **A11**
generates pre-window history from the same latent slopes that drive current COD choice.
**~29% irreducible is an optimistic floor, not a realistic estimate.**

`rto_model.intercept_solved` (γ₀) is **not** a spec constant. It is whatever the
calibrator solves for CAL-05. It is never moved to make the AME land on 13.4pp.

**CAL-11 is the real gate.** If the selection share leaves [0.25, 0.45], the dataset
no longer supports the case study — whatever the rate levels do.

---

## HARD RULES

1. **Only INTERCEPTS may be calibrated** — currently five (`cod`, `rto`, `conversion`, `pre_window_cod`, `pre_window_rto`). Every slope coefficient in every model block is immutable. CAL-09 enforces this across all blocks. If a new model block is added, its intercept may be calibrated and its slopes may not.

2. **Never edit generated rows to pass validation.** Fix a parameter, regenerate, re-validate. No post-processing, no clamps, no fudge factors.

3. **If a target cannot be reached with the specified slopes, stop and report it.** That means the assumption set is internally inconsistent — a real finding, not a bug to hide.

4. **Latents are hidden.** `latent_trust`, `latent_liquidity`, `latent_intent`, `latent_price_sensitivity` live only in PostgreSQL schema `truth`. Never in any analyst-visible table, view, or export. `analyst` role has REVOKE ALL on `truth`.

5. **Risk-model AUC on safe features must be < 0.85.** If it isn't, something leaked. Test LK-03.

6. **The subtlest leakage trap:** `dim_customer.hist_orders_final`, `hist_rto_rate_final`, `hist_cod_share_final`, `clv_estimate` are end-of-window aggregates that include the current order. They look like innocent customer attributes. Keep them in the schema; firewall them out of `vw_risk_model_input`.

7. **Point-in-time state is chronological.** A prior order counts toward `pit_*` features only if its outcome had *resolved* before the session timestamp. Outcomes take 4–25 days.

8. **`is_shipped` is the RTO-rate denominator.** Pre-ship cancellations are removed before the RTO draw.

9. **Never write `if payment_method == 'COD': rto = True`.** Compute a probability, then draw. COD is one coefficient among ~25.

10. **No business literals in `src/`.** Every assumption lives in `config/params.yaml`.

11. **Randomness uses `SeedSequence` substreams**, never a global `np.random.seed()`. Changing `n_products` must not shift customer latents. New substreams append to the end of the list, never the middle.

12. **Never declare success with a HARD validation test failing.**

13. **Never restate a test because it failed.** Restating is permitted only on one of two showings, and the showing goes in the register beside the change. Anything else — including "the data turned out different from what I expected" — is tuning.

    **(a) The test was unsatisfiable** (register note **N3**, from A50). Show, by arithmetic that generalises beyond the test, that *no correct estimator could have passed it*. GT-04 qualified: A37's noise attenuates every coefficient by 0.480, so a CI of half-width 0.0136 can never span a gap of 0.0281 — a fact about the noise and the sample size, not about `review_count`.

    **(b) The threshold encoded a wrong belief about the mechanism** (register note **N4**, from A51). All four clauses must hold: the mechanism was **measured first**, it is **named in the restatement**, it **predates the result** and would have argued for the new number in advance, and the restated constraint **still excludes what the test exists to exclude**. GT-03 qualified: A11's history-from-latents was measured (R² 0.853 on the choice channel, ≤0.295 on every latent), it is named beside the constant, A11 predates the ceiling, and full recovery still fails — the logistic ATE remains outside the band at 91.2%.

    Every clause is checkable after the fact. That is the point: "here is a clause it would pass" and "here is why it could not have passed" look identical from outside unless the reasoning is written down.

---

## GENERATION ORDER (dependencies are forced — see spec §8)

```
config → dates → geography → sellers → products
  → customers + latents → customer history        [history FROM latents]
  → sessions → point-in-time state                [strict chronological]
  → COD intent → payment attempts → conversion    [failure causes abandonment]
  → orders → cancellations                        [before RTO: denominator]
  → pre-checkout RTO score                        [frozen before Stage-4 info]
  → delivery + post-dispatch shock → RTO draw → RTO reasons
  → economics                                     [costs are outcome-conditional]
  → rollup → truth file → PostgreSQL → validation
```

---

## STAGE GATES — stop and report at each

| Stage | Gate |
|---|---|
| 1 | Repo inspection + architecture summary + ambiguities + plan → **WAIT FOR APPROVAL** |
| 2 | params.yaml + schemas + seed harness |
| 3 | **5,000-order dev dataset** (`config/scenarios/dev_small.yaml`) |
| 4 | Validation on dev dataset → **SHOW THE REPORT** |
| 5 | Fix by adjusting parameters, not data. Loop 3–4 until green |
| 6 | Full 100K+ generation |
| 7 | Full validation |
| 8 | PostgreSQL load + verify REVOKE |
| 9 | Final report + 5-seed check + sensitivity scenarios + docs |

**Do not generate 100K rows until Stage 4 passes on the dev dataset.**

---

## VALIDATION

**68 tests**, eight families: **VOL** (5) · **CAL** (11) · **EC** (9) · **BR** (11) · **LK** (6) · **DQ** (18) · **GT** (7) · **FA** (1).

**This count is measured from the suite, not maintained by hand.** `python scripts/03_validate.py` prints the per-family totals; if this line disagrees with it, this line is the one that is wrong. The old "42" was an arithmetic error and the "62" that replaced it went stale the same way — it was still being quoted after **VOL-02** split into **02a/02b** (A31), after **EC-01b** and **EC-08** were added, and after **DQ-15/DQ-16** landed with A46, none of which were counted.

The count moved on the rulings: **+CAL-10** (reason-weight immutability, A4) · **+CAL-11** (selection share, A7) · **+LK-06** (declared shrinkage prior, A19) · **DQ-07 split into 07a / 07b / 07c** (A9) · **+DQ-15/DQ-16** (A46) · **+FA-01** (A47). CAL-03 and CAL-04 were downgraded HARD → SOFT (A7) but are still counted.

**FA — fairness (A47).** One test, HARD. `FA-01` asserts that the restrictive-intervention rate ratio, worst geo tier over best, stays ≤ **2.5x** at every restriction volume in [0.05, 0.10, 0.17, 0.25]. It exists because §8.4's geography audit failed on M1 and the escalation was ruled on — **restrictive interventions rank within geo tier; offers use the global score**. A ruling that is not tested is a ruling that regresses. FA-01 reads the fairness result published by `scripts/07_fit_m2.py`, hash-guarded against the dataset, and reports **SKIP rather than PASS** if that result was computed on a different one.

HARD failures block. SOFT failures require written sign-off in `docs/validation.md`.

Behavioural tests need **effect-size floors**, not just significance — at 100K rows everything is significant.

**Every test now runs, every test passes, zero skips.** GT-01/03/04/06/07 and BR-09 were SKIP from Phase 2B until Phase 4 produced the fitted models they needed. Three rulings closed them out: **A49** (restate GT-03 and GT-04, accept GT-01 as **L14**), **A50** (restate GT-04 a second time; refuse to restate GT-03 and order it measured), **A51** (accept GT-03 as a DGP limitation, ceiling 65% → 75%, record **L15**).

**Current verdict: 🟢 DATASET READY — 68 tests, 68 pass, 0 HARD, 0 SOFT, 0 skip.** `phase4-complete` is tagged. See `docs/phase4_closeout.md` §7.

**GT-03 was accepted, not waived and not tuned.** A50's diagnostics (`make gt03` → `reports/gt03_diagnostics.md`) found that **no latent is reconstructible** from safe features — highest R² **0.295** on `latent_liquidity`, under the 0.35 bar — so "unobservable by construction" holds as written. What *is* reconstructible is the **choice channel**: `true_cod_propensity` at **R² 0.853**, propensity-model AUC **0.835**. The top gap-closers are all COD-choice history (`pit_cod_share` +8.06pp of closure at 1.5% of deviance; `pit_has_history` +5.05pp) while `courier_reliability_score` carries 10.6% of deviance and closes nothing. **The adjustment recovers treatment assignment, not latent values.** Mechanism: **A11** generates pre-window history from the same latent slopes that drive COD choice. A51 raised the ceiling **on that mechanism, not on the measurement** — and the constraint still binds: the logistic ATE still fails at 91.2%, and an estimate landing on the AME still fails.

**Two accepted limitations, each with a measured mechanism and a named origin: L14** (magnitudes unrecoverable, signs and ranking survive; A37's noise) and **L15** (confounding weaker than designed, ~29% irreducible is an **optimistic floor**; A11's history-from-latents).

**Both restatements are logged with their justification** — A50's note **N3** (GT-04's clause was unsatisfiable) and A51's note **N4** (GT-03's threshold encoded a wrong belief about the mechanism). Both gates are now **hard rule 13**.

⚠️ **Any test comparing a fitted coefficient to a planted one must compare against `planted × expected_attenuation(σ)`, not the planted value itself** — or test signs, ordering, or one-sided inflation instead. A37 set σ = 3.3125, which attenuates every recovered coefficient by ≈ 0.480. GT-01 and GT-04 both failed on this and it was the same failure twice — see **L14**. GT-01 is now graded on signs (A49) and GT-04 on inflation (A50); both pass.

⚠️ **The attenuation is heterogeneous (CV 0.58), so ranking is NOT guaranteed preserved** — a non-uniform rescaling can reorder terms arbitrarily, and the usual "one common rescaling preserves order" argument does not apply. Terms that proxy the omitted latents are *inflated* instead of attenuated, so the mean ratio of 1.011 is misleading. **Ranking must be measured, never inferred.** As measured: Spearman ρ = **0.823** between |planted| and |fitted| across the 13 — high, so the ranking survives, and the order-level ranking the risk model depends on sits at AUC 0.7530/0.7684 against a 0.7717 ceiling. **Re-measure ρ if `post_dispatch_noise_sd` ever moves.**

**GT-03 is designed to fail to fully recover the truth.** Re-anchored relatively by ruling A6, because the old fixed [15, 19]pp band became invalid once γ₀ > −3.00 — the band would then contain the truth, so an estimate that fully recovered the unobservable would *pass*, inverting the test.

```
PASS if  AME < adjusted < naive_gap                         # A6
AND      closes 20%–65% of the naive-to-AME distance        # A49 restatement
```

The adjustment must move toward the truth without reaching it. If it lands *on* the AME, a hidden variable leaked. **The primary estimate is the propensity match, as an ATT** (A49 fixed the estimand; the ATE/ATT switch alone is worth 1.9pp). Measured: **12.24pp, closes 70.9% — PASS.**

⚠️ **The ceiling is 75%, raised from 65% by A51 — on the mechanism, not on the measurement.** 65% came from intuition about a dataset whose propensity channel was less recoverable than **A11** actually made it; A50 measured the mechanism *before* the number moved. **It has not been widened until everything passes:** the logistic ATE still fails at 91.2%, and an estimate landing on the AME still fails. Do not move it again without satisfying **hard rule 13(b)**.

⚠️ **Two different denominators get confused here.** The *shrink from naive* is `(naive − adjusted) / naive` = 30.9%. GT-03's clause uses the *selection component*: `(naive − adjusted) / (naive − AME)` = 70.9%. They are not comparable, and the second is what the test grades. (Both figures are the PSM ATT primary; the older 39.8% / 91.2% pair was the logistic **ATE**, which A49 retired as non-comparable.)

Verdict: 🟢 DATASET READY (all HARD pass, ≤2 SOFT) · 🟡 CONDITIONAL (3–5 SOFT, documented) · 🔴 NOT READY.

---

## STACK

Python 3.11+ · NumPy · pandas · PostgreSQL 14+ · SQLAlchemy + psycopg2 · PyYAML + jsonschema · statsmodels · scikit-learn (validation benchmarking only) · pytest · Parquet.

**Do not add** deep learning frameworks, Airflow/Prefect/dbt, or any dependency outside this list without asking.

Code must be readable by a PM who knows basic Python and SQL. Prefer an explicit boring function over a clever abstraction. No file over ~300 lines.

---

## COMMANDS

```bash
make dev         # generate 5,000-order development dataset
make generate    # generate full 100K+ dataset
make validate    # run all 68 validation tests → reports/data_validation_report.md
make load        # load PostgreSQL, create views, apply REVOKE
make test        # pytest — unit tests for generator code
make m1          # Phase 4: rules baseline + M1 (pre-selection)  → reports/phase4_m1.md
make m2          # Phase 4: M2 + GBM challenger + A47 policy     → reports/phase4_m2.md
make gt03        # A50: GT-03 diagnostics, measures only        → reports/gt03_diagnostics.md
make all         # generate → validate → load
```

`make m1` / `make m2` need `make load` to have run: both read
`analytics.vw_risk_model_input` through the restricted `analyst` role, which is
the only permitted source. `make m2` also publishes `reports/fairness_checks.json`,
which FA-01 consumes.

`tests/` unit-tests the **generator code**. `src/validation/` tests the **generated data**. Do not merge them.

---

## REPORTING

- **Chat summaries: max 250 words.** Full documents go to disk.
- **Tables in chat responses: plain markdown pipe tables only.** Never box-drawing characters — they truncate in transit.

---

## DO NOT

- Invent or change a business assumption without asking
- Use future information to build pre-checkout features
- Make the data perfectly predictable — the ~0.76 AUC ceiling is a requirement, not a defect
- Make all customers identical or all orders ₹1,000
- Build risk models, interventions, or A/B tests (Phase 3+)
- Add dependencies outside the approved stack

---

## WHEN UNSURE

Flag it and ask. An unflagged assumption is the worst failure mode in this project. If the spec is ambiguous, say so and propose options — do not resolve it silently and proceed.
