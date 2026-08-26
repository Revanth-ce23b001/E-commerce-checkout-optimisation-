# Phase 5 Stage 1 — intervention simulation and the decision table

**Reproduce:** `make load` -> `make m2` -> `python scripts/09_interventions.py`

**Population:** the M2 test window, 2026-02-15 to 2026-03-06 — 22,520 shipped, resolved, scored orders against 34,281 checkout-start sessions.

**Every behavioural number is [A]** and lives in `config/interventions.yaml`. **Every rupee is [D]**, derived per order from `config/params.yaml`'s cost registry plus that order's own realised cost draws. The two never mix, and the file boundary is what keeps them from mixing.

**Scope.** The six §10.1 levers plus COD gating, the risk-based pricing decision table, and the sensitivity sweep. The five-scenario CM comparison is Stage 2; the A/B design and power analysis are Phase 6; the PRD is Phase 7.

## 1. The population

| stage | count | note |
|---|---|---|
| checkout sessions started | 34,281 | CM/session denominator |
| orders placed | 23,465 | 68.45% checkout conversion |
| pre-ship cancelled | 945 | zero on every economic line (A23) - carried, not simulated |
| censored inside the window | 0 | zero by construction: the horizon cut lands on the first censored day |
| shipped, resolved, scored | 22,520 | the simulation population |

The window is clean by construction: the censoring horizon cut lands on the first day carrying any censored order, so every order inside it has a resolved outcome. Phase 4 closeout §3 warns that the blended 16.53% RTO rate is the *censored* figure; this population's realised rate is **18.68%**, and that is the number every delta below is measured against.

The 945 pre-ship cancellations are carried in the session denominator and excluded from the margin arithmetic. Decision A23 zeroes every economic line on a cancelled order, so including them would add 945 zeros to a mean and change nothing except the mean.

## 2. The two derivations, each checked before use

### 2.1 [D] Order economics — rebuilt, then reconciled

`fct_order_economics.contribution_margin` is the margin of the order that *happened*. Every question in this phase asks about an order that did not: the same basket paid for online, or carrying a fee, or delivered when in fact it returned. So the margin is rebuilt line by line, reusing each order's realised draws for every cost that does not depend on the payment method — forward freight, packaging, goods value — and taking the outcome-conditional lines at their registry expectations.

| population | orders | realised_cm | rebuilt_cm | delta |
|---|---|---|---|---|
| all orders | 22,520 | 33.53 | 33.52 | -0 |
| COD delivered | 10,317 | 108.63 | 108.63 | 0 |
| COD returned | 3,662 | -318.02 | -318.04 | -0.02 |
| prepaid delivered | 7,996 | 121.38 | 121.34 | -0.04 |
| prepaid returned | 545 | -315.03 | -314.5 | 0.53 |

Agreement is ₹-0.00 per order across 22,520 orders. The largest cell error is ₹0.53 on *prepaid returned*, and it is expected: that cell replaces a realised reverse-freight and shrink draw with its mean.

The effective PG rate is **1.347%**, measured from the realised prepaid mix rather than taken from the registry's headline 1.8%. UPI runs at 0.9% and cards at 2.1%, and a counterfactual switch has no rail yet — so both arms are priced at the blended rate the platform actually pays, which stops an artificial gap opening between the baseline and the switch.

Three deliberate departures from the generator, each stated because each changes a number:

1. **A fee does not increase COGS.** The generator folds `cod_fee` into net revenue and computes COGS as 75% of it, so a ₹39 convenience fee would silently add about ₹29 of procurement cost. It buys no goods. COGS is pinned to the realised `cogs_value` and is invariant to every lever. Without this, intervention B is understated by roughly three quarters.
2. **Outcome-conditional lines are used in expectation.** A counterfactual needs reverse freight on an order that did not return.
3. **Support/NDR is the registry mean of ₹18.00**, not `base + slope × attempts`. `delivery_attempts` is behind the Stage-4 bar, and a counterfactual order has no attempt count to use anyway.

### 2.2 [D] The causal counterfactual — the planted coefficients, per order

The DGP's RTO logit carries exactly three terms that switch on the payment method: `is_cod` +1.60, `month_end_x_cod` +0.30, `paid_via_switch` −0.45. A switch to prepaid removes all three from that order's own logit, keeping its latents, its geography and its realised post-dispatch shock. That is a per-order counterfactual, not a population average applied to individuals.

The identity is checked on the truth file's own population before it is used anywhere: **9.9923pp rebuilt against 9.9923pp in `_truth.json`, over 56,216 COD orders.**

| quantity | population | pp | note |
|---|---|---|---|
| truth file canonical AME (is_cod only) | full 90-day window | 9.9923 | _truth.json, decision A6 |
| is_cod only | M2 test window | 10.5426 | same estimator, later and riskier population |
| all three payment terms | M2 test window | 10.7682 | PRIMARY - the exact per-order counterfactual |
| naive observed COD-prepaid gap | full 90-day window | 17.7326 | what 10.2 uses; 1.77x the truth |

> **The truth channel evaluates; it never targets.** Every tier, threshold and eligibility flag in this phase is computed from `m2_score`, fitted on the firewalled view. `p_rto_final` is used only to score what a policy would produce. An M2 score cannot substitute for it: M2's logit is attenuated by about 0.480 (limitation L14), so applying an un-attenuated +1.60 to a compressed logit would overstate every switch.

## 3. Tier boundaries, derived from the economics

Blueprint §8.3 derives the HIGH line properly — p-star is where a COD order's expected margin crosses zero — and then gives the LOW/MED line as 0.10, justified with *"COD order EV = +₹65"*. ₹65 has no economic meaning; the 0.10 came first and the ₹65 followed. So the LOW line is solved here instead, on a question that has an answer:

> **At what predicted RTO probability does it first become possible for a paid intervention to create value?**

§6.6 frames it — *"how much can we afford to pay to convert a COD order to prepaid?"* — and that affordable spend is a function of p. Below some p it is smaller than the cheapest paid lever in the library, and no incentive can pay for itself **even at perfect targeting, zero leakage and a 100% switch rate**.

| line | value | basis |
|---|---|---|
| LOW / MED | **p = 0.0632** | affordable switch spend crosses the ₹30 incentive anchor (§10.2) |
| MED / HIGH | **p-star = 0.2576** | `_truth.json` `breakeven_rto_probability_derived` |

The derived LOW line is **6.32%**, not §8.3's 10%. It moves with its anchor and the anchor is a lever parameter, so the sweep is reported rather than hidden:

| incentive_rupees | low_med_boundary |
|---|---|
| 25 | 0.0477 |
| 30 | 0.0632 |
| 50 | 0.1264 |

p-star re-derived on *this* population's realised costs lands at **0.2671** against the truth file's 0.2576. The 0.9pp difference is a population difference — the test window is later and riskier — and the truth file's value is the one used, per CLAUDE.md's rule that everything downstream quotes `_truth.json`.

| tier | definition | orders | share_pct | mean_score | realised_rto | cod_share | realised_cm_per_order |
|---|---|---|---|---|---|---|---|
| LOW | p < 0.0632 | 6,352 | 28.21 | 0.0349 | 0.0373 | 0.1085 | 113.3 |
| MED | 0.0632 <= p < 0.2576 | 9,850 | 43.74 | 0.1457 | 0.1535 | 0.7159 | 46.87 |
| HIGH | p >= 0.2576  (p*) | 6,318 | 28.06 | 0.3943 | 0.389 | 0.9873 | -67.48 |

Shares are **28.2 / 43.7 / 28.1**, against §8.3's expected 45 / 38 / 17. The HIGH share matches Phase 4's measured 28.1% exactly, as it must — same score, same p-star. The LOW/MED split differs from Phase 4's 40.0 / 32.0 because that used §8.3's 0.10 and this uses the derived 0.0632.

> **§8.3's expected shares were priors and they are wrong in the direction that matters.** The HIGH tier is 28.1% of orders, not 17% — **65% more traffic sits above the break-even line than Phase 1 expected.** Every restriction volume in this report inherits that, and so does the fairness exposure.

## 4. Baseline, and every lever at its best targeting depth

### 4.1 Baseline

| tier | orders | conversion | cod_share | rto_rate | cm_per_order | cm_per_session | realised_rto_rate | realised_cm_per_order |
|---|---|---|---|---|---|---|---|---|
| LOW | 6,352 | 0.1853 | 0.1085 | 0.036 | 116.18 | 21.53 | 0.0373 | 113.3 |
| MED | 9,850 | 0.2873 | 0.7159 | 0.1548 | 46.7 | 13.42 | 0.1535 | 46.87 |
| HIGH | 6,318 | 0.1843 | 0.9873 | 0.3884 | -67.66 | -12.47 | 0.389 | -67.48 |
| ALL | 22,520 | 0.6569 | 0.6207 | 0.1869 | 34.21 | 22.48 | 0.1868 | 33.53 |

