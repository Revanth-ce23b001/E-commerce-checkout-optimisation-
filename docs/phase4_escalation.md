# The §8.4 escalation — geography, and what a fairness constraint costs

**Status:** RULED (decision **A47**, 2026-08-26). Enforced by **FA-01**.
**Generated from:** `reports/phase4_m1.md` §6 · `reports/phase4_m2.md` §6.
**Reproduce with:** `make m1` then `make m2` then `make validate`.

---

## The finding

> **No model of this dataset both clears the §9.4 feasibility gate of 0.72 AUC and
> passes the §8.4 fairness audit at 2.5x. Removing geography from the feature set
> does not remove geography from the model: four non-geographic features
> reconstruct the delivery tier at a cost of 0.0015 AUC.**

That is the whole result, and it is a finding about the business rather than
about the model. It is written down here as a standalone document rather than as
a subsection of a model report because it is the most transferable thing Phase 4
produced, and because a reader who needs it is not usually reading a scorecard.

## 1. What §8.4 said, and when it said it

Blueprint §8.4 pre-committed, before any model existed:

> *"Geography can inform the score but must not be the **sole** driver of a
> restriction. Audit: measure the intervention rate by geo tier; if Tier-3
> restriction rate is >2.5x Metro, the model is proxying for postcode, not
> behaviour. Escalate."*

And it said why the constraint was written first:

> *"A risk model in a consumer product is a **policy**, not just a classifier. I
> wrote the fairness constraints before I wrote the model, because after you have
> the AUC it's very hard to argue yourself out of using it."*

That sentence is what makes the rest of this document possible. The audit was not
negotiated after the number arrived.

## 2. The audit failed, at every volume

M1 (pre-selection, 48 features, test AUC 0.7530), restriction rate by geo tier
across a sweep of restricted volumes:

| volume flagged | METRO | TIER1 | TIER2 | TIER3 | Tier-3 ÷ Metro | verdict |
|---|---|---|---|---|---|---|
| 0.05 | 0.0000 | 0.0007 | 0.0201 | 0.1771 | no Metro flagged | ESCALATE |
| 0.10 | 0.0000 | 0.0029 | 0.0563 | 0.3349 | no Metro flagged | ESCALATE |
| 0.17 | 0.0002 | 0.0076 | 0.1324 | 0.5257 | 2677x | ESCALATE |
| 0.25 | 0.0012 | 0.0255 | 0.2553 | 0.6904 | 586x | ESCALATE |

At the 17% volume §8.3 expects the High tier to occupy, the policy restricts
**52.6% of Tier-3 orders and 0.02% of Metro orders**. The limit is 2.5x. This is
not a marginal breach and no amount of threshold-nudging closes it.

> **One measurement note, because two different Tier-3 numbers appear in this
> document.** This table is measured **before** the §8.4 customer-level
> protections run, because the question it answers is *what is the score doing*.
> §8 and FA-01 measure **after** them, because the question there is *what does a
> customer experience*. The same M1 policy reads 52.6% pre-overlay and 42.3%
> post-overlay at the 17% volume. Neither is wrong; they answer different
> questions, and mixing them would understate the score's concentration while
> overstating what the finished policy does.

## 3. The model is reporting geography, not distorting it

The audit cannot tell those apart on its own, and the answer changes the remedy.

| geo_tier | orders | observed RTO | mean M1 score | score − observed | RTO lift |
|---|---|---|---|---|---|
| METRO | 5,093 | 0.0640 | 0.0597 | −0.43pp | 0.34 |
| TIER1 | 5,533 | 0.1122 | 0.1069 | −0.53pp | 0.60 |
| TIER2 | 6,271 | 0.2081 | 0.2066 | −0.15pp | 1.11 |
| TIER3 | 5,623 | 0.3477 | 0.3473 | −0.03pp | 1.86 |

**The score is right.** Mean M1 score tracks observed RTO within each tier to
under a percentage point. The 5.4x spread in restriction rates exists because
there is a 5.4x spread in actual RTO.

**A correct score can still produce an unshippable policy.** §8.4 constrains what
may be *done* with a score; it is not a diagnostic of whether the score is
accurate. Confusing the two is the most common way this constraint gets argued
away — "but the model is right" is true and irrelevant.

## 4. The load-bearing measurement

Refit M1 with geographic feature blocks removed, one block at a time:

| model | features | test AUC | Metro flagged | Tier-3 flagged | Tier-3 ÷ Metro | verdict |
|---|---|---|---|---|---|---|
| full model | 48 | 0.7530 | 0.0002 | 0.5257 | 2677x | ESCALATE |
| no `geo_tier` dummies | 45 | 0.7515 | 0.0012 | 0.5031 | 427x | ESCALATE |
| no geographic features at all | 41 | 0.6934 | 0.0813 | 0.2616 | 3.2x | ESCALATE |
| no point-in-time history | 35 | 0.7217 | 0.0000 | 0.6237 | no Metro flagged | ESCALATE |

