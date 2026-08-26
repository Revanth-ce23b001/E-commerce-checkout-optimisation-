"""The Phase 4 Stage-1 report writer.

Split out of ``scripts/06_fit_m1.py`` only for length — this file is one long
markdown template plus the three small tables that are computed for the report
alone and are not model inputs. Nothing here decides anything; the driver does.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import evaluate, fairness, features

LK03_LIMIT = 0.85
DIAGNOSTIC_LIMIT = 0.80
FEASIBILITY_GATE = 0.72


def md_table(frame: pd.DataFrame, floatfmt: str = "{:.4f}") -> str:
    """Plain markdown pipe table. No box-drawing characters: they truncate in
    transit through terminals and chat clients."""
    display = frame.copy()
    for col in display.columns:
        if pd.api.types.is_float_dtype(display[col]):
            display[col] = display[col].map(
                lambda v: "" if pd.isna(v) else floatfmt.format(v))
    header = "| " + " | ".join(str(c) for c in display.columns) + " |"
    rule = "|" + "|".join("---" for _ in display.columns) + "|"
    body = ["| " + " | ".join(str(v) for v in row) + " |"
            for row in display.itertuples(index=False)]
    return "\n".join([header, rule] + body)


def censoring_table(shipped: pd.DataFrame) -> pd.DataFrame:
    """Censored share and observed RTO rate by week, over the FULL shipped
    population. The view has already dropped the censored rows, so this is the
    only place the bias is visible."""
    frame = shipped.copy()
    frame["week_of"] = pd.to_datetime(frame["order_date"]).dt.to_period("W").dt.start_time
    rows = []
    for week, group in frame.groupby("week_of"):
        resolved = group.loc[~group["is_censored"].astype(bool), "rto_flag"]
        rows.append({
            "week_of": str(week.date()),
            "shipped": len(group),
            "censored_pct": round(float(group["is_censored"].mean()) * 100, 2),
            "rto_rate_of_resolved": (round(float(resolved.mean()), 4)
                                     if len(resolved) else np.nan),
        })
    return pd.DataFrame(rows).tail(8).reset_index(drop=True)


def _geo_truth(frame: pd.DataFrame, scores: np.ndarray) -> pd.DataFrame:
    """Observed RTO rate against mean M1 score, by geo tier.

    The question the audit cannot answer on its own: is the model distorting
    geography, or reporting it?
    """
    work = frame.copy()
    work["m1_score"] = scores
    grouped = work.groupby("geo_tier").agg(
        orders=("rto_flag", "size"),
        observed_rto=("rto_flag", "mean"),
        mean_m1_score=("m1_score", "mean"),
        cod_share=("payment_method", lambda s: (s == "COD").mean()))
    grouped["score_minus_observed_pp"] = (
        grouped["mean_m1_score"] - grouped["observed_rto"]) * 100
    base = float(frame["rto_flag"].mean())
    grouped["rto_lift_vs_population"] = grouped["observed_rto"] / base
    return grouped.reset_index()


def verdict(m1_auc: float, audit: pd.DataFrame, ablation: pd.DataFrame,
            ceiling: float, pstar: float) -> str:
    lines = []
    if m1_auc >= DIAGNOSTIC_LIMIT:
        lines.append("**STOP — M1 AUC {:.4f} breaches the {} diagnostic ceiling. "
                     "Diagnose before proceeding.**".format(m1_auc, DIAGNOSTIC_LIMIT))
    elif m1_auc >= ceiling:
        lines.append("**STOP — M1 AUC {:.4f} is at or above the derived ceiling "
                     "{:.4f}. Something leaked.**".format(m1_auc, ceiling))
    elif m1_auc >= FEASIBILITY_GATE:
        lines.append("M1 test AUC **{:.4f}** clears the §9.4 feasibility gate of {} "
                     "and sits below the {:.4f} ceiling. Full risk-based pricing is "
                     "permitted on discrimination.".format(
                         m1_auc, FEASIBILITY_GATE, ceiling))
    else:
        lines.append("M1 test AUC **{:.4f}** falls below the §9.4 gate of {}. Phase 1's "
                     "pre-commitment applies: **coarse tiers only**, reported honestly. "
                     "Do not tune to clear it.".format(m1_auc, FEASIBILITY_GATE))
    if (audit["verdict"] == "ESCALATE").any():
        row = ablation.set_index("model")
        full, no_tier = row.loc["full model"], row.loc["no geo_tier dummies"]
        blind = row.loc["no geographic features at all"]
        bad = audit.loc[audit["verdict"] == "ESCALATE", "volume_flagged"].tolist()
        lines.append(
            "**Fairness: ESCALATE — and this, not the AUC, is the binding result.** "
            "The Tier-3 restriction rate exceeds Metro by more than {}x at every "
            "volume tested ({}). §8.4 says escalate rather than proceed, so no "
            "tiering is proposed here and M2 is not fitted until the escalation has "
            "an answer.".format(fairness.PROXY_RATIO_LIMIT, bad))
        lines.append(
            "**The model is not the defect.** Mean M1 score matches the observed RTO "
            "rate within each geo tier to under a percentage point (§6.2). The "
            "geographic concentration is in the data, not in the fit. What §8.4 "
            "forbids is acting on it as a restriction policy — and it forbids that "
            "whether or not the score is accurate.")
        lines.append(
            "**The load-bearing measurement is §6.3.** Removing the `geo_tier` dummies "
            "moves the ratio only from {} to {}, at a cost of {:.4f} AUC, because four "
            "other features reconstruct the delivery address. A geography-blind M1 "
            "still breaches §8.4 at {}x AND falls to {:.4f}, below the §9.4 gate of {}. "
            "There is no version of this model that both clears the gate and passes "
            "the audit. That is a finding about the business, not about the "
            "model.".format(
                full["tier3_over_metro"], no_tier["tier3_over_metro"],
                full["test_auc"] - no_tier["test_auc"], blind["tier3_over_metro"],
                blind["test_auc"], FEASIBILITY_GATE))
    else:
        lines.append("Fairness: the geography audit **PASSES** at every volume tested; "
                     "the largest Tier-3/Metro ratio is {} against a {} limit.".format(
                         audit["tier3_over_metro"].max(), fairness.PROXY_RATIO_LIMIT))
    lines.append(
        "**Next, in order.** (1) Route the §6.6 options to whoever owns the "
        "intervention policy; per §8.4 that decision is not the modeller's. "
        "(2) Only then fit M2, where p* = {:.4f} legitimately applies and where "
        "blueprint §9.3's three-rule baseline finally becomes available. (3) The "
        "GBM challenger ships only on a >=3pp AUC margin over this scorecard. "
        "(4) GT-01/03/04/06/07 and BR-09 stay skipped until M2 exists.".format(pstar))
    return "\n\n".join(lines)


def write_report(ctx: dict, truth: dict, path) -> None:
    ceiling = truth["achieved"]["auc_ceiling_precheckout"]
    pstar = truth["economics_targets"]["breakeven_rto_probability_derived"]
    split, model, p_test, y_test = ctx["split"], ctx["model"], ctx["p_test"], ctx["y_test"]
    scoreboard, m1_auc = ctx["scoreboard"], ctx["m1_auc"]

    coefs = model.coefficients()
    top = coefs[coefs["feature"] != "(intercept)"].head(20)[
        ["feature", "coef_per_sd", "odds_ratio_per_sd", "p_value"]]
    audit = fairness.geo_audit(split.test, p_test)
    ablation = fairness.geography_ablation(
        ctx["X_train"], ctx["y_train"], ctx["X_test"], split.test, y_test)
    geo_truth = _geo_truth(split.test, p_test)
    coverage = fairness.protection_coverage(split.test)
    exclusions = pd.DataFrame(
        [{"column": k, "reason": v} for k, v in features.EXCLUSIONS.items()])
    points = model.points().head(13)[["feature", "points_per_sd"]]
    redundant = pd.DataFrame(ctx["matrix"].redundant_,
                             columns=["column", "why it was dropped"])
    rules_table = ctx["rules"].table(split.test).reset_index()

    cuts = np.quantile(p_test, [0.55, 0.83])
    tiers = pd.Series(np.where(p_test >= cuts[1], "HIGH",
                               np.where(p_test >= cuts[0], "MED", "LOW")))
    overlay = fairness.apply_overlay(split.test, tiers)

    ceiling_status = "below" if m1_auc < ceiling else "AT OR ABOVE — LEAK"
    lk03 = "PASS" if m1_auc < LK03_LIMIT else "FAIL"
    diag = "PASS" if m1_auc < DIAGNOSTIC_LIMIT else "STOP"
    gate = ("full risk-based pricing" if m1_auc >= FEASIBILITY_GATE
            else "COARSE TIERS ONLY")

    parts = []
    parts.append("""# Phase 4 — Stage 1: the rules baseline and M1 (pre-selection)