`rto_rate` and `cm_per_order` are **expected** values under the truth channel; `realised_*` are what the window actually did. They agree to 0.01pp on RTO and ₹0.68 per order, which is the check that the expectation machinery is not quietly biased before a single lever runs.

### 4.2 All seven levers

Each lever is run at three targeting depths — HIGH only, MED+HIGH and flat. The row below is the depth with the highest CM/session **among those that clear §12.1**, falling back to the depth §10.1 specifies when none of them do. Here that fallback applies to every lever, because nothing clears §12.1 at any depth — see §6.2.

| id | lever | kind | best depth | §10.1 depth | treated | [A] switch | [A] abandon | d_conv_%rel | d_net_conv_%rel | d_COD_pp | d_RTO_pp | d_CM/order | d_CM/session | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D | Partial payment | RESTRICTIVE | HIGH only | HIGH only | 4,743 | 0 | 0.05 | -1.053 | 1.077 | -0.4 | -1.75 | 12.38 | 7.81 | KILL |
| G | COD gating (COD withdrawn) | RESTRICTIVE | HIGH only | HIGH only | 4,743 | 0.5 | 0.5 | -10.531 | -7.075 | -16.23 | -3.14 | 14.1 | 5.918 | KILL |
| B | COD fee | RESTRICTIVE | HIGH only | HIGH only | 4,743 | 0.25 | 0.075 | -1.58 | -0.493 | -5.96 | -0.9 | 7.99 | 4.814 | KILL |
| E | Smart payment recommendation | RESTRICTIVE | flat (all) | flat (all) | 13,979 | 0.0725 | 0 | 0 | 0.596 | -4.5 | -0.48 | 2.39 | 1.568 | ITERATE |
| C | Trust-building checkout | offer | flat (all) | flat (all) | 13,979 | 0.0483 | 0 | 0 | 0.397 | -3 | -0.32 | 1.59 | 1.045 | ITERATE |
| F | Payment-reliability routing | offer | flat (all) | flat (all) | 815 | 0.5 | 0 | 0 | 0.085 | -1.81 | -0.07 | 0.43 | 0.284 | ITERATE |
| A | Prepaid incentive | offer | MED+HIGH | MED+HIGH | 16,168 | 0.2 | 0 | 0 | 0.544 | -4.29 | -0.44 | -2.91 | -1.912 | ITERATE |

> **§10.1 and §10.2 disagree about intervention A, and the disagreement is worth ₹2.30 per session.** §10.1's target segment for the prepaid incentive is *"Medium & high risk"*; §10.2's worked example for the same lever targets high risk only. At MED+HIGH, A is worth **₹-1.912**/session; at HIGH only, **₹0.392**. The blueprint's own arithmetic is right and its own target segment is wrong — the MED-tier incentive leaks ₹30 to 2,878 orders that were already paying online, against a switch benefit too small to cover it.

**ΔCM/session is the north-star unit** (§5.2) and the one §12.1 sets its **+₹1.50** ship bar against. ΔCM/order is shown beside it because the two can point in opposite directions: a lever that removes bad orders raises the *per-order* margin while shrinking the base, and only the per-session figure notices.

**ΔRTO uses the surviving-orders denominator**, per CLAUDE.md invariant 8. An intervention that removes orders must not book that as an RTO improvement for free.

## 5. Per lever, per tier, per depth

`d_cm_per_session` uses the fixed 34,281-session denominator, so the tier rows add to the ALL row. The depth marked **best** is the one carried into §6.

### A — Prepaid incentive  ·  offer, ranks global (A47)

**[A] source:** blueprint 10.1 / 10.2. Switch stated as *within-COD rate*.

The cohort is **every order in the band**, not just the COD ones: a ₹30 incentive is displayed at the payment step and every order that ends prepaid collects it, including the ones that were always going to. That leakage is §10.2's entire argument, and defining the cohort as COD-only would delete it by construction.

Two things throttle A hard on this dataset. Only **37.2%** of COD orders carry a saved prepaid instrument, and they are not a random 37% — `has_saved_prepaid_instrument` sits at −0.60 in the COD-choice model, so holding one already makes COD less likely. A's reachable population is small *and* skewed low-risk, which is the opposite of where the money is.

| depth | tier | orders | treated | switch | d_conv_%rel | d_net_conv_%rel | d_COD_pp | d_RTO_pp | d_CM/order | d_CM/session |
|---|---|---|---|---|---|---|---|---|---|---|
| HIGH only | LOW | 6,352 | 0 | 0.2 | 0 | 0 | 0 | 0 | 0 | 0 |
| HIGH only | MED | 9,850 | 0 | 0.2 | 0 | 0 | 0 | 0 | 0 | 0 |
| HIGH only | HIGH | 6,318 | 6,318 | 0.2 | 0 | 1.5 | -6.51 | -0.92 | 2.13 | 0.392 |
| HIGH only | ALL | 22,520 | 6,318 | 0.2 | 0 | 0.317 | -1.83 | -0.26 | 0.6 | 0.392 |
| HIGH only | TREATED | 6,318 | 6,318 | 0.2 | 0 | 1.5 | -6.51 | -0.92 | 2.13 | 0.392 |
| MED+HIGH **best** | LOW | 6,352 | 0 | 0.2 | 0 | 0 | 0 | 0 | 0 | 0 |
| MED+HIGH **best** | MED | 9,850 | 9,850 | 0.2 | 0 | 0.501 | -5.65 | -0.42 | -8.02 | -2.304 |
| MED+HIGH **best** | HIGH | 6,318 | 6,318 | 0.2 | 0 | 1.5 | -6.51 | -0.92 | 2.13 | 0.392 |
| MED+HIGH **best** | ALL | 22,520 | 16,168 | 0.2 | 0 | 0.544 | -4.29 | -0.44 | -2.91 | -1.912 |
| MED+HIGH **best** | TREATED | 16,168 | 16,168 | 0.2 | 0 | 0.818 | -5.98 | -0.62 | -4.05 | -1.912 |
| flat (all) | LOW | 6,352 | 6,352 | 0.2 | 0 | 0.032 | -1.16 | -0.03 | -26.83 | -4.972 |
| flat (all) | MED | 9,850 | 9,850 | 0.2 | 0 | 0.501 | -5.65 | -0.42 | -8.02 | -2.304 |
| flat (all) | HIGH | 6,318 | 6,318 | 0.2 | 0 | 1.5 | -6.51 | -0.92 | 2.13 | 0.392 |
| flat (all) | ALL | 22,520 | 22,520 | 0.2 | 0 | 0.555 | -4.62 | -0.45 | -10.48 | -6.884 |
| flat (all) | TREATED | 22,520 | 22,520 | 0.2 | 0 | 0.555 | -4.62 | -0.45 | -10.48 | -6.884 |

**[A] band** at the best depth (lo / central / hi from `config/interventions.yaml`):

| level | switch | abandon | d_cm_per_session | d_rto_pp | d_conversion_rel_pct |
|---|---|---|---|---|---|
| lo | 0.15 | 0 | -2.064 | -0.33 | 0 |
| central | 0.2 | 0 | -1.912 | -0.44 | 0 |
| hi | 0.25 | 0 | -1.76 | -0.55 | 0 |

### B — COD fee  ·  RESTRICTIVE, ranks per-tier (A47)

**[A] source:** blueprint 10.1. Switch stated as *within-COD rate*.

Above p-star an abandoned order is a **saving**, not a loss — which is why the fee may never be applied below it. The ₹39 is modelled as pure margin: collected on delivery, buys no goods.