Three results, in ascending order of how much they transfer:

### 4.1 Dropping the protected attribute does almost nothing

AUC moves **0.0015**. The ratio stays two orders of magnitude above the limit.
`serviceability_score`, `courier_reliability_score`, `cod_cultural_index` and
`estimated_delivery_days` are all derived from the delivery address, and between
them they reconstruct the tier immediately.

> **Removing a protected attribute from a model does not remove it from the
> model.** Any fairness claim resting on "we don't use geography as a feature" is
> unfalsifiable theatre. The only honest test is the one on outcomes.

This is the part that generalises past this dataset, past RTO, and past India.
Anywhere a protected attribute correlates with operational reality, a
sufficiently rich feature set reconstructs it for free.

### 4.2 There is no version of this model that satisfies both constraints

A fully geography-blind M1 lands at **AUC 0.6934** — below §9.4's 0.72, which by
Phase 1's own pre-commitment means *coarse tiers only, no fine-grained fee
ladders*. And it **still breaches §8.4** at 3.2x.

So the choice was never "accurate model or fair model". It was:

* an accurate model that cannot be used as a restriction policy, or
* a weakened model that **still** cannot be used as a restriction policy.

Both branches fail. That is why this went to an escalation rather than a
feature-selection decision.

### 4.3 History moderates the concentration; it does not cause it

Strip the point-in-time features and **no Metro order is flagged at all**. The
customer-behaviour signal is the only thing pulling the policy away from pure
geography. The instinct to drop history features "because they might encode
disadvantage" would make the geographic concentration strictly worse.

## 5. The options, as escalated

Stated as options rather than a recommendation, because §8.4 makes this the
intervention owner's decision and not the modeller's.

| Option | Effect on §8.4 | Effect on §9.4 | Cost |
|---|---|---|---|
| 1. Ship as scored, restrict on score alone | fails, 2677x | clears at 0.7530 | restricts half of Tier-3 and none of Metro |
| 2. Strip geography from the features | still fails, 3.2x | fails, 0.6934 | 5.7pp AUC for a breach that remains |
| 3. **Restrict within geo tier (per-tier thresholds)** | equalises by construction | preserved | flags Metro orders that are genuinely low risk |
| 4. Use the score for carrots only | not applicable | not applicable | forgoes the restriction lever entirely |
| 5. Re-anchor the 2.5x limit against the measured 5.4x spread | rule changes | preserved | requires an explicit written ruling that the limit was set without this measurement |

Option 5 needed saying out loud. §8.4's 2.5x was written before anyone had
measured the geographic spread in RTO, and the measured spread is 5.4x. That may
mean the limit is wrong. It may equally mean the limit is right and this business
genuinely cannot price Tier-3 risk through restrictions. **Deciding that having
just seen the AUC is precisely what §8.4 was written to prevent** — which is why
the modeller stopped and did not decide it.

## 6. The ruling — A47

**Option 3, with three conditions.**

> Restrict WITHIN geo tier. Rank orders against their own tier's distribution,
> not the global one. A Metro order at the 95th Metro percentile gets treated
> like a Tier-3 order at the 95th Tier-3 percentile.
>
> The objection is accepted as a real cost: this flags Metro orders that are
> genuinely low absolute risk. We are deliberately choosing a policy that is less
> margin-optimal than the score permits, because a policy that restricts half of
> Tier-3 and none of Metro is one we could not defend to a customer, a regulator,
> or a journalist.
>
> That trade IS the product decision. Phase 1 §8.4 pre-committed to it before the
> AUC existed, which is exactly why the pre-commitment was written down.

**Condition 1 — sticks only.** Per-tier thresholds apply to **restrictive**
interventions only. Incentives, trust messaging and payment-reliability routing
use the global score. *Carrots are not rationed by geography; sticks are.*

**Condition 2 — price the constraint.** Report expected CM under global-threshold
vs per-tier-threshold restriction at the same total restriction volume. The price
of the fairness constraint goes on the record as a number, not a principle.

**Condition 3 — report the level, not just the ratio.** Report what per-tier
thresholding does to Tier-3's *absolute* restriction rate. Equalising the ratio
is not the same as making Tier-3's exposure acceptable.

### The classification the ruling implies

