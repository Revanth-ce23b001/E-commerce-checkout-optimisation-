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
| True marginal effect of COD on RTO (AME) | **10.05pp** | **DERIVED** — measured post-hoc |
| Naive observed COD−prepaid gap | **17.65pp** | **EMERGENT** |
| Selection share of the naive gap | **0.430** | **EMERGENT** — gated by CAL-11 at [0.25, 0.45] |
| Achievable risk-model AUC ceiling | **0.7702** | **DERIVED** — `noise_sd` calibrated to it (A37) |

⚠️ **The spec's prose figures — 13.4pp, 19.9pp, 33% — belong to `noise_sd = 0.85` and no
longer describe this dataset** (limitation L8, decision A37). The values above are measured.
**Everything downstream quotes `data/truth/_truth.json`, never the spec prose.**

The finding is unchanged in kind and sharper in degree: the naive estimate is **1.76× the
truth**, not "overstates by about a third".

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

**62 tests**, seven families: **VOL** (4) · **CAL** (11) · **EC** (7) · **BR** (11) · **LK** (6) · **DQ** (16) · **GT** (7).

The old "42" was an arithmetic error — the family counts never summed to it. The count then moved on the rulings: **+CAL-10** (reason-weight immutability, A4) · **+CAL-11** (selection share, A7) · **+LK-06** (declared shrinkage prior, A19) · **DQ-07 split into 07a / 07b / 07c** (A9). CAL-03 and CAL-04 were downgraded HARD → SOFT (A7) but are still counted.

HARD failures block. SOFT failures require written sign-off in `docs/validation.md`.

Behavioural tests need **effect-size floors**, not just significance — at 100K rows everything is significant.

**GT-03 is designed to fail to fully recover the truth.** Re-anchored relatively by ruling A6, because the old fixed [15, 19]pp band became invalid once γ₀ > −3.00 — the band would then contain the truth, so an estimate that fully recovered the unobservable would *pass*, inverting the test.

```
PASS if  AME < adjusted < naive_gap
AND      (adjusted − AME) / (naive_gap − AME) >= 0.35
```

The adjustment must move toward the truth without reaching it. If it lands *on* the AME, a hidden variable leaked.

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
make validate    # run all 62 validation tests → reports/data_validation_report.md
make load        # load PostgreSQL, create views, apply REVOKE
make test        # pytest — unit tests for generator code
make all         # generate → validate → load
```

`tests/` unit-tests the **generator code**. `src/validation/` tests the **generated data**. Do not merge them.

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