| depth | tier | orders | treated | switch | d_conv_%rel | d_net_conv_%rel | d_COD_pp | d_RTO_pp | d_CM/order | d_CM/session |
|---|---|---|---|---|---|---|---|---|---|---|
| HIGH only **best** | LOW | 6,352 | 0 | 0.25 | 0 | 0 | 0 | 0 | 0 | 0 |
| HIGH only **best** | MED | 9,850 | 1,771 | 0.25 | -1.348 | -0.85 | -4.94 | -0.43 | 5.8 | 1.463 |
| HIGH only **best** | HIGH | 6,318 | 2,972 | 0.25 | -3.528 | -0.506 | -12.24 | -1.92 | 16.37 | 3.351 |
| HIGH only **best** | ALL | 22,520 | 4,743 | 0.25 | -1.58 | -0.493 | -5.96 | -0.9 | 7.99 | 4.814 |
| HIGH only **best** | TREATED | 4,743 | 4,743 | 0.25 | -7.5 | -2.836 | -27.03 | -3.39 | 35.63 | 4.814 |
| MED+HIGH | LOW | 6,352 | 223 | 0.25 | -0.263 | -0.23 | -1.12 | -0.03 | 1.15 | 0.155 |
| MED+HIGH | MED | 9,850 | 4,043 | 0.25 | -3.078 | -2.028 | -11.49 | -0.92 | 13.66 | 3.392 |
| MED+HIGH | HIGH | 6,318 | 4,891 | 0.25 | -5.806 | -1.085 | -20.62 | -3.07 | 27.62 | 5.518 |
| MED+HIGH | ALL | 22,520 | 9,157 | 0.25 | -3.05 | -1.228 | -11.68 | -1.53 | 15.31 | 9.065 |
| MED+HIGH | TREATED | 9,157 | 9,157 | 0.25 | -7.5 | -3.447 | -27.03 | -3.12 | 35.58 | 9.065 |
| flat (all) | LOW | 6,352 | 689 | 0.25 | -0.814 | -0.713 | -3.47 | -0.1 | 3.55 | 0.477 |
| flat (all) | MED | 9,850 | 7,052 | 0.25 | -5.37 | -3.553 | -20.53 | -1.62 | 24.54 | 5.952 |
| flat (all) | HIGH | 6,318 | 6,238 | 0.25 | -7.405 | -1.515 | -26.76 | -3.89 | 35.6 | 6.999 |
| flat (all) | ALL | 22,520 | 13,979 | 0.25 | -4.656 | -2.173 | -18.13 | -2.12 | 23.11 | 13.429 |
| flat (all) | TREATED | 13,979 | 13,979 | 0.25 | -7.5 | -3.855 | -27.03 | -2.91 | 35.35 | 13.429 |

**[A] band** at the best depth (lo / central / hi from `config/interventions.yaml`):

| level | switch | abandon | d_cm_per_session | d_rto_pp | d_conversion_rel_pct |
|---|---|---|---|---|---|
| lo | 0.2 | 0.05 | 4.578 | -0.68 | -1.053 |
| central | 0.25 | 0.075 | 4.814 | -0.9 | -1.58 |
| hi | 0.3 | 0.1 | 5.049 | -1.11 | -2.106 |

### C — Trust-building checkout  ·  offer, ranks global (A47)

**[A] source:** blueprint 10.1. Switch stated as *population pp -> within-COD rate*.

Costs nothing and takes nothing, so no abandonment term and no leakage term. §10.1 also allows a conversion *gain*; it is held at zero here, because crediting an unmeasured gain to the library's cheapest lever would let it win by assumption.

| depth | tier | orders | treated | switch | d_conv_%rel | d_net_conv_%rel | d_COD_pp | d_RTO_pp | d_CM/order | d_CM/session |
|---|---|---|---|---|---|---|---|---|---|---|
| HIGH only | LOW | 6,352 | 0 | 0.0483 | 0 | 0 | 0 | 0 | 0 | 0 |
| HIGH only | MED | 9,850 | 0 | 0.0483 | 0 | 0 | 0 | 0 | 0 | 0 |
| HIGH only | HIGH | 6,318 | 6,238 | 0.0483 | 0 | 1.136 | -4.77 | -0.69 | 3.3 | 0.609 |
| HIGH only | ALL | 22,520 | 6,238 | 0.0483 | 0 | 0.24 | -1.34 | -0.19 | 0.93 | 0.609 |
| HIGH only | TREATED | 6,238 | 6,238 | 0.0483 | 0 | 1.153 | -4.83 | -0.7 | 3.35 | 0.609 |
| MED+HIGH | LOW | 6,352 | 0 | 0.0483 | 0 | 0 | 0 | 0 | 0 | 0 |
| MED+HIGH | MED | 9,850 | 7,052 | 0.0483 | 0 | 0.335 | -3.46 | -0.28 | 1.45 | 0.415 |
| MED+HIGH | HIGH | 6,318 | 6,238 | 0.0483 | 0 | 1.136 | -4.77 | -0.69 | 3.3 | 0.609 |
| MED+HIGH | ALL | 22,520 | 13,290 | 0.0483 | 0 | 0.392 | -2.85 | -0.32 | 1.56 | 1.024 |
| MED+HIGH | TREATED | 13,290 | 13,290 | 0.0483 | 0 | 0.742 | -4.83 | -0.54 | 2.64 | 1.024 |
| flat (all) **best** | LOW | 6,352 | 689 | 0.0483 | 0 | 0.016 | -0.52 | -0.02 | 0.11 | 0.021 |
| flat (all) **best** | MED | 9,850 | 7,052 | 0.0483 | 0 | 0.335 | -3.46 | -0.28 | 1.45 | 0.415 |
| flat (all) **best** | HIGH | 6,318 | 6,238 | 0.0483 | 0 | 1.136 | -4.77 | -0.69 | 3.3 | 0.609 |
| flat (all) **best** | ALL | 22,520 | 13,979 | 0.0483 | 0 | 0.397 | -3 | -0.32 | 1.59 | 1.045 |
| flat (all) **best** | TREATED | 13,979 | 13,979 | 0.0483 | 0 | 0.705 | -4.83 | -0.52 | 2.56 | 1.045 |

**[A] band** at the best depth (lo / central / hi from `config/interventions.yaml`):

| level | switch | abandon | d_cm_per_session | d_rto_pp | d_conversion_rel_pct |
|---|---|---|---|---|---|
| lo | 0.0322 | 0 | 0.697 | -0.22 | 0 |
| central | 0.0483 | 0 | 1.045 | -0.32 | 0 |
| hi | 0.0644 | 0 | 1.394 | -0.43 | 0 |

### D — Partial payment  ·  RESTRICTIVE, ranks per-tier (A47)

**[A] source:** phase5-judgement (10.1 states a direction, no magnitude). Switch stated as *within-COD rate*.

**The least evidenced row in the table.** §10.1 gives D no magnitudes at all. The ₹99 token is modelled as non-refundable on refusal — that is what makes it a commitment device — and it delivers a 60% dose of the full COD-to-prepaid causal logit shift. Both numbers are Phase 5 judgements with no Phase 1 antecedent, and D's rank should be read as a hypothesis to test rather than a result.

| depth | tier | orders | treated | switch | d_conv_%rel | d_net_conv_%rel | d_COD_pp | d_RTO_pp | d_CM/order | d_CM/session |
|---|---|---|---|---|---|---|---|---|---|---|
| HIGH only **best** | LOW | 6,352 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| HIGH only **best** | MED | 9,850 | 1,771 | 0 | -0.899 | 0.278 | -0.26 | -1 | 5.87 | 1.551 |
| HIGH only **best** | HIGH | 6,318 | 2,972 | 0 | -2.352 | 4.504 | -0.03 | -4.29 | 33.15 | 6.26 |
| HIGH only **best** | ALL | 22,520 | 4,743 | 0 | -1.053 | 1.077 | -0.4 | -1.75 | 12.38 | 7.81 |
| HIGH only **best** | TREATED | 4,743 | 4,743 | 0 | -5 | 6.19 | 0 | -7.91 | 58.13 | 7.81 |
| MED+HIGH | LOW | 6,352 | 223 | 0 | -0.176 | -0.103 | -0.16 | -0.07 | 0.46 | 0.047 |
| MED+HIGH | MED | 9,850 | 4,043 | 0 | -2.052 | 0.469 | -0.6 | -2.18 | 13.22 | 3.444 |
| MED+HIGH | HIGH | 6,318 | 4,891 | 0 | -3.871 | 7.227 | -0.05 | -7.06 | 52.97 | 9.867 |
| MED+HIGH | ALL | 22,520 | 9,157 | 0 | -2.033 | 1.704 | -0.79 | -3.1 | 21.47 | 13.358 |
| MED+HIGH | TREATED | 9,157 | 9,157 | 0 | -5 | 4.784 | 0 | -7.33 | 51.92 | 13.358 |
| flat (all) | LOW | 6,352 | 689 | 0 | -0.542 | -0.32 | -0.49 | -0.22 | 1.45 | 0.15 |
| flat (all) | MED | 9,850 | 7,052 | 0 | -3.58 | 0.801 | -1.05 | -3.84 | 23.33 | 5.982 |
| flat (all) | HIGH | 6,318 | 6,238 | 0 | -4.937 | 9.053 | -0.07 | -9 | 67 | 12.354 |
| flat (all) | ALL | 22,520 | 13,979 | 0 | -3.104 | 2.168 | -1.21 | -4.42 | 30.14 | 18.486 |
| flat (all) | TREATED | 13,979 | 13,979 | 0 | -5 | 3.845 | 0 | -6.88 | 47.56 | 18.486 |

**[A] band** at the best depth (lo / central / hi from `config/interventions.yaml`):