| Intervention (§10.1) | Kind | Threshold basis |
|---|---|---|
| A — Prepaid incentive | offer | global |
| B — COD fee | **RESTRICTIVE** | per-tier |
| C — Trust-building checkout | offer | global |
| D — Partial payment | **RESTRICTIVE** | per-tier |
| E — Smart payment recommendation | **RESTRICTIVE** | per-tier |
| F — Payment-reliability routing | offer | global |
| G — COD gating | **RESTRICTIVE** | per-tier |

**`E` was reclassified as restrictive (decision A48).** It was first listed as an
offer on the reasoning that reordering removes nothing. That reasoning was wrong.
E does not only emphasise some options — it **de-emphasises others**, and for a
payment method chosen by 62% of orders largely out of habit, salience *is* the
option. A lever expected to move prepaid share by 3-6pp through position alone is
exercising the same power as a fee while requiring none of a fee's disclosure.

E therefore ranks **per-tier**, and carries a hard product floor into the Phase 5
PRD as non-negotiable:

> **COD must remain reachable in ONE TAP in every variant of E.** Reordering and
> de-emphasis are permitted; an extra tap, a hidden menu, a collapsed accordion
> or a confirmation interstitial is not.

The constraint lives in `src/risk/interventions.py:ONE_TAP_CONSTRAINT` as well as
in the PRD, because a constraint that lives only in a document is a constraint
that gets lost at the next handover.

## 7. What the ruling cost, measured

M2 (post-selection, 51 features, test AUC 0.7684), 22,520-order test window,
annualisation factor 227.3 (derived).

| volume | selected | ΔCM global | ΔCM per-tier | price of fairness | ₹/restricted order | ₹ Cr / year |
|---|---|---|---|---|---|---|
| 0.05 | 1,126 | ₹144,382 | ₹69,401 | ₹74,981 | ₹74.98 | 1.70 |
| 0.10 | 2,252 | ₹211,378 | ₹101,720 | ₹109,658 | ₹57.02 | 2.49 |
| **0.17** | **3,829** | **₹295,117** | **₹122,483** | **₹172,634** | **₹54.94** | **3.92** |
| 0.25 | 5,630 | ₹341,891 | ₹113,400 | ₹228,491 | ₹51.00 | 5.19 |

**At the §8.3 volume of 17%, the fairness constraint costs ₹3.92 Cr a year** —
about **2.4%** of the project's ~₹165 Cr headline RTO exposure.

The counterfactual is stated once and applied identically to both arms: a
restricted order does not happen, so its realised contribution margin goes to zero
and the policy's effect on it is `−cm`. An RTO carries a negative margin, so
removing it is a gain; a delivered order carries a positive one, so removing it is
a loss. This is §10.2's own frame — *above p\*, an abandoned order is a saving* —
and it puts no behavioural parameter anywhere in the arithmetic.

**The price is robust to the behavioural response.** Sweeping the share of
restricted COD orders that switch to prepaid rather than abandoning from 0% to
50%, both arms improve together and the price moves from ₹3.92 Cr to ₹4.07 Cr —
under 4%. The answer does not depend on picking a switch rate.

## 8. What the ruling fixed, and what it did not (condition 3)

At the 17% volume, per-tier thresholds versus global. Both columns are measured
**post-overlay** — what a customer experiences — which is why the global Tier-3
rate reads 42.3% here against §2's pre-overlay 52.6%:

| geo_tier | rate (global) | rate (per-tier) | tier RTO | mean score of restricted set | RTO of restricted set | share below p\* |
|---|---|---|---|---|---|---|
| METRO | 0.0002 | 0.1109 | 0.0640 | 0.1551 | 0.1575 | **0.9681** |
| TIER1 | 0.0078 | 0.1254 | 0.1122 | 0.2455 | 0.2651 | 0.7061 |
| TIER2 | 0.1150 | 0.1442 | 0.2081 | 0.4035 | 0.3861 | 0.0000 |
| TIER3 | 0.4227 | 0.1496 | 0.3477 | 0.5762 | 0.5600 | 0.0000 |

**Fixed.** Tier-3's absolute restriction rate falls from **42.3% to 15.0%** —
from more than two in five Tier-3 orders to about one in seven. The absolute
exposure does not merely become *equal*; it becomes roughly a third of what it
was. That is the direct answer to condition 3.

**Not fixed, part 1 — the cost lands where the objection said it would.** 96.8%
of the Metro orders the policy now restricts score *below* p\*, meaning their
expected contribution margin as COD orders is positive. The policy restricts them
anyway. That is the trade, and §7 is what it costs.

**Not fixed, part 2 — under-restriction is the mirror image.** Tier-3's restricted
orders carry a 56.0% realised RTO rate against Metro's 15.8%. Per-tier
thresholding does not make Tier-3 safe; it stops the policy from *pricing* how
unsafe it is. Both tiers are cut at their own 83rd percentile, so a Tier-3 order
just below the cut goes unrestricted while a Metro order just above it is
restricted at 3.6x less risk.

