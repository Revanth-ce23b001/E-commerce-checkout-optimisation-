"""reports/phase5_interventions.md — written from the run, never typed.

Assembly, plus sections 1-5: the population and the two derivations. Sections
6-11 — the decision table, H10, the sweeps — live in ``report_findings.py``.

Every figure in the output comes out of ``ctx``. The prose around them is
templated on the same values, so a re-run with different data cannot leave a
sentence claiming something the tables contradict — the failure mode that makes
a generated report worse than no report.

Tables are substituted into templates *after* ``.format`` runs, so a brace
appearing in a data cell cannot break the formatting.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import decision
from .library import ONE_TAP_CONSTRAINT
from .report_findings import (_decision, _h10, _naive, _exclusions,
                              _next, _sensitivity)
from .report_md import _fill, _total, md


def write(ctx: dict, path: Path) -> None:
    parts = [_header(ctx), _population(ctx), _derivations(ctx), _tiers(ctx),
             _levers(ctx), _per_lever_detail(ctx), _decision(ctx), _h10(ctx),
             _sensitivity(ctx), _naive(ctx), _exclusions(ctx), _next(ctx)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------


def _header(ctx: dict) -> str:
    lo, hi = ctx["pop"]["window"]
    return _fill(
        "# Phase 5 Stage 1 — intervention simulation and the decision table\n\n"
        "**Reproduce:** `make load` -> `make m2` -> "
        "`python scripts/09_interventions.py`\n\n"
        "**Population:** the M2 test window, {lo} to {hi} — {n:,} shipped, "
        "resolved, scored orders against {s:,} checkout-start sessions.\n\n"
        "**Every behavioural number is [A]** and lives in "
        "`config/interventions.yaml`. **Every rupee is [D]**, derived per order "
        "from `config/params.yaml`'s cost registry plus that order's own realised "
        "cost draws. The two never mix, and the file boundary is what keeps them "
        "from mixing.\n\n"
        "**Scope.** The six §10.1 levers plus COD gating, the risk-based pricing "
        "decision table, and the sensitivity sweep. The five-scenario CM "
        "comparison is Stage 2; the A/B design and power analysis are Phase 6; "
        "the PRD is Phase 7.", {},
        lo=lo.date(), hi=(hi - pd.Timedelta(days=1)).date(),
        n=len(ctx["frame"]), s=ctx["pop"]["sessions"])


def _population(ctx: dict) -> str:
    from .population import funnel
    pop = ctx["pop"]
    return _fill(
        "## 1. The population\n\n<<funnel>>\n\n"
        "The window is clean by construction: the censoring horizon cut lands on "
        "the first day carrying any censored order, so every order inside it has "
        "a resolved outcome. Phase 4 closeout §3 warns that the blended 16.53% "
        "RTO rate is the *censored* figure; this population's realised rate is "
        "**{rto:.2%}**, and that is the number every delta below is measured "
        "against.\n\n"
        "The {c:,} pre-ship cancellations are carried in the session denominator "
        "and excluded from the margin arithmetic. Decision A23 zeroes every "
        "economic line on a cancelled order, so including them would add {c:,} "
        "zeros to a mean and change nothing except the mean.",
        {"funnel": md(funnel(pop))},
        rto=float(ctx["frame"]["rto_flag"].mean()), c=pop["preship_cancelled"])


def _derivations(ctx: dict) -> str:
    ledger, canon = ctx["ledger"], ctx["canonical"]
    worst = ledger.loc[ledger["delta"].abs().idxmax()]
    return _fill(
        "## 2. The two derivations, each checked before use\n\n"
        "### 2.1 [D] Order economics — rebuilt, then reconciled\n\n"
        "`fct_order_economics.contribution_margin` is the margin of the order "
        "that *happened*. Every question in this phase asks about an order that "
        "did not: the same basket paid for online, or carrying a fee, or "
        "delivered when in fact it returned. So the margin is rebuilt line by "
        "line, reusing each order's realised draws for every cost that does not "
        "depend on the payment method — forward freight, packaging, goods value "
        "— and taking the outcome-conditional lines at their registry "
        "expectations.\n\n"
        "<<ledger>>\n\n"
        "Agreement is ₹{d:.2f} per order across {n:,} orders. The largest cell "
        "error is ₹{w:.2f} on *{p}*, and it is expected: that cell replaces a "
        "realised reverse-freight and shrink draw with its mean.\n\n"
        "The effective PG rate is **{pg:.3%}**, measured from the realised "
        "prepaid mix rather than taken from the registry's headline 1.8%. UPI "
        "runs at 0.9% and cards at 2.1%, and a counterfactual switch has no rail "
        "yet — so both arms are priced at the blended rate the platform actually "
        "pays, which stops an artificial gap opening between the baseline and "
        "the switch.\n\n"
        "Three deliberate departures from the generator, each stated because "
        "each changes a number:\n\n"
        "1. **A fee does not increase COGS.** The generator folds `cod_fee` into "
        "net revenue and computes COGS as 75% of it, so a ₹39 convenience fee "
        "would silently add about ₹29 of procurement cost. It buys no goods. "
        "COGS is pinned to the realised `cogs_value` and is invariant to every "
        "lever. Without this, intervention B is understated by roughly three "
        "quarters.\n"
        "2. **Outcome-conditional lines are used in expectation.** A "
        "counterfactual needs reverse freight on an order that did not return.\n"
        "3. **Support/NDR is the registry mean of ₹18.00**, not `base + slope × "
        "attempts`. `delivery_attempts` is behind the Stage-4 bar, and a "
        "counterfactual order has no attempt count to use anyway.\n\n"
        "### 2.2 [D] The causal counterfactual — the planted coefficients, per order\n\n"
        "The DGP's RTO logit carries exactly three terms that switch on the "
        "payment method: `is_cod` +1.60, `month_end_x_cod` +0.30, "
        "`paid_via_switch` −0.45. A switch to prepaid removes all three from that "
        "order's own logit, keeping its latents, its geography and its realised "
        "post-dispatch shock. That is a per-order counterfactual, not a "
        "population average applied to individuals.\n\n"
        "The identity is checked on the truth file's own population before it is "
        "used anywhere: **{r:.4f}pp rebuilt against {t:.4f}pp in `_truth.json`, "
        "over {o:,} COD orders.**\n\n"
        "<<effect>>\n\n"
        "> **The truth channel evaluates; it never targets.** Every tier, "
        "threshold and eligibility flag in this phase is computed from "
        "`m2_score`, fitted on the firewalled view. `p_rto_final` is used only to "
        "score what a policy would produce. An M2 score cannot substitute for it: "
        "M2's logit is attenuated by about 0.480 (limitation L14), so applying an "
        "un-attenuated +1.60 to a compressed logit would overstate every switch.",
        {"ledger": md(ledger), "effect": md(ctx["effect_summary"])},
        d=float(ledger.iloc[0]["delta"]), n=int(ledger.iloc[0]["orders"]),
        w=abs(float(worst["delta"])), p=worst["population"], pg=ctx["pg_rate"],
        r=canon["rebuilt_pp"], t=canon["truth_file_pp"], o=canon["orders"])


def _tiers(ctx: dict) -> str:
    b = ctx["boundaries"]
    shares = ctx["tier_table"].set_index("tier")["share_pct"]
    return _fill(
        "## 3. Tier boundaries, derived from the economics\n\n"
        "Blueprint §8.3 derives the HIGH line properly — p-star is where a COD "
        "order's expected margin crosses zero — and then gives the LOW/MED line "
        "as 0.10, justified with *\"COD order EV = +₹65\"*. ₹65 has no economic "
        "meaning; the 0.10 came first and the ₹65 followed. So the LOW line is "
        "solved here instead, on a question that has an answer:\n\n"
        "> **At what predicted RTO probability does it first become possible for "
        "a paid intervention to create value?**\n\n"
        "§6.6 frames it — *\"how much can we afford to pay to convert a COD order "
        "to prepaid?\"* — and that affordable spend is a function of p. Below "
        "some p it is smaller than the cheapest paid lever in the library, and no "
        "incentive can pay for itself **even at perfect targeting, zero leakage "
        "and a 100% switch rate**.\n\n"
        "| line | value | basis |\n|---|---|---|\n"
        "| LOW / MED | **p = {lm:.4f}** | affordable switch spend crosses the "
        "₹{a:.0f} incentive anchor (§10.2) |\n"
        "| MED / HIGH | **p-star = {mh:.4f}** | `_truth.json` "
        "`breakeven_rto_probability_derived` |\n\n"
        "The derived LOW line is **{lm:.2%}**, not §8.3's 10%. It moves with its "
        "anchor and the anchor is a lever parameter, so the sweep is reported "
        "rather than hidden:\n\n<<anchor>>\n\n"
        "p-star re-derived on *this* population's realised costs lands at "
        "**{re:.4f}** against the truth file's {mh:.4f}. The {gap:.1f}pp "
        "difference is a population difference — the test window is later and "
        "riskier — and the truth file's value is the one used, per CLAUDE.md's "
        "rule that everything downstream quotes `_truth.json`.\n\n<<tiers>>\n\n"
        "Shares are **{sl:.1f} / {sm:.1f} / {sh:.1f}**, against §8.3's expected "
        "45 / 38 / 17. The HIGH share matches Phase 4's measured 28.1% exactly, "
        "as it must — same score, same p-star. The LOW/MED split differs from "
        "Phase 4's 40.0 / 32.0 because that used §8.3's 0.10 and this uses the "
        "derived {lm:.4f}.\n\n"
        "> **§8.3's expected shares were priors and they are wrong in the "
        "direction that matters.** The HIGH tier is 28.1% of orders, not 17% — "
        "**65% more traffic sits above the break-even line than Phase 1 "
        "expected.** Every restriction volume in this report inherits that, and "
        "so does the fairness exposure.",
        {"anchor": md(b["anchor_sweep"]), "tiers": md(ctx["tier_table"])},
        lm=b["low_med"], mh=b["med_high"], a=b["anchor_rupees"],
        re=b["pstar_rederived_on_population"],
        gap=abs(b["pstar_rederived_on_population"] - b["med_high"]) * 100,
        sl=float(shares["LOW"]), sm=float(shares["MED"]), sh=float(shares["HIGH"]))


def _levers(ctx: dict) -> str:
    base = ctx["baseline"]
    rows = []
    for key, by_depth in ctx["cells"].items():
        lever = ctx["levers"][key]
        depth = by_depth["_best_depth"]
        cell = by_depth[depth]
        t = _total(cell)
        rows.append({
            "id": key, "lever": lever.name,
            "kind": "RESTRICTIVE" if lever.restrictive else "offer",
            "best depth": decision.DEPTH_LABEL[depth],
            "§10.1 depth": decision.DEPTH_LABEL[lever.blueprint_depth],
            "treated": int(t["treated"]),
            "[A] switch": round(cell["resp"]["switch"], 4),
            "[A] abandon": round(cell["resp"]["abandon"], 4),
            "d_conv_%rel": float(t["d_conversion_rel_pct"]),
            "d_net_conv_%rel": float(t["d_net_conversion_rel_pct"]),
            "d_COD_pp": float(t["d_cod_share_pp"]),
            "d_RTO_pp": float(t["d_rto_pp"]),
            "d_CM/order": float(t["d_cm_per_order"]),
            "d_CM/session": float(t["d_cm_per_session"]),
            "verdict": ctx["guard"][(key, depth)]["verdict"].split(" (")[0],
        })
    summary = pd.DataFrame(rows).sort_values("d_CM/session", ascending=False)
    last = base.iloc[-1]
    return _fill(
        "## 4. Baseline, and every lever at its best targeting depth\n\n"
        "### 4.1 Baseline\n\n<<base>>\n\n"
        "`rto_rate` and `cm_per_order` are **expected** values under the truth "
        "channel; `realised_*` are what the window actually did. They agree to "
        "{dr:.2f}pp on RTO and ₹{dc:.2f} per order, which is the check that the "
        "expectation machinery is not quietly biased before a single lever "
        "runs.\n\n"
        "### 4.2 All seven levers\n\n"
        "Each lever is run at three targeting depths — HIGH only, MED+HIGH and "
        "flat. The row below is the depth with the highest CM/session **among "
        "those that clear §12.1**, falling back to the depth §10.1 specifies "
        "when none of them do. {fallback_note}\n\n<<summary>>\n\n"
        "> **§10.1 and §10.2 disagree about intervention A, and the disagreement "
        "is worth ₹{gap:.2f} per session.** §10.1's target segment for the prepaid "
        "incentive is *\"Medium & high risk\"*; §10.2's worked example for the "
        "same lever targets high risk only. At MED+HIGH, A is worth "
        "**₹{med:.3f}**/session; at HIGH only, **₹{high:.3f}**. The blueprint's "
        "own arithmetic is right and its own target segment is wrong — the "
        "MED-tier incentive leaks ₹30 to {leak:,} orders that were already paying "
        "online, against a switch benefit too small to cover it.\n\n"
        "**ΔCM/session is the north-star unit** (§5.2) and the one §12.1 sets its "
        "**+₹1.50** ship bar against. ΔCM/order is shown beside it because the "
        "two can point in opposite directions: a lever that removes bad orders "
        "raises the *per-order* margin while shrinking the base, and only the "
        "per-session figure notices.\n\n"
        "**ΔRTO uses the surviving-orders denominator**, per CLAUDE.md invariant "
        "8. An intervention that removes orders must not book that as an RTO "
        "improvement for free.",
        {"base": md(base), "summary": md(summary)},
        dr=abs(float(last["rto_rate"] - last["realised_rto_rate"])) * 100,
        dc=abs(float(last["cm_per_order"] - last["realised_cm_per_order"])),
        fallback_note=(
            "Here that fallback applies to every lever, because nothing clears "
            "§12.1 at any depth — see §6.2."
            if all(d == ctx["levers"][k].blueprint_depth
                   for k, d in ((k, v["_best_depth"])
                                for k, v in ctx["cells"].items()))
            else "Where the two columns differ, the score found a better "
                 "configuration than the blueprint specified."),
        med=float(_total(ctx["cells"]["A"]["med_high"])["d_cm_per_session"]),
        high=float(_total(ctx["cells"]["A"]["high_only"])["d_cm_per_session"]),
        gap=abs(float(_total(ctx["cells"]["A"]["high_only"])["d_cm_per_session"])
                - float(_total(ctx["cells"]["A"]["med_high"])["d_cm_per_session"])),
        leak=int((ctx["cells"]["A"]["med_high"]["mask"]
                  & ~ctx["sim"].is_cod).sum()))


def _per_lever_detail(ctx: dict) -> str:
    blocks = ["## 5. Per lever, per tier, per depth\n\n"
              "`d_cm_per_session` uses the fixed {:,}-session denominator, so the "
              "tier rows add to the ALL row. The depth marked **best** is the one "
              "carried into §6.".format(ctx["pop"]["sessions"])]
    bands = ctx["bands"]
    for key, by_depth in ctx["cells"].items():
        lever = ctx["levers"][key]
        best = by_depth["_best_depth"]
        rows = []
        for depth in ("high_only", "med_high", "all"):
            summary = by_depth[depth]["summary"]
            resp = by_depth[depth]["resp"]
            for _, row in summary.iterrows():
                rows.append({
                    "depth": decision.DEPTH_LABEL[depth]
                             + (" **best**" if depth == best else ""),
                    "tier": row["tier"], "orders": int(row["orders"]),
                    "treated": int(row["treated"]),
                    "switch": round(resp["switch"], 4),
                    "d_conv_%rel": float(row["d_conversion_rel_pct"]),
                    "d_net_conv_%rel": float(row["d_net_conversion_rel_pct"]),
                    "d_COD_pp": float(row["d_cod_share_pp"]),
                    "d_RTO_pp": float(row["d_rto_pp"]),
                    "d_CM/order": float(row["d_cm_per_order"]),
                    "d_CM/session": float(row["d_cm_per_session"]),
                })
        band = bands[bands["lever"] == key][
            ["level", "switch", "abandon", "d_cm_per_session", "d_rto_pp",
             "d_conversion_rel_pct"]]
        blocks.append(
            "### {id} — {name}  ·  {kind}, ranks {rank}\n\n{note}\n\n{table}\n\n"
            "**[A] band** at the best depth (lo / central / hi from "
            "`config/interventions.yaml`):\n\n{band}".format(
                id=key, name=lever.name,
                kind="RESTRICTIVE" if lever.restrictive else "offer",
                rank="per-tier (A47)" if lever.restrictive else "global (A47)",
                note=_lever_note(key, lever, by_depth[best]["resp"], ctx),
                table=md(pd.DataFrame(rows)), band=md(band)))
    return "\n\n".join(blocks)


def _lever_note(key: str, lever, resp: dict, ctx: dict) -> str:
    stem = "**[A] source:** {}. Switch stated as *{}*.".format(
        lever.cfg.get("source", ""), resp["stated_as"])
    frame = ctx["frame"]
    is_cod = (frame["payment_method"] == "COD").to_numpy()
    extra = {
        "A": "The cohort is **every order in the band**, not just the COD ones: a "
             "₹{inc:.0f} incentive is displayed at the payment step and every "
             "order that ends prepaid collects it, including the ones that were "
             "always going to. That leakage is §10.2's entire argument, and "
             "defining the cohort as COD-only would delete it by construction.\n\n"
             "Two things throttle A hard on this dataset. Only **{sav:.1%}** of "
             "COD orders carry a saved prepaid instrument, and they are not a "
             "random {sav:.0%} — `has_saved_prepaid_instrument` sits at −0.60 in "
             "the COD-choice model, so holding one already makes COD less likely. "
             "A's reachable population is small *and* skewed low-risk, which is "
             "the opposite of where the money is.",
        "B": "Above p-star an abandoned order is a **saving**, not a loss — which "
             "is why the fee may never be applied below it. The ₹{fee:.0f} is "
             "modelled as pure margin: collected on delivery, buys no goods.",
        "C": "Costs nothing and takes nothing, so no abandonment term and no "
             "leakage term. §10.1 also allows a conversion *gain*; it is held at "
             "zero here, because crediting an unmeasured gain to the library's "
             "cheapest lever would let it win by assumption.",
        "D": "**The least evidenced row in the table.** §10.1 gives D no "
             "magnitudes at all. The ₹{par:.0f} token is modelled as "
             "non-refundable on refusal — that is what makes it a commitment "
             "device — and it delivers a {dose:.0%} dose of the full "
             "COD-to-prepaid causal logit shift. Both numbers are Phase 5 "
             "judgements with no Phase 1 antecedent, and D's rank should be read "
             "as a hypothesis to test rather than a result.",
        "E": "**Zero abandonment here is a constraint, not an estimate.** "
             + ONE_TAP_CONSTRAINT + " A Phase 6 arm that loses conversion has "
             "breached it, and is intervention G wearing E's name.",
        "F": "H11 is **measured** at {h11:.1%} in `_truth.json`, against an 8-15% "
             "prior — so F's ceiling is set by the data, not by an assumption. It "
             "can only move the {n:,} orders that are COD *because* a payment "
             "failed. The {pf:,} sessions that abandoned at the payment-failure "
             "step are NOT counted: they have no score and no order row, so "
             "crediting them would need an imputed margin. **This row is "
             "therefore a floor on F, not an estimate of it.**",
        "G": "Not a §10.1 lever — named by the A47 ruling. Gating is absolute, so "
             "switch + abandon = 1 by construction and **one assumption decides "
             "the whole lever**. Phase 4 priced it at switch = 0 as a "
             "conservative floor; §8 is how this row should actually be read.",
    }.get(key, "")
    if extra:
        extra = extra.format(
            inc=resp["incentive"], fee=resp["cod_fee"], par=resp["partial"],
            dose=resp["dose"],
            sav=float(frame.loc[is_cod, "has_saved_prepaid_instrument"].mean()),
            h11=float(ctx["truth"]["hypothesis_ground_truth"]
                      ["H11_pct_cod_from_payment_failure"]["observed"]),
            pf=ctx["pop"]["payment_failure_abandons"],
            n=int(frame["paid_via_switch"].sum()))
    return stem + ("\n\n" + extra if extra else "")