| level | switch | abandon | d_cm_per_session | d_rto_pp | d_conversion_rel_pct |
|---|---|---|---|---|---|
| lo | 0 | 0.03 | 6.794 | -1.2 | -0.632 |
| central | 0 | 0.05 | 7.81 | -1.75 | -1.053 |
| hi | 0 | 0.08 | 8.668 | -2.27 | -1.685 |

### E — Smart payment recommendation  ·  RESTRICTIVE, ranks per-tier (A47)

**[A] source:** blueprint 10.1, reclassified by A48. Switch stated as *population pp -> within-COD rate*.

**Zero abandonment here is a constraint, not an estimate.** COD must remain reachable in ONE TAP in every variant of E. Reordering and de-emphasis are permitted; an extra tap, a hidden menu, a collapsed accordion or a confirmation interstitial is not. §10.1 already draws this line — 'emphasis is acceptable; hiding or burying COD is not' — and E is the only lever that can cross it without any copy changing. A Phase 6 arm that loses conversion has breached it, and is intervention G wearing E's name.

| depth | tier | orders | treated | switch | d_conv_%rel | d_net_conv_%rel | d_COD_pp | d_RTO_pp | d_CM/order | d_CM/session |
|---|---|---|---|---|---|---|---|---|---|---|
| HIGH only | LOW | 6,352 | 0 | 0.0725 | 0 | 0 | 0 | 0 | 0 | 0 |
| HIGH only | MED | 9,850 | 1,771 | 0.0725 | 0 | 0.134 | -1.3 | -0.11 | 0.56 | 0.161 |
| HIGH only | HIGH | 6,318 | 2,972 | 0.0725 | 0 | 0.827 | -3.41 | -0.51 | 2.45 | 0.452 |
| HIGH only | ALL | 22,520 | 4,743 | 0.0725 | 0 | 0.235 | -1.53 | -0.19 | 0.93 | 0.613 |
| HIGH only | TREATED | 4,743 | 4,743 | 0.0725 | 0 | 1.353 | -7.25 | -0.91 | 4.43 | 0.613 |
| MED+HIGH | LOW | 6,352 | 223 | 0.0725 | 0 | 0.008 | -0.25 | -0.01 | 0.05 | 0.009 |
| MED+HIGH | MED | 9,850 | 4,043 | 0.0725 | 0 | 0.289 | -2.98 | -0.24 | 1.24 | 0.358 |
| MED+HIGH | HIGH | 6,318 | 4,891 | 0.0725 | 0 | 1.35 | -5.61 | -0.83 | 3.96 | 0.73 |
| MED+HIGH | ALL | 22,520 | 9,157 | 0.0725 | 0 | 0.419 | -2.95 | -0.34 | 1.67 | 1.097 |
| MED+HIGH | TREATED | 9,157 | 9,157 | 0.0725 | 0 | 1.175 | -7.25 | -0.84 | 4.11 | 1.097 |
| flat (all) **best** | LOW | 6,352 | 689 | 0.0725 | 0 | 0.024 | -0.79 | -0.02 | 0.17 | 0.032 |
| flat (all) **best** | MED | 9,850 | 7,052 | 0.0725 | 0 | 0.503 | -5.19 | -0.42 | 2.17 | 0.623 |
| flat (all) **best** | HIGH | 6,318 | 6,238 | 0.0725 | 0 | 1.704 | -7.16 | -1.04 | 4.95 | 0.913 |
| flat (all) **best** | ALL | 22,520 | 13,979 | 0.0725 | 0 | 0.596 | -4.5 | -0.48 | 2.39 | 1.568 |
| flat (all) **best** | TREATED | 13,979 | 13,979 | 0.0725 | 0 | 1.057 | -7.25 | -0.78 | 3.84 | 1.568 |

**[A] band** at the best depth (lo / central / hi from `config/interventions.yaml`):

| level | switch | abandon | d_cm_per_session | d_rto_pp | d_conversion_rel_pct |
|---|---|---|---|---|---|
| lo | 0.0483 | 0 | 1.045 | -0.32 | 0 |
| central | 0.0725 | 0 | 1.568 | -0.48 | 0 |
| hi | 0.0967 | 0 | 2.09 | -0.65 | 0 |

### F — Payment-reliability routing  ·  offer, ranks global (A47)

**[A] source:** blueprint 10.1 - "depends entirely on H11's magnitude". Switch stated as *share of failure-driven COD orders the fix removes*.

H11 is **measured** at 5.9% in `_truth.json`, against an 8-15% prior — so F's ceiling is set by the data, not by an assumption. It can only move the 815 orders that are COD *because* a payment failed. The 271 sessions that abandoned at the payment-failure step are NOT counted: they have no score and no order row, so crediting them would need an imputed margin. **This row is therefore a floor on F, not an estimate of it.**

| depth | tier | orders | treated | switch | d_conv_%rel | d_net_conv_%rel | d_COD_pp | d_RTO_pp | d_CM/order | d_CM/session |
|---|---|---|---|---|---|---|---|---|---|---|
| HIGH only | LOW | 6,352 | 0 | 0.5 | 0 | 0 | 0 | 0 | 0 | 0 |
| HIGH only | MED | 9,850 | 0 | 0.5 | 0 | 0 | 0 | 0 | 0 | 0 |
| HIGH only | HIGH | 6,318 | 29 | 0.5 | 0 | 0.041 | -0.23 | -0.03 | 0.11 | 0.021 |
| HIGH only | ALL | 22,520 | 29 | 0.5 | 0 | 0.009 | -0.06 | -0.01 | 0.03 | 0.021 |
| HIGH only | TREATED | 29 | 29 | 0.5 | 0 | 9.481 | -50 | -5.47 | 24.47 | 0.021 |
| MED+HIGH | LOW | 6,352 | 0 | 0.5 | 0 | 0 | 0 | 0 | 0 | 0 |
| MED+HIGH | MED | 9,850 | 383 | 0.5 | 0 | 0.115 | -1.94 | -0.1 | 0.53 | 0.152 |
| MED+HIGH | HIGH | 6,318 | 29 | 0.5 | 0 | 0.041 | -0.23 | -0.03 | 0.11 | 0.021 |
| MED+HIGH | ALL | 22,520 | 412 | 0.5 | 0 | 0.061 | -0.91 | -0.05 | 0.26 | 0.173 |
| MED+HIGH | TREATED | 412 | 412 | 0.5 | 0 | 3.195 | -50 | -2.7 | 14.36 | 0.173 |
| flat (all) **best** | LOW | 6,352 | 403 | 0.5 | 0 | 0.072 | -3.17 | -0.07 | 0.6 | 0.112 |
| flat (all) **best** | MED | 9,850 | 383 | 0.5 | 0 | 0.115 | -1.94 | -0.1 | 0.53 | 0.152 |
| flat (all) **best** | HIGH | 6,318 | 29 | 0.5 | 0 | 0.041 | -0.23 | -0.03 | 0.11 | 0.021 |
| flat (all) **best** | ALL | 22,520 | 815 | 0.5 | 0 | 0.085 | -1.81 | -0.07 | 0.43 | 0.284 |
| flat (all) **best** | TREATED | 815 | 815 | 0.5 | 0 | 2.124 | -50 | -1.91 | 11.96 | 0.284 |

**[A] band** at the best depth (lo / central / hi from `config/interventions.yaml`):

| level | switch | abandon | d_cm_per_session | d_rto_pp | d_conversion_rel_pct |
|---|---|---|---|---|---|
| lo | 0.3 | 0 | 0.171 | -0.04 | 0 |
| central | 0.5 | 0 | 0.284 | -0.07 | 0 |
| hi | 0.7 | 0 | 0.398 | -0.1 | 0 |

### G — COD gating (COD withdrawn)  ·  RESTRICTIVE, ranks per-tier (A47)

**[A] source:** phase5-judgement (not a 10.1 lever; named by the A47 ruling). Switch stated as *gate: switch + abandon = 1 by construction*.

Not a §10.1 lever — named by the A47 ruling. Gating is absolute, so switch + abandon = 1 by construction and **one assumption decides the whole lever**. Phase 4 priced it at switch = 0 as a conservative floor; §8 is how this row should actually be read.