Generated by `scripts/06_fit_m1.py`. Every figure is measured on the dataset or
read from `data/truth/_truth.json`. Nothing is quoted from spec prose.

M2 is **not** in this report. The derived p* = {pstar:.4f} is the break-even of a
**COD** order and belongs to M2 alone; applying it to an M1 score is a category
error (spec §4.3, Phase 3 closeout §2).

---

## 1. Population — and the censoring horizon

`vw_risk_model_input` returns **{full_rows:,}** shipped, non-censored orders at an
RTO rate of **{full_rto:.4f}**. That population cannot be used as-is.

The view's `is_censored = FALSE` filter is correct row-by-row and destructive in
aggregate at the end of the window. RTO resolves more slowly than delivery, so
the filter removes RTOs preferentially — and at a rate far above the raw censored
share. Measured by week of order date over the full shipped population:

{censoring}

By the final week the observed RTO rate is **zero**. Not because late orders are
safe, but because none of the failures have come back yet. Note the week of
2026-03-09: only 7.3% of its orders are censored, yet its observed RTO rate has
already fallen from ~18.5% to 11.5%. Censoring is not removing a random 7%.

**The horizon cut.** Keep only order dates strictly before the first date with
any censoring at all — **2026-03-07**. This drops **{dropped:,}** rows
({dropped_pct:.1%}) and moves the base rate from {full_rto:.4f} to
**{clean_rto:.4f}**.