**Equalising the ratio moved the unfairness. It did not delete it.** Anyone
presenting this policy should say so before someone else does.

## 9. Why the ratio is 1.35x and not 1.00x

Per-tier selection equalises exactly: 17.00% of every tier, before anything else
runs. The §8.4 *customer-level* protections then veto different shares in
different tiers.

| geo_tier | clean record | zero history | protected | tier RTO |
|---|---|---|---|---|
| METRO | 33.87% | 12.59% | 46.46% | 0.0640 |
| TIER1 | 30.20% | 11.78% | 41.98% | 0.1122 |
| TIER2 | 23.94% | 11.67% | 35.61% | 0.2081 |
| TIER3 | 16.75% | 12.20% | 28.95% | 0.3477 |

A clean record is three delivered orders and zero prior RTO. Tier-3 runs 5.4x
Metro's RTO rate, so half as many Tier-3 customers hold one. Zero-history
immunity is almost flat across tiers, so essentially the entire residual gradient
comes from the clean-record cap.

**The residual spread is caused by a protection, not by the score** — and it is a
protection that helps Metro more than Tier-3, which is worth knowing about any
"clean record" style carve-out before designing another one.

### 9.1 The 1.35x floor is accepted, documented, and must not be engineered away

**RULE (A47, condition 4).** The residual ratio is a **floor**, not a defect. It
is not to be optimised toward 1.00.

The gradient behind it is **real**. Metro customers hold clean records at 33.9%
against Tier-3's 16.8% because they genuinely have different delivery-success
histories — Tier-3 runs 5.4x Metro's RTO rate, so a Tier-3 customer has a
materially harder time accumulating three delivered orders and zero returns.
That is a fact about outcomes, not an artefact of how the score was fitted or
how the tiers were cut.

**The only way to remove the residual is to weaken the clean-record protection
for Metro customers** — cap fewer of them, or raise the bar for what counts as a
clean record so that fewer Metro customers clear it. Both are strictly worse
outcomes for real customers, and both run directly against §8.4, whose *first*
protection is "never take away a payment option from a customer with a clean
record." Equalising a fairness ratio by protecting fewer good customers is not a
fairness improvement. It is a fairness metric improvement, which is a different
and much cheaper thing.

**FA-01's 2.5x limit sits comfortably above the floor.** The measured worst-case
is 1.44x across every volume tested; the limit is 2.5x. There is roughly 1.1x of
headroom, which is the room a future refit needs and is not room to be spent
chasing 1.00. If a later model pushes the ratio toward 2.5x, the diagnosis to run
first is whether the *score* has concentrated further — not whether the overlay
can be trimmed to buy back margin on the metric.

Stated as a standing instruction for anyone who inherits this: **if you find
yourself proposing a change to the clean-record cap in order to move FA-01's
number, you have found the wrong lever.** The number is downstream of a
protection working as designed.

## 10. How this is prevented from regressing

**FA-01**, HARD, its own validation family:

```
Restrictive-intervention rate ratio, worst geo tier over best geo tier,
must be <= 2.5x at every restriction volume in [0.05, 0.10, 0.17, 0.25].
```

Measured **post-overlay**, which is what a customer actually experiences.
Worst-over-best rather than Tier-3-over-Metro, so a policy that concentrated on
some other tier could not pass a check watching the wrong pair.

Current result: **PASS, worst 1.44x**. The same model under global thresholds
fails at every volume — which is the point. *The ruling changed the policy, not
the score.*

FA-01 reads the fairness result published by `scripts/07_fit_m2.py`, hash-guarded
against the dataset. If that result was computed on a different dataset, FA-01
reports **SKIP, never PASS**.

> A ruling that is not tested is a ruling that regresses. §8.4 wrote its
> constraint down before the AUC existed because the accurate thing is easy to
> argue for. FA-01 is the same precaution one layer down: it makes the *ruling*
> as hard to talk your way out of as the original rule.

---

## Appendix — the three sentences worth carrying out of this

1. **Removing a protected attribute from a model does not remove it from the
   model.** Geography survived at a cost of 0.0015 AUC, reconstructed from four
   features nobody would call geographic.
2. **A correct score can still be an indefensible policy.** The model tracked
   observed RTO within each tier to under a percentage point and still produced a
   policy that restricted half of one tier and none of another.
3. **A fairness constraint has a price, and refusing to compute it is not
   modesty.** Here it is ₹3.92 Cr a year, 2.4% of the headline exposure — a
   number small enough to pay and specific enough to argue with.