| depth | tier | orders | treated | switch | d_conv_%rel | d_net_conv_%rel | d_COD_pp | d_RTO_pp | d_CM/order | d_CM/session |
|---|---|---|---|---|---|---|---|---|---|---|
| HIGH only **best** | LOW | 6,352 | 0 | 0.5 | 0 | 0 | 0 | 0 | 0 | 0 |
| HIGH only **best** | MED | 9,850 | 1,771 | 0.5 | -8.99 | -7.82 | -12.68 | -1.09 | 3.5 | -0.291 |
| HIGH only **best** | HIGH | 6,318 | 2,972 | 0.5 | -23.52 | -16.68 | -31.14 | -5.47 | 23.25 | 6.209 |
| HIGH only **best** | ALL | 22,520 | 4,743 | 0.5 | -10.531 | -7.075 | -16.23 | -3.14 | 14.1 | 5.918 |
| HIGH only **best** | TREATED | 4,743 | 4,743 | 0.5 | -50 | -40.671 | -100 | -12.53 | 61.13 | 5.918 |
| MED+HIGH | LOW | 6,352 | 223 | 0.5 | -1.755 | -1.652 | -3.38 | -0.1 | 1.09 | -0.18 |
| MED+HIGH | MED | 9,850 | 4,043 | 0.5 | -20.523 | -18.166 | -33.16 | -2.51 | 10.64 | -0.324 |
| MED+HIGH | HIGH | 6,318 | 4,891 | 0.5 | -38.707 | -28.952 | -63.95 | -9.73 | 44.93 | 9.902 |
| MED+HIGH | ALL | 22,520 | 9,157 | 0.5 | -20.331 | -14.92 | -35.2 | -5.52 | 26.69 | 9.398 |
| MED+HIGH | TREATED | 9,157 | 9,157 | 0.5 | -50 | -41.894 | -100 | -11.55 | 56.64 | 9.398 |
| flat (all) | LOW | 6,352 | 689 | 0.5 | -5.423 | -5.138 | -10.85 | -0.29 | 2.94 | -0.653 |
| flat (all) | MED | 9,850 | 7,052 | 0.5 | -35.797 | -31.774 | -71.59 | -5.3 | 23.96 | -0.382 |
| flat (all) | HIGH | 6,318 | 6,238 | 0.5 | -49.367 | -37.516 | -98.73 | -14.31 | 67.73 | 12.476 |
| flat (all) | ALL | 22,520 | 13,979 | 0.5 | -31.037 | -24.079 | -62.07 | -8.2 | 40.65 | 11.441 |
| flat (all) | TREATED | 13,979 | 13,979 | 0.5 | -50 | -42.71 | -100 | -10.77 | 53.04 | 11.441 |

**[A] band** at the best depth (lo / central / hi from `config/interventions.yaml`):

| level | switch | abandon | d_cm_per_session | d_rto_pp | d_conversion_rel_pct |
|---|---|---|---|---|---|
| lo | 0.25 | 0.75 | 4.649 | -3.44 | -15.796 |
| central | 0.5 | 0.5 | 5.918 | -3.14 | -10.531 |
| hi | 0.75 | 0.25 | 7.188 | -2.88 | -5.265 |

## 6. The decision table

Risk tier × intervention, at each lever's best targeting depth. Tiers are the derived economic lines from §3, never percentiles. A cell is what happens **in that tier** when the lever is deployed — which includes tiers the lever was not aimed at, and those are the rows that decide whether it ships.

| tier | lever | name | depth_label | treated | d_conversion_rel_pct | d_net_conversion_rel_pct | d_cod_share_pp | d_rto_pp | d_cm_per_order | d_cm_per_session |
|---|---|---|---|---|---|---|---|---|---|---|
| HIGH | D | Partial payment | HIGH only | 2,972 | -2.352 | 4.504 | -0.03 | -4.29 | 33.15 | 6.26 |
| HIGH | G | COD gating (COD withdrawn) | HIGH only | 2,972 | -23.52 | -16.68 | -31.14 | -5.47 | 23.25 | 6.209 |
| HIGH | B | COD fee | HIGH only | 2,972 | -3.528 | -0.506 | -12.24 | -1.92 | 16.37 | 3.351 |
| HIGH | E | Smart payment recommendation | flat (all) | 6,238 | 0 | 1.704 | -7.16 | -1.04 | 4.95 | 0.913 |
| HIGH | C | Trust-building checkout | flat (all) | 6,238 | 0 | 1.136 | -4.77 | -0.69 | 3.3 | 0.609 |
| HIGH | A | Prepaid incentive | MED+HIGH | 6,318 | 0 | 1.5 | -6.51 | -0.92 | 2.13 | 0.392 |
| HIGH | F | Payment-reliability routing | flat (all) | 29 | 0 | 0.041 | -0.23 | -0.03 | 0.11 | 0.021 |
| LOW | F | Payment-reliability routing | flat (all) | 403 | 0 | 0.072 | -3.17 | -0.07 | 0.6 | 0.112 |
| LOW | E | Smart payment recommendation | flat (all) | 689 | 0 | 0.024 | -0.79 | -0.02 | 0.17 | 0.032 |
| LOW | C | Trust-building checkout | flat (all) | 689 | 0 | 0.016 | -0.52 | -0.02 | 0.11 | 0.021 |
| LOW | A | Prepaid incentive | MED+HIGH | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LOW | B | COD fee | HIGH only | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LOW | D | Partial payment | HIGH only | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| LOW | G | COD gating (COD withdrawn) | HIGH only | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| MED | D | Partial payment | HIGH only | 1,771 | -0.899 | 0.278 | -0.26 | -1 | 5.87 | 1.551 |
| MED | B | COD fee | HIGH only | 1,771 | -1.348 | -0.85 | -4.94 | -0.43 | 5.8 | 1.463 |
| MED | E | Smart payment recommendation | flat (all) | 7,052 | 0 | 0.503 | -5.19 | -0.42 | 2.17 | 0.623 |
| MED | C | Trust-building checkout | flat (all) | 7,052 | 0 | 0.335 | -3.46 | -0.28 | 1.45 | 0.415 |
| MED | F | Payment-reliability routing | flat (all) | 383 | 0 | 0.115 | -1.94 | -0.1 | 0.53 | 0.152 |
| MED | G | COD gating (COD withdrawn) | HIGH only | 1,771 | -8.99 | -7.82 | -12.68 | -1.09 | 3.5 | -0.291 |
| MED | A | Prepaid incentive | MED+HIGH | 9,850 | 0 | 0.501 | -5.65 | -0.42 | -8.02 | -2.304 |
| TREATED | D | Partial payment | HIGH only | 4,743 | -5 | 6.19 | 0 | -7.91 | 58.13 | 7.81 |
| TREATED | G | COD gating (COD withdrawn) | HIGH only | 4,743 | -50 | -40.671 | -100 | -12.53 | 61.13 | 5.918 |
| TREATED | B | COD fee | HIGH only | 4,743 | -7.5 | -2.836 | -27.03 | -3.39 | 35.63 | 4.814 |
| TREATED | E | Smart payment recommendation | flat (all) | 13,979 | 0 | 1.057 | -7.25 | -0.78 | 3.84 | 1.568 |
| TREATED | C | Trust-building checkout | flat (all) | 13,979 | 0 | 0.705 | -4.83 | -0.52 | 2.56 | 1.045 |
| TREATED | F | Payment-reliability routing | flat (all) | 815 | 0 | 2.124 | -50 | -1.91 | 11.96 | 0.284 |
| TREATED | A | Prepaid incentive | MED+HIGH | 16,168 | 0 | 0.818 | -5.98 | -0.62 | -4.05 | -1.912 |

### 6.1 §12.1 guardrails — three of six are evaluable