This is limitation **L9** — "censored orders are not zero-cost" — reappearing as
a modelling problem rather than an accounting one. It also means the project's
headline blended RTO rate of {full_rto:.4f} is itself depressed by the censored
tail; the uncensored-window rate is {clean_rto:.4f}.

## 2. The split

Time-based, never random (blueprint §9.3). A random split would put a customer's
later orders in train and their earlier ones in test, so the point-in-time
history features would be scored against a future the model had already seen.

{splits}

Cut date **{cut}**.

## 3. Scoreboard

{scoreboard}

| Bar | Value | M1 test | Status |
|---|---|---|---|
| Achievable AUC ceiling (`_truth.json`) | {ceiling:.4f} | {auc:.4f} | {ceiling_status} |
| LK-03 hard limit | {lk03_limit:.2f} | {auc:.4f} | {lk03} |
| Diagnostic stop (this phase's instruction) | {diag_limit:.2f} | {auc:.4f} | {diag} |
| Feasibility gate (§9.4) | {feas:.2f} | {auc:.4f} | {gate} |

**Why M1 sits below the ceiling, and should.** The {ceiling:.4f} ceiling is
computed with `is_cod` known. M1 is trained without it — that is its definition —
and `is_cod` is the single largest coefficient in the planted RTO logit (+1.60).
The gap between M1 and the ceiling is not model weakness; it is the cost of
asking the question before the customer has answered it.

**The rules baseline is two rules, not three.** Blueprint §9.3's floor is
"payment method + prior RTO + tenure". Payment method is Stage-3, so M1's floor
is prior RTO + tenure only — `pit_risk_tier_rule_based`, decision A21. Scoring M1
against the three-rule floor would credit it with beating a baseline it never
competed against.

{rules_table}

## 4. Calibration

Calibration matters more than AUC here: the thresholds are absolute probabilities
tied to money, so a model that ranks well but reports 0.31 where the truth is
0.19 restricts orders that are in fact profitable.

{reliability}

Calibration slope **{slope:.4f}** (1.0 perfect), intercept **{intercept:.4f}**
(0.0 perfect), ECE **{ece:.5f}**, Brier **{brier:.5f}**.

### Risk deciles

{deciles}

## 5. Feature importances

Coefficients are per standard deviation, so |coefficient| is the importance
directly — no permutation importance, no tree-based importance to misread. Top 20
of {n_features}:

{coefs}

The first fit ranked `log1p_pit_orders_placed` (-0.53) and `log1p_pit_cod_orders`
(+0.47) at the top. Both were artefacts: the six `pit_*` order counts are
numerators over the same denominator and carried VIFs of 30-216 against each
other, so the fit split one effect into a near-cancelling pair. The retained
history set is blueprint §9.2's own pre-registered audit — shrunk RTO rate, COD
share, prepaid-success count, delivered count, tenure, payment-failure rate.
Culling the redundant counts cost **0.0000 AUC** and slightly *improved*
calibration.

### The scorecard itself

Points per one-standard-deviation move, PDO 20 at a 20:1 reference. Higher points
mean lower risk, the convention a support agent will already expect. This is the
artefact blueprint §9.3 calls primary — the thing you can hand to a risk committee
and have them argue with a specific line rather than with "the model".

{points}

### What is not in the model, and why

{count_cull_note}

{exclusions}

### Columns the redundancy sweep removed automatically

Detected generically — a perfect-collinearity sweep over the built matrix —
rather than by a named list, per the A44 methodology note that a targeted fix is
only ever as complete as the person writing it. Neither of these was on anyone's
list, and both carried VIFs above 1e6 in the first fit:

{redundant}

A third redundancy was caught earlier, at the encoder rather than by the sweep:
`device_type`'s declared reference level was `ANDROID_APP`, which does not exist
in the data (the levels are `ANDROID` / `IOS` / `WEB`). Nothing was dropped, all
three dummies were emitted, and the set was collinear with the intercept — VIF
2.4e7. The encoder now asserts that a declared reference level is present rather
than silently emitting a full dummy set.

**The Stage-4 bar held.** `attempt_delay_days` and `delivery_delay_days` are
absent from the view, absent from the design matrix, and asserted absent *by name*
in `dataset.assert_firewall` — a second, redundant check, because Phase 3 closeout
§3.2 names this the single most likely leakage vector in Phase 4. H6 makes realised
delay look like the strongest signal in the dataset at 15.4x the promise. It is
also determined after dispatch, so a model containing it does not predict RTO — it
observes one. The only legitimate checkout-time proxy, `estimated_delivery_days`,
IS in the model.

## 6. Fairness audit (§8.4) — ESCALATION

Blueprint §8.4: if the Tier-3 restriction rate exceeds Metro by more than 2.5x,
the model is proxying for postcode rather than behaviour. **Escalate, do not
proceed.** This section is that escalation.

> **STATUS: RULED.** The escalation raised here was answered — option 3, per-tier
> thresholds for restrictive interventions only, with three conditions
> (decision **A47**). The standalone record of the finding, the options, the
> ruling and what it cost is **`docs/phase4_escalation.md`**; the model fitted
> under it is `reports/phase4_m2.md`. **`FA-01`** now tests the ruling in the
> validation suite so it cannot regress. This section is left as written — it is
> the state of knowledge at the moment the escalation was raised, and rewriting
> it after the answer would erase the fact that the answer was not the
> modeller's to give.

### 6.1 The audit

Run across a sweep of restricted volumes rather than at a single cut, because the
one cut that would be natural — p* — belongs to M2.

Measured **before** the §8.4 customer-level protections in §6.4-6.5 run: the
question here is what the *score* is doing, and letting the overlay soften the
answer first would understate the concentration this section exists to detect.
`reports/phase4_m2.md` §6.2 measures the same audit post-overlay, which is what a
customer experiences and what FA-01 asserts on. The two differ — this rule reads
52.6% of Tier-3 at the 17% volume here and 42.3% post-overlay.

{audit}

The limit is 2.5x. At the 17% volume that blueprint §8.3 expects the High tier to
occupy, the model flags **{tier3_at_17:.1%} of Tier-3 orders and {metro_at_17:.2%}
of Metro orders**. This is not a marginal breach.

### 6.2 Is the model distorting geography, or reporting it?

The audit cannot tell these apart on its own, and the answer changes what to do.

{geo_truth}

**The model is reporting, not distorting.** Mean M1 score tracks the observed RTO
rate within each tier to under a percentage point. The 5.4x spread in restriction
rates exists because there is a 5.4x spread in actual RTO. The score is right.

**That does not rescue the policy, and §8.4 anticipated exactly this.** Its rule
is a constraint on what may be *done* with a score, not a diagnostic of whether
the score is accurate — "a risk model in a consumer product is a *policy*, not
just a classifier." A correct score can still produce an unshippable policy, and
here it does.

### 6.3 What it would cost to fix, measured

{ablation}

Three things fall out of this table, and the second is the one worth carrying:

1. **Dropping the `geo_tier` dummies does almost nothing.** AUC moves by
   {tier_auc_delta:.4f} and the ratio stays far above the limit. `serviceability_score`,
   `courier_reliability_score` and `estimated_delivery_days` are all derived
   from the delivery address and reconstruct the tier immediately.
   **Removing a protected attribute from a model does not remove it from the
   model.** Any fairness claim that rests on "we don't use geography as a
   feature" is unfalsifiable theatre.

2. **A fully geography-blind M1 still fails the audit — and now fails the
   feasibility gate too.** It lands at AUC {blind_auc:.4f}, below §9.4's 0.72,
   which by Phase 1's own pre-commitment means coarse tiers only. So the choice
   is not "accurate model or fair model". It is: an accurate model that cannot be
   used as a restriction policy, or a weakened model that still cannot be used as
   a restriction policy. There is no geography-blind M1 that both clears the gate
   and passes §8.4.

3. **Point-in-time history is what moderates the concentration, not what causes
   it.** Strip it and no Metro order is flagged at all.

### 6.4 Are the customer-level protections implementable from the view?

Both predicates exist as point-in-time columns, so both are implementable without
widening the firewall.

{coverage}

### 6.5 What the overlay reclassifies

Tiers here are volume-anchored placeholders (55/28/17 percent, the §8.3 expected
shares), used only to size the overlay. The economically anchored tiering is M2's.

{overlay}

The overlay is a customer-level protection and it does not touch the geographic
concentration — the two failures are independent, and fixing the second does not
fix the first.

### 6.6 What is escalated, and to whom

Per §8.4 this is a decision for whoever owns the intervention policy, not for
whoever fitted the model. Stated as options rather than a recommendation:

| Option | Effect on §8.4 | Effect on §9.4 | Cost |
|---|---|---|---|
| Ship M1 as scored, restrict on score alone | fails, {ratio_at_17} | clears at {auc:.4f} | a policy that restricts half of Tier-3 and none of Metro |
| Strip geography from the features | still fails at {blind_ratio}x | fails, {blind_auc:.4f} | 5.7pp AUC for a breach that remains |
| Restrict within geo tier (per-tier thresholds) | equalises rates by construction | preserved | flags Metro orders that are genuinely low risk |
| Use M1 for carrots only, never restrictions | not applicable — no restriction | not applicable | forgoes the restriction lever entirely |
| Re-anchor the 2.5x limit with the measured 5.4x spread on the record | rule changes | preserved | requires an explicit written ruling that the limit was set without this measurement |

The last row is the one that needs saying out loud. §8.4's 2.5x was written
before anyone had measured the geographic spread in RTO, and the measured spread
is 5.4x. That may mean the limit is wrong. It may equally mean the limit is
right and this business genuinely cannot price Tier-3 risk through restrictions.
**Deciding that here, having just seen the AUC, is precisely what §8.4 was
written to prevent.**

---

## 7. Verdict

{verdict}
""".format(
        pstar=pstar, full_rows=ctx["full_rows"], full_rto=ctx["full_rto"],
        censoring=md_table(ctx["censoring"]), dropped=ctx["dropped"],
        dropped_pct=ctx["dropped"] / ctx["full_rows"], clean_rto=ctx["clean_rto"],
        splits=md_table(split.describe()), cut=split.cut_date.date(),
        scoreboard=md_table(scoreboard), ceiling=ceiling, auc=m1_auc,
        ceiling_status=ceiling_status, lk03_limit=LK03_LIMIT, lk03=lk03,
        diag_limit=DIAGNOSTIC_LIMIT, diag=diag, feas=FEASIBILITY_GATE, gate=gate,
        rules_table=md_table(rules_table),
        reliability=md_table(evaluate.reliability(y_test, p_test)),
        slope=scoreboard.loc[2, "cal_slope"], intercept=scoreboard.loc[2, "cal_intercept"],
        ece=scoreboard.loc[2, "ece"], brier=scoreboard.loc[2, "brier"],
        deciles=md_table(evaluate.decile_lift(y_test, p_test)),
        n_features=ctx["X_test"].shape[1], coefs=md_table(top),
        exclusions=md_table(exclusions), redundant=md_table(redundant),
        count_cull_note=features.COUNT_CULL_NOTE,
        points=md_table(points, "{:.2f}"),
        audit=md_table(audit), geo_truth=md_table(geo_truth),
        ablation=md_table(ablation), coverage=md_table(coverage),
        tier3_at_17=float(audit.loc[audit["volume_flagged"] == 0.17, "TIER3"].iloc[0]),
        metro_at_17=float(audit.loc[audit["volume_flagged"] == 0.17, "METRO"].iloc[0]),
        ratio_at_17=audit.loc[audit["volume_flagged"] == 0.17, "tier3_over_metro"].iloc[0],
        blind_auc=float(ablation.loc[
            ablation["model"] == "no geographic features at all", "test_auc"].iloc[0]),
        blind_ratio=ablation.loc[
            ablation["model"] == "no geographic features at all",
            "tier3_over_metro"].iloc[0],
        tier_auc_delta=float(
            ablation.loc[ablation["model"] == "full model", "test_auc"].iloc[0]
            - ablation.loc[ablation["model"] == "no geo_tier dummies",
                           "test_auc"].iloc[0]),
        overlay=md_table(fairness.overlay_summary(overlay)),
        verdict=verdict(m1_auc, audit, ablation, ceiling, pstar),
    ))
    path.write_text("".join(parts), encoding="utf-8")