| lever | depth | ΔCM/session | agg checkout %rel | agg NET %rel | LOW checkout %rel | LOW NET %rel | treated ΔRTO pp | verdict |
|---|---|---|---|---|---|---|---|---|
| A | HIGH only | 0.392 | 0 | 0.317 | 0 | 0 | -0.92 | ITERATE (CM/session below the +1.50 ship bar) |
| A | MED+HIGH | -1.912 | 0 | 0.544 | 0 | 0 | -0.62 | ITERATE (CM/session below the +1.50 ship bar) |
| A | flat (all) | -6.884 | 0 | 0.555 | 0 | 0.032 | -0.45 | ITERATE (CM/session below the +1.50 ship bar) |
| B | HIGH only | 4.814 | -1.58 | -0.493 | 0 | 0 | -3.39 | KILL (aggregate checkout-conversion floor breached) |
| B | MED+HIGH | 9.065 | -3.05 | -1.228 | -0.263 | -0.23 | -3.12 | KILL (aggregate checkout-conversion floor breached) |
| B | flat (all) | 13.429 | -4.656 | -2.173 | -0.814 | -0.713 | -2.91 | KILL (low-risk checkout conversion — fairness failure) |
| C | HIGH only | 0.609 | 0 | 0.24 | 0 | 0 | -0.7 | ITERATE (CM/session below the +1.50 ship bar) |
| C | MED+HIGH | 1.024 | 0 | 0.392 | 0 | 0 | -0.54 | ITERATE (CM/session below the +1.50 ship bar) |
| C | flat (all) | 1.045 | 0 | 0.397 | 0 | 0.016 | -0.52 | ITERATE (CM/session below the +1.50 ship bar) |
| D | HIGH only | 7.81 | -1.053 | 1.077 | 0 | 0 | -7.91 | KILL (aggregate checkout-conversion floor breached) |
| D | MED+HIGH | 13.358 | -2.033 | 1.704 | -0.176 | -0.103 | -7.33 | KILL (aggregate checkout-conversion floor breached) |
| D | flat (all) | 18.486 | -3.104 | 2.168 | -0.542 | -0.32 | -6.88 | KILL (low-risk checkout conversion — fairness failure) |
| E | HIGH only | 0.613 | 0 | 0.235 | 0 | 0 | -0.91 | ITERATE (CM/session below the +1.50 ship bar) |
| E | MED+HIGH | 1.097 | 0 | 0.419 | 0 | 0.008 | -0.84 | ITERATE (CM/session below the +1.50 ship bar) |
| E | flat (all) | 1.568 | 0 | 0.596 | 0 | 0.024 | -0.78 | ITERATE (mechanism did not fire: RTO moved < 2.0pp in treated tiers) |
| F | HIGH only | 0.021 | 0 | 0.009 | 0 | 0 | -5.47 | ITERATE (CM/session below the +1.50 ship bar) |
| F | MED+HIGH | 0.173 | 0 | 0.061 | 0 | 0 | -2.7 | ITERATE (CM/session below the +1.50 ship bar) |
| F | flat (all) | 0.284 | 0 | 0.085 | 0 | 0.072 | -1.91 | ITERATE (CM/session below the +1.50 ship bar) |
| G | HIGH only | 5.918 | -10.531 | -7.075 | 0 | 0 | -12.53 | KILL (aggregate checkout-conversion floor breached) |
| G | MED+HIGH | 9.398 | -20.331 | -14.92 | -1.755 | -1.652 | -11.55 | KILL (low-risk checkout conversion — fairness failure) |
| G | flat (all) | 11.441 | -31.037 | -24.079 | -5.423 | -5.138 | -10.77 | KILL (low-risk checkout conversion — fairness failure) |

Floors: ΔCM/session ≥ **+₹1.50** · aggregate conversion ≥ **-1.0% rel** · **LOW-tier conversion ≥ -0.3% rel** · treated-tier ΔRTO ≤ **-2.0pp**.

> **Three clauses cannot be evaluated from a simulation and are not quietly passed:** 95% CI excludes zero; power >= 80%; >=30d maturation; complaint rate <= +5% rel; refund rate <= +5% rel; 30-day repeat purchase >= -1.0% rel. A verdict with half its clauses unevaluated is a shortlist, not a launch decision — which is why the passing verdict reads SHORTLIST and never LAUNCH.

The LOW-tier clause is checked **first and alone**, because §12.2 makes it a KILL *regardless of CM*. Reading it in sequence with the others would let a large CM gain argue against it, which is what pre-committing it in Phase 1 was meant to prevent.

### 6.2 Recommended action per tier — at the depths §10.1 specifies

| tier | action | lever | depth | treated_in_tier | d_cm_per_session | d_conversion_rel_pct | d_rto_pp | why |
|---|---|---|---|---|---|---|---|---|
| LOW | NO INTERVENTION | - | - | 0 | 0 | 0 | 0 | no lever clears the §12.1 guardrails with a positive CM contribution in this tier |
| MED | NO INTERVENTION | - | - | 0 | 0 | 0 | 0 | no lever clears the §12.1 guardrails with a positive CM contribution in this tier |
| HIGH | NO INTERVENTION | - | - | 0 | 0 | 0 | 0 | no lever clears the §12.1 guardrails with a positive CM contribution in this tier |

**Nothing in the library ships as specified.** That is not a marginal result: 0 of 21 lever-and-depth configurations clear §12.1, and the two failure modes are cleanly separated by lever kind. Every stick breaches a conversion floor; every carrot falls short of the +₹1.50 CM bar. §6.4 asks the useful question instead — not *does this configuration pass* but *what configuration would*.

### 6.3 FA-01 re-measured on every restrictive lever and depth

| lever | name | depth | treated | worst_over_best | limit | verdict |
|---|---|---|---|---|---|---|
| B | COD fee | high_only | 4,743 | 1.35 | 2.5 | PASS |
| B | COD fee | med_high | 9,157 | 2.35 | 2.5 | PASS |
| D | Partial payment | high_only | 4,743 | 1.35 | 2.5 | PASS |
| D | Partial payment | med_high | 9,157 | 2.35 | 2.5 | PASS |
| E | Smart payment recommendation | high_only | 4,743 | 1.35 | 2.5 | PASS |
| E | Smart payment recommendation | med_high | 9,157 | 2.35 | 2.5 | PASS |
| G | COD gating (COD withdrawn) | high_only | 4,743 | 1.35 | 2.5 | PASS |
| G | COD gating (COD withdrawn) | med_high | 9,157 | 2.35 | 2.5 | PASS |

Re-measured rather than inherited: the eligible volume here is set by the **derived** risk bands, not by Phase 4's 17%, and a ruling that holds at one volume and not another has not been enforced. Worst measured ratio **2.35x** against the 2.5x limit.

### 6.4 What would have to be true — the shippable configuration

Two knobs, one per kind of lever, and **neither is a behavioural assumption**. The [A] band stays exactly where `config/interventions.yaml` puts it; what moves is the policy.

**Restrictions have a volume.** A47 ruled on *which* orders a restriction picks and said nothing about how many. Volume is a free policy parameter, and the aggregate conversion floor caps it. Sweeping it for the COD fee — holding A47's per-tier selection and the §8.4 overlay fixed throughout, so this varies how much restriction and never how it is chosen:

| volume | treated | d_cm_per_session | agg_checkout_rel_pct | agg_net_rel_pct | treated_rto_pp | shippable |
|---|---|---|---|---|---|---|
| 0.05 | 969 | 1.048 | -0.323 | -0.041 | -3.88 | no |
| 0.08 | 1,505 | 1.609 | -0.501 | -0.088 | -3.75 | yes |
| 0.1 | 1,843 | 1.965 | -0.614 | -0.12 | -3.69 | yes |
| 0.13 | 2,340 | 2.471 | -0.779 | -0.169 | -3.65 | yes |
| 0.17 | 2,993 | 3.13 | -0.997 | -0.243 | -3.59 | yes |
| 0.18 | 3,162 | 3.292 | -1.053 | -0.266 | -3.56 | no |
| 0.25 | 4,292 | 4.37 | -1.429 | -0.425 | -3.44 | no |
| 0.28 | 4,733 | 4.804 | -1.576 | -0.491 | -3.39 | no |

**The COD fee ships at a restricted volume between 8% and 17%.** Below the floor of that window it does not clear the +₹1.50 bar; above the top of it, the aggregate checkout-conversion floor breaks. That window is the most actionable number this phase produces, and it is **derived**: the abandonment rate is §10.1's prior, the selection rule is A47's ruling, and the cap falls out of the two.

> The top of the window lands on **17%**, the volume §8.3 pre-committed the HIGH tier to before anyone had a model. **That is a coincidence and should be presented as one.** §8.3 set 17% from an expected tier share that turned out to be wrong — the HIGH tier is 28.1% — and this 17% comes from a conversion floor. Two unrelated routes to the same number is worth noticing and is not worth treating as confirmation.

**Offers have a required switch rate.** They cost conversion nothing, so the ship bar is what binds. Inverting it: what switch rate would each offer need to deliver ₹1.50 per session?

| lever | §10.1 prior switch | switch needed for ₹1.50 | multiple of prior | max ΔCM/session at a 100% switch | reachable |
|---|---|---|---|---|---|
| A | 0.2 | - | - | 0.514 | no |
| C | 0.0483 | 0.07 | 1.45 | 21.627 | yes |
| F | 0.5 | - | - | 0.569 | no |

**1 of 3 offers can reach the ship bar, but only above its §10.1 prior:** **C** at 7.0% (1.45x its §10.1 prior). That is a testable claim rather than a shippable configuration — it says what the experiment has to find, not what the lever will do. **A and F** cannot reach it at any switch rate, including 100%.

The mechanism behind the unreachable ones is worth stating: an offer can only move COD orders onto the prepaid rail; the true causal value of doing so is about 10pp of RTO rather than the naive 17.7pp; and A additionally pays its incentive to every order that was already prepaid, which is why A gets *worse* as it is rolled out wider. The carrots are not badly designed — they are being asked to clear a bar §12.1 deliberately set at 3.5x its own break-even, with a mechanism the economics cap.

### 6.5 Recommended action per tier — at shippable volumes

| tier | action | lever | treated_in_tier | d_cm_per_session | d_conversion_rel_pct | d_rto_pp |
|---|---|---|---|---|---|---|
| LOW | NO INTERVENTION | - | 0 | 0 | 0 | 0 |
| MED | RUN D at 26% restricted volume | Partial payment | 1,639 | 1.483 | -0.832 | -0.96 |
| HIGH | RUN D at 26% restricted volume | Partial payment | 2,794 | 5.921 | -2.211 | -4.05 |

> ⚠ **The recommendation lands on Partial payment, which is the least evidenced lever in the library.** §10.1 gives partial payment no magnitudes at all — not a switch rate, not an abandonment rate, not an RTO effect — so its commitment dose and its token retention are Phase 5 judgements with no Phase 1 antecedent. It wins here *because* its assumed abandonment is the lowest of the sticks, which is exactly the parameter nobody has measured.
>
> **Do not read this row as a recommendation to build D.** Read it as: under assumptions this project invented, D would dominate — which makes measuring those assumptions the highest-value experiment in the programme, and makes B the lever to ship first because its numbers were committed to in advance.

This is the risk-based pricing answer and it is narrower than the intervention library implies: **one lever, at a volume well below what a naive reading of the tier shares would suggest.** Everything else is either an experiment worth running for the information or a build that cannot pay for itself at the effect sizes Phase 1 assumed.

## 7. H10 — does risk-based beat one-size-fits-all?

Blueprint H10 is the thesis of the case study, and §12.2 pre-commits to killing the risk engine if the effect exists only in the flat arm. The comparison therefore has to be genuine, and §10.2's version is not: it compares a flat rollout against a targeted one at **different volumes**, so it measures volume and selection together and credits both to targeting.

Three arms, and the third is the one that answers the question.

* **targeted** — the risk score, A47 per-tier ranking for sticks, the §8.4 overlay.
* **flat** — same lever, same cash terms, every order it can act on, no score and no overlay. §10.2's comparator, and what a PM means by one-size-fits-all.
* **random, volume-matched** — the *same number* of orders, chosen at random. **This isolates the risk engine.** Margin over this arm is targeting; margin over the flat arm may be nothing but a bigger intervention.

| id | lever | reported depth | targeted | flat | random-matched | premium vs random | premium as % of targeted | beats flat | flat verdict |
|---|---|---|---|---|---|---|---|---|---|
| A | Prepaid incentive | MED+HIGH | -1.912 | -6.884 | -4.91 | 2.998 | 157% | yes | ITERATE |
| B | COD fee | HIGH only | 4.814 | 13.429 | 4.561 | 0.253 | 5% | no | KILL |
| C | Trust-building checkout | flat (all) | 1.045 | 1.045 | 1.045 | - | - | tie | ITERATE |
| D | Partial payment | HIGH only | 7.81 | 18.486 | 6.072 | 1.738 | 22% | no | KILL |
| E | Smart payment recommendation | flat (all) | 1.568 | 1.568 | 1.568 | - | - | tie | ITERATE |
| F | Payment-reliability routing | flat (all) | 0.284 | 0.284 | 0.284 | - | - | tie | ITERATE |
| G | COD gating (COD withdrawn) | HIGH only | 5.918 | 11.441 | 4.1 | 1.818 | 31% | no | KILL |

**3 of the seven levers are reported at flat depth already** — C, E and F are shown to everyone by design — so for those three the three arms are the same arm and the comparison is degenerate rather than lost. H10 is contested on the remaining **4**: A, B, D and G.

### 7.1 The finding

Three results, and they do not all point the same way.

**1. Targeting loses to a flat rollout on margin — on 3 of the 4.** A flat COD fee earns nearly three times the targeted one, because a ₹39 fee collected on a mid-risk COD order is close to pure margin against an order whose expected value is already positive, and §10.1's 7.5% abandonment does not cost enough to offset it. **This is not an artefact of a weak comparator** — the flat arm is the same lever, at the same price, differing only in that it does not consult the score.

**2. Targeting beats a random cohort of the same size — 4 of 4 — but by less than the thesis implies.** Median premium over random selection at equal volume is **26%** of the targeted arm's own CM. For the COD fee specifically the premium is small, and the reason is structural: a ₹39 fee is profitable across most of the risk distribution, so choosing *which* orders to charge adds little when charging almost any of them works. **The risk engine earns most where the lever is expensive and least where the lever is cheap** — which is the opposite of where a fee sits.

**3. What separates the targeted policy is not margin — it is the guardrails, and they were pre-committed in Phase 1.** 0 of seven flat arms clear §12.1, and 3 are outright KILLs on a conversion floor. So the honest statement of the result is weaker than H10 claims:

> Risk-based targeting does not beat one-size-fits-all on contribution margin. It beats it on **margin per unit of harm to low-risk customers** — and the only reason that is the deciding criterion is that Phase 1 wrote the harm constraint down before anyone had a number to argue with.

**A reader who rejects §12.1's conversion floors should read this table as §12.2's kill clause:** *effect exists in the flat arm too ⇒ ship flat, kill the risk engine.* The floors are what stand between this project and that conclusion, and the floors are an [A]. That is the most important sentence in this report and it is not the one the thesis wanted.

**The number Phase 6 should be powered to detect is the premium over the random-matched arm** — same volume, same cash, no score. That is the risk engine's actual contribution. Powering on the flat-versus-targeted gap that §10.2 reports would measure the size of the intervention and call it the value of the model.

## 8. Sensitivity — the two assumptions that drive everything

Everything economic here is derived; everything behavioural is assumed. Two assumptions carry most of the variance: the **COD-to-prepaid switch rate**, which scales every carrot and *is* the mechanism of G, and the **abandonment rate under a fee**, which sets the sign of the stick levers.

> **A second, independent anchor for the fee's conversion cost.** The DGP carries a planted `conversion_model.shipping_fee_charged_gt0 = −0.45` that never fired, because `params.yaml` sets `shipping_fee_charged: 0`. Applied to this window's conversion it implies an abandonment rate of **16.3%** — far above §10.1's 5-10% prior. It is marked on the sweep, not substituted for the prior: it was planted for a *shipping* fee and nobody calibrated it against ₹39 of COD fee. If it is the better anchor, every fee result moves to the right-hand end of the grid.

### 8.1 B — COD fee   (targeted at HIGH only)

ΔCM per session. Rows are the switch rate, columns the abandonment rate. §12.1's ship bar is ₹1.50.

| switch | 0.0 | 0.025 | 0.05 | 0.075 | 0.1 | 0.15 | 0.2 | 0.3 |
|---|---|---|---|---|---|---|---|---|
| 0 | 3.62 | 3.62 | 3.61 | 3.6 | 3.6 | 3.59 | 3.58 | 3.55 |
| 0.05 | 3.86 | 3.86 | 3.85 | 3.85 | 3.84 | 3.83 | 3.82 | 3.79 |
| 0.1 | 4.11 | 4.1 | 4.1 | 4.09 | 4.08 | 4.07 | 4.06 | 4.03 |
| 0.15 | 4.35 | 4.34 | 4.34 | 4.33 | 4.32 | 4.31 | 4.3 | 4.28 |
| 0.2 | 4.59 | 4.58 | 4.58 | 4.57 | 4.57 | 4.55 | 4.54 | 4.52 |
| 0.25 | 4.83 | 4.83 | 4.82 | 4.81 | 4.81 | 4.8 | 4.78 | 4.76 |
| 0.3 | 5.07 | 5.07 | 5.06 | 5.06 | 5.05 | 5.04 | 5.03 | 5 |
| 0.4 | 5.56 | 5.55 | 5.54 | 5.54 | 5.53 | 5.52 | 5.51 | 5.48 |
| 0.5 | 6.04 | 6.03 | 6.03 | 6.02 | 6.02 | 6 | 5.99 | 5.97 |

**Abandonment barely moves the margin.** Across the whole 0-30% abandonment range at the central 25% switch rate, ΔCM/session spans **₹0.07** — because every treated order sits above p-star, where §10.2's insight bites: an abandoned order there was worth less than nothing, so losing it is close to free. What abandonment destroys is not margin but **conversion**: at the top of that range the aggregate checkout-conversion move is -6.32% relative, against a −1.0% floor. **The binding constraint on a fee is the guardrail, not the economics** — which is why §6.4 sweeps volume and not price.

**Targeting premium.** Against the flat arm it is negative in **72 of 72** grid cells. Against the random, volume-matched arm it is negative in **17 of 72**. The second number is the one about targeting; the first is mostly about volume.

### 8.2 G — COD gating (COD withdrawn)   (targeted at HIGH only)

ΔCM per session. Gating has no third branch, so abandonment is **1 − switch by construction** and there is no second axis to sweep. One assumption decides the whole lever.

| switch | abandon (= 1 - switch) | d_cm_per_session | d_conversion_rel_pct | low_conv_rel_pct | d_rto_pp | clears_ship_bar |
|---|---|---|---|---|---|---|
| 0 | 1 | 3.379 | -21.061 | 0 | -3.78 | yes |
| 0.05 | 0.95 | 3.633 | -20.008 | 0 | -3.71 | yes |
| 0.1 | 0.9 | 3.887 | -18.955 | 0 | -3.64 | yes |
| 0.15 | 0.85 | 4.141 | -17.902 | 0 | -3.57 | yes |
| 0.2 | 0.8 | 4.395 | -16.849 | 0 | -3.5 | yes |
| 0.25 | 0.75 | 4.649 | -15.796 | 0 | -3.44 | yes |
| 0.3 | 0.7 | 4.903 | -14.743 | 0 | -3.38 | yes |
| 0.4 | 0.6 | 5.411 | -12.637 | 0 | -3.26 | yes |
| 0.5 | 0.5 | 5.918 | -10.531 | 0 | -3.14 | yes |

The lever clears the ship bar at every switch rate on the grid, including **zero** — a gate that converts nobody and loses every treated order still raises margin, because the orders it destroys had negative expected value. That is the cleanest possible statement of §10.2's insight, and it is also why CM cannot be the criterion here: at switch = 0 this lever deletes 21% of orders and the §12.1 conversion floors reject it on sight.

**Targeting premium.** Against the flat arm it is negative in **48 of 72** grid cells. Against the random, volume-matched arm it is negative in **0 of 72**. The second number is the one about targeting; the first is mostly about volume.

### 8.3 Where the risk-based policy stops winning

Three failure modes, and only one of them is about targeting.

1. **The conversion floor binds before the economics do.** For a lever aimed above p-star, more abandonment is nearly free in margin terms and expensive in conversion terms. The lever does not stop being profitable; it stops being *permissible*. This is the mode that actually decides every stick in this library, and it is why §6.4's answer is a volume window rather than a price.
2. **The flat arm out-earns the targeted one.** True across most of both grids, and it is §7's result. The targeted policy is preferred on the §12.1 guardrails, not on margin.
3. **The lever's own assumption set is wrong.** Only G is genuinely fragile this way, and the sweep shows why: it clears the margin bar at every switch rate including zero, so the switch rate does not decide whether G *works* — it decides how much conversion G destroys on the way. That makes it a guardrail parameter, not an economics parameter, and it is measured by an experiment rather than argued from a model.

**The single number the whole answer turns on is G's switch rate**, and nobody has measured it on this platform or any other. It is the first thing Phase 6 should be powered to estimate.

## 9. What the obvious analysis would have concluded

Blueprint §10.2 prices a switch at `(24.0% − 4.1%) × ₹416` — the **observed** COD/prepaid RTO gap, applied flat to every switched order. `_truth.json` measures that gap at **17.73pp** against a true average marginal effect of **9.99pp**: the naive figure is **1.77x** the truth, because 43.6% of it is selection.

Same simulation, one substitution — the counterfactual prepaid probability comes from the flat rate gap instead of the planted structural shift:

| id | lever | ΔCM causal (₹) | ΔCM naive (₹) | naive / causal |
|---|---|---|---|---|
| A | Prepaid incentive | -65,545 | -73,767 | 1.13x |
| B | COD fee | 165,022 | 153,218 | 0.93x |
| C | Trust-building checkout | 35,831 | 29,950 | 0.84x |
| D | Partial payment | 267,740 | 250,104 | 0.93x |
| E | Smart payment recommendation | 53,747 | 44,926 | 0.84x |
| F | Payment-reliability routing | 9,749 | 10,317 | 1.06x |
| G | COD gating (COD withdrawn) | 202,886 | 179,278 | 0.88x |

### 9.1 The naive analysis is not uniformly optimistic, and that is the finding

**5 of the 7 levers come out LOWER under the naive channel, not higher.** That looks like it contradicts the 1.77x headline. It does not, and the reconciliation is the most transferable thing in this section.

The 1.77x is a statement about **population averages**. Applied to an individual order it does not carry, and it does not even carry with a consistent sign, because a logit shift is not a constant pp shift — it is largest near p = 0.5 and vanishes at both extremes:

| p_cod | causal drop (pp) | naive flat-gap drop (pp) | naive / causal | direction |
|---|---|---|---|---|
| 0.05 | 3.95 | 5 | 1.27 | overstates |
| 0.1 | 7.81 | 10 | 1.28 | overstates |
| 0.15 | 11.56 | 15 | 1.3 | overstates |
| 0.2 | 15.2 | 17.73 | 1.17 | overstates |
| 0.26 | 19.38 | 17.73 | 0.92 | understates |
| 0.3 | 22.04 | 17.73 | 0.8 | understates |
| 0.4 | 28.14 | 17.73 | 0.63 | understates |
| 0.5 | 33.2 | 17.73 | 0.53 | understates |

The two agree at **p = 0.236**, and diverge in opposite directions either side of it. Below that line a flat gap **overstates** the value of switching an order; above it, it **understates**. Every lever in this library is aimed above the line — which is the half of the distribution where the obvious analysis is *conservative*.

> **The lesson is not "naive overstates". It is that a population rate gap is not a per-order effect.** An analyst who applies the 17.7pp gap order by order will misprice every order that is not at the population mean, and the direction of the error flips at 23.6%. The headline figure is right about the aggregate and useless for targeting — which is precisely why §10.2's flat-arm arithmetic and its targeted-arm arithmetic cannot both be built on it.

The crossover sits close to p-star (0.2576) and that is a **coincidence of this parameterisation**, not a result: it depends on β = 1.6, on the observed gap, and on nothing about the cost model that produces p-star. It should not be presented as though the two were connected.

> And read L15 before quoting the residual. Adjustment closes ~71% of the naive-to-truth gap; the remaining ~29% is irreducible because purchase intent is unobservable — and on real data that residual would likely be **larger**, because our simulated treatment assignment is unusually recoverable. **~29% is an optimistic floor, not a realistic estimate.**

## 10. What is deliberately absent

### 10.1 H6 — no "shorten the promise" lever

**There is no "shorten the delivery promise" lever in this library and there will not be one.** Delay is post-dispatch (Stage-4 bar) and is a logistics fix, not a checkout lever. GT-06: realised delay carries 15.4x the deviance of the delivery promise.

Delay is a real and large RTO driver — the single strongest signal in the warehouse, and the most tempting feature anyone will propose. It is nonetheless a **logistics** fix (courier mix, dispatch SLA, network coverage), and putting it in this library would put a lever in the PRD that checkout cannot pull. Evidence: GT-06 / blueprint H6 / phase4_closeout 2.3.

Stated rather than omitted, because a silently missing lever reads as an oversight and this one is a ruling.

### 10.2 The Stage-4 bar

`attempt_delay_days`, `delivery_delay_days`, `delivery_attempts` and `actual_delivery_days` appear in no scoring path, no eligibility rule and no counterfactual in this phase. `dataset.assert_firewall` re-asserts them by name on the frame this simulation was built from. The support/NDR line is taken at its registry mean specifically to avoid needing `delivery_attempts`.

### 10.3 What Phase 4 priced on one side only

The A47 fairness constraint cost ₹3.92 Cr/year and the other side of that trade was never priced. Nothing in this phase changes that, and nothing here should be read as having closed it. The per-tier policy remains the natural control arm for measuring it, and that is a Phase 6 experiment.

## 11. What Stage 2 inherits

| Artefact | What it is |
|---|---|
| `data/processed/phase5_levers.parquet` | the decision table: lever × depth × tier |
| `config/interventions.yaml` | every [A]; change one number and re-run |
| §3's derived tier lines | LOW/MED **0.0632**, MED/HIGH **0.2576** |

**Open, and blocking a confident recommendation:**

1. **G's switch rate.** One assumption fixes the entire lever and nobody has measured it. Phase 6 should be powered for it first.
2. **D's commitment dose.** §10.1 gives partial payment no magnitudes at all; the 60% dose is a Phase 5 judgement and D's rank moves with it.
3. **Whether §10.1's fee-abandonment prior or the DGP's planted −0.45 is the better anchor.** They disagree by roughly a factor of two, and the sign of the flat-versus-targeted comparison moves with them.
4. **F's recovered sessions.** The 271 payment-failure abandons have no score and no order row, so F is reported here as a floor. Stage 2 needs an explicit imputation or an explicit statement that it has none.
5. **§8.3's tier shares were priors and the HIGH tier is 28.1%, not 17%.** Every restriction volume and every fairness exposure in Stage 2 inherits that.
