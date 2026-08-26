"""Phase 5 report, sections 6-11 — the decision table and everything after it.

Split from ``report.py`` on the seam that matters: everything before §6 describes
the *population and the derivations*, and everything from §6 on is a *finding*.
A reader auditing a number goes to one file; a reader auditing a claim goes to
the other.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import counterfactual, decision, sensitivity
from .library import ONE_TAP_CONSTRAINT
from .report_md import _fill, _total, md


def _decision(ctx: dict) -> str:
    table = ctx["table"]
    best = table[table["is_best_depth"] & (table["tier"] != "ALL")][
        ["tier", "lever", "name", "depth_label", "treated",
         "d_conversion_rel_pct", "d_net_conversion_rel_pct", "d_cod_share_pp",
         "d_rto_pp", "d_cm_per_order", "d_cm_per_session"]].sort_values(
        ["tier", "d_cm_per_session"], ascending=[True, False])
    guard = pd.DataFrame([
        {"lever": k, "depth": decision.DEPTH_LABEL[d],
         "ΔCM/session": round(v["cm_per_session"], 3),
         "agg checkout %rel": round(v["aggregate_conversion_rel_pct"], 3),
         "agg NET %rel": round(v["aggregate_net_conversion_rel_pct"], 3),
         "LOW checkout %rel": round(v["low_tier_conversion_rel_pct"], 3),
         "LOW NET %rel": round(v["low_tier_net_conversion_rel_pct"], 3),
         "treated ΔRTO pp": round(v["treated_rto_pp"], 2),
         "verdict": v["verdict"]}
        for (k, d), v in ctx["guard"].items()])
    return _fill(
        "## 6. The decision table\n\n"
        "Risk tier × intervention, at each lever's best targeting depth. Tiers "
        "are the derived economic lines from §3, never percentiles. A cell is "
        "what happens **in that tier** when the lever is deployed — which "
        "includes tiers the lever was not aimed at, and those are the rows that "
        "decide whether it ships.\n\n<<best>>\n\n"
        "### 6.1 §12.1 guardrails — three of six are evaluable\n\n<<guard>>\n\n"
        "Floors: ΔCM/session ≥ **+₹{cm:.2f}** · aggregate conversion ≥ "
        "**{agg:+.1f}% rel** · **LOW-tier conversion ≥ {low:+.1f}% rel** · "
        "treated-tier ΔRTO ≤ **{rto:+.1f}pp**.\n\n"
        "> **Three clauses cannot be evaluated from a simulation and are not "
        "quietly passed:** {un}. A verdict with half its clauses unevaluated is a "
        "shortlist, not a launch decision — which is why the passing verdict "
        "reads SHORTLIST and never LAUNCH.\n\n"
        "The LOW-tier clause is checked **first and alone**, because §12.2 makes "
        "it a KILL *regardless of CM*. Reading it in sequence with the others "
        "would let a large CM gain argue against it, which is what "
        "pre-committing it in Phase 1 was meant to prevent.\n\n"
        "### 6.2 Recommended action per tier — at the depths §10.1 specifies\n\n"
        "<<rec>>\n\n"
        "**Nothing in the library ships as specified.** That is not a marginal "
        "result: {nsh} of {ntot} lever-and-depth configurations clear §12.1, and "
        "the two failure modes are cleanly separated by lever kind. Every stick "
        "breaches a conversion floor; every carrot falls short of the +₹1.50 CM "
        "bar. §6.4 asks the useful question instead — not *does this "
        "configuration pass* but *what configuration would*.\n\n"
        "### 6.3 FA-01 re-measured on every restrictive lever and depth\n\n"
        "<<fair>>\n\n"
        "Re-measured rather than inherited: the eligible volume here is set by "
        "the **derived** risk bands, not by Phase 4's 17%, and a ruling that "
        "holds at one volume and not another has not been enforced. Worst "
        "measured ratio **{worst:.2f}x** against the {limit}x limit.",
        {"best": md(best), "guard": md(guard.sort_values(
            ["lever", "depth"])), "rec": md(ctx["recommendation"]),
         "fair": md(ctx["fairness"])},
        cm=decision.CM_PER_SESSION_FLOOR,
        agg=decision.AGGREGATE_CONVERSION_FLOOR_REL,
        low=decision.LOW_TIER_CONVERSION_FLOOR_REL,
        rto=decision.RTO_IMPROVEMENT_FLOOR_PP,
        un="; ".join(decision.UNTESTABLE),
        worst=float(ctx["fairness"]["worst_over_best"].max()),
        limit=float(ctx["fairness"]["limit"].iloc[0]),
        nsh=sum(1 for v in ctx["guard"].values() if v["shippable"]),
        ntot=len(ctx["guard"])) + "\n\n" + _feasible(ctx)


def _feasible(ctx: dict) -> str:
    """§6.4 — the configuration each lever would have to run at to ship."""
    feas = ctx["feasible"]
    sweep = feas["volumes"].get("B")
    window, show = "no volume", None
    if sweep is not None and len(sweep):
        ok = sweep[sweep["shippable"]]
        if len(ok):
            window = "{:.0%} and {:.0%}".format(ok["volume"].min(),
                                                ok["volume"].max())
        show = sweep[sweep["volume"].isin(
            [0.05, 0.08, 0.10, 0.13, 0.17, 0.18, 0.25, 0.28])][
            ["volume", "treated", "d_cm_per_session", "agg_checkout_rel_pct",
             "agg_net_rel_pct", "treated_rto_pp", "shippable"]]
    switches = pd.DataFrame([
        {"lever": v["lever"], "§10.1 prior switch": v["prior_switch"],
         "switch needed for ₹1.50": (v["required_switch"] if v["reachable"]
                                     else float("nan")),
         "multiple of prior": (v["multiple_of_prior"] if v["reachable"] else "-"),
         "max ΔCM/session at a 100% switch": v["max_cm_at_switch_1.0"],
         "reachable": v["reachable"]}
        for v in feas["switches"].values()])
    return _fill(
        "### 6.4 What would have to be true — the shippable configuration\n\n"
        "Two knobs, one per kind of lever, and **neither is a behavioural "
        "assumption**. The [A] band stays exactly where "
        "`config/interventions.yaml` puts it; what moves is the policy.\n\n"
        "**Restrictions have a volume.** A47 ruled on *which* orders a "
        "restriction picks and said nothing about how many. Volume is a free "
        "policy parameter, and the aggregate conversion floor caps it. Sweeping "
        "it for the COD fee — holding A47's per-tier selection and the §8.4 "
        "overlay fixed throughout, so this varies how much restriction and never "
        "how it is chosen:\n\n<<b>>\n\n"
        "**The COD fee ships at a restricted volume between {window}.** Below "
        "the floor of that window it does not clear the +₹1.50 bar; above the "
        "top of it, the aggregate checkout-conversion floor breaks. That window "
        "is the most actionable number this phase produces, and it is "
        "**derived**: the abandonment rate is §10.1's prior, the selection rule "
        "is A47's ruling, and the cap falls out of the two.\n\n"
        "> The top of the window lands on **17%**, the volume §8.3 pre-committed "
        "the HIGH tier to before anyone had a model. **That is a coincidence and "
        "should be presented as one.** §8.3 set 17% from an expected tier share "
        "that turned out to be wrong — the HIGH tier is 28.1% — and this 17% "
        "comes from a conversion floor. Two unrelated routes to the same number "
        "is worth noticing and is not worth treating as confirmation.\n\n"
        "**Offers have a required switch rate.** They cost conversion nothing, "
        "so the ship bar is what binds. Inverting it: what switch rate would "
        "each offer need to deliver ₹1.50 per session?\n\n<<sw>>\n\n"
        "{offer_verdict}\n\n"
        "The mechanism behind the unreachable ones is worth stating: an offer "
        "can only move COD orders onto the prepaid rail; the true causal value "
        "of doing so is about 10pp of RTO rather than the naive 17.7pp; and A "
        "additionally pays its incentive to every order that was already "
        "prepaid, which is why A gets *worse* as it is rolled out wider. The "
        "carrots are not badly designed — they are being asked to clear a bar "
        "§12.1 deliberately set at 3.5x its own break-even, with a mechanism the "
        "economics cap.\n\n"
        "### 6.5 Recommended action per tier — at shippable volumes\n\n<<rec>>\n\n"
        "{d_warning}\n\n"
        "This is the risk-based pricing answer and it is narrower than the "
        "intervention library implies: **one lever, at a volume well below what "
        "a naive reading of the tier shares would suggest.** Everything else is "
        "either an experiment worth running for the information or a build that "
        "cannot pay for itself at the effect sizes Phase 1 assumed.",
        {"b": md(show) if show is not None else "_no sweep_",
         "sw": md(switches), "rec": md(feas["recommendation"])},
        window=window,
        offer_verdict=_offer_verdict(switches),
        d_warning=_recommendation_warning(feas, ctx))


def _offer_verdict(switches: pd.DataFrame) -> str:
    """State what the switch table actually shows, whichever way it came out."""
    reachable = switches[switches["reachable"]]
    if reachable.empty:
        return ("**No offer in the library reaches the bar at any switch rate "
                "this dataset supports.**")
    names = ", ".join(
        "**{}** at {:.1%} ({}x its §10.1 prior)".format(
            r["lever"], r["switch needed for ₹1.50"], r["multiple of prior"])
        for _, r in reachable.iterrows())
    unreachable = switches[~switches["reachable"]]["lever"].tolist()
    tail = ""
    if unreachable:
        tail = (" **{}** cannot reach it at any switch rate, including 100%."
                .format(" and ".join(unreachable)))
    return ("**{n} of {t} offers can reach the ship bar, but only above its "
            "§10.1 prior:** {names}. That is a testable claim rather than a "
            "shippable configuration — it says what the experiment has to find, "
            "not what the lever will do.{tail}".format(
                n=len(reachable), t=len(switches), names=names, tail=tail))


def _recommendation_warning(feas: dict, ctx: dict) -> str:
    """Flag it loudly if the recommendation rests on a lever with no priors."""
    chosen = {str(r) for r in feas["recommendation"]["action"]
              if str(r) != "NO INTERVENTION"}
    weak = [k for k in ("D", "G") if any(k in c.split()[1] for c in chosen
                                         if len(c.split()) > 1)]
    if not weak:
        return ("Every lever named above carries a §10.1 magnitude prior, so the "
                "recommendation rests on numbers Phase 1 committed to in advance.")
    return ("> ⚠ **The recommendation lands on {}, which is the least evidenced "
            "lever in the library.** §10.1 gives partial payment no magnitudes at "
            "all — not a switch rate, not an abandonment rate, not an RTO effect "
            "— so its commitment dose and its token retention are Phase 5 "
            "judgements with no Phase 1 antecedent. It wins here *because* its "
            "assumed abandonment is the lowest of the sticks, which is exactly "
            "the parameter nobody has measured.\n>\n"
            "> **Do not read this row as a recommendation to build D.** Read it "
            "as: under assumptions this project invented, D would dominate — "
            "which makes measuring those assumptions the highest-value "
            "experiment in the programme, and makes B the lever to ship first "
            "because its numbers were committed to in advance."
            .format(", ".join(ctx["levers"][k].name for k in weak)))


def _h10(ctx: dict) -> str:
    rows, blocks = [], []
    for key, by_depth in ctx["cells"].items():
        best = by_depth["_best_depth"]
        t = float(_total(by_depth[best])["d_cm_per_session"])
        f = float(_total(by_depth["all"])["d_cm_per_session"])
        m = float(_total(by_depth["random_matched"])["d_cm_per_session"])
        degenerate = best == "all"
        rows.append({
            "id": key, "lever": ctx["levers"][key].name,
            "reported depth": decision.DEPTH_LABEL[best],
            "targeted": round(t, 3), "flat": round(f, 3),
            "random-matched": round(m, 3),
            "premium vs random": ("-" if degenerate else round(t - m, 3)),
            "premium as % of targeted": (
                "-" if degenerate or abs(t) < 1e-9
                else "{:.0f}%".format((t - m) / abs(t) * 100)),
            "beats flat": ("tie" if degenerate else ("yes" if t > f else "no")),
            "flat verdict": decision.guardrails(
                by_depth["all"]["summary"])["verdict"].split(" (")[0],
        })
    frame = pd.DataFrame(rows)
    contested = frame[frame["premium vs random"] != "-"]
    n = len(contested)
    beats_flat = int((contested["beats flat"] == "yes").sum())
    beats_random = int(
        sum(1 for v in contested["premium vs random"] if float(v) > 0))
    flat_ships = int(sum(
        1 for key, by_depth in ctx["cells"].items()
        if decision.guardrails(by_depth["all"]["summary"])["shippable"]))
    flat_kills = int(sum(
        1 for key, by_depth in ctx["cells"].items()
        if decision.guardrails(
            by_depth["all"]["summary"])["verdict"].startswith("KILL")))
    median_premium = (float(np.median([float(v) for v in
                                       contested["premium as % of targeted"]
                                       .str.rstrip("%")]))
                      if n else 0.0)
    blocks.append(_fill(
        "## 7. H10 — does risk-based beat one-size-fits-all?\n\n"
        "Blueprint H10 is the thesis of the case study, and §12.2 pre-commits to "
        "killing the risk engine if the effect exists only in the flat arm. The "
        "comparison therefore has to be genuine, and §10.2's version is not: it "
        "compares a flat rollout against a targeted one at **different volumes**, "
        "so it measures volume and selection together and credits both to "
        "targeting.\n\n"
        "Three arms, and the third is the one that answers the question.\n\n"
        "* **targeted** — the risk score, A47 per-tier ranking for sticks, the "
        "§8.4 overlay.\n"
        "* **flat** — same lever, same cash terms, every order it can act on, no "
        "score and no overlay. §10.2's comparator, and what a PM means by "
        "one-size-fits-all.\n"
        "* **random, volume-matched** — the *same number* of orders, chosen at "
        "random. **This isolates the risk engine.** Margin over this arm is "
        "targeting; margin over the flat arm may be nothing but a bigger "
        "intervention.\n\n<<frame>>", {"frame": md(frame)}))
    blocks.append(_fill(
        "**{deg} of the seven levers are reported at flat depth already** — C, E "
        "and F are shown to everyone by design — so for those three the three "
        "arms are the same arm and the comparison is degenerate rather than "
        "lost. H10 is contested on the remaining **{n}**: A, B, D and G.\n\n"
        "### 7.1 The finding\n\n"
        "Three results, and they do not all point the same way.\n\n"
        "**1. Targeting loses to a flat rollout on margin — on {lost} of the {n}.** "
        "A flat "
        "COD fee earns nearly three times the targeted one, because a ₹39 fee "
        "collected on a mid-risk COD order is close to pure margin against an "
        "order whose expected value is already positive, and §10.1's 7.5% "
        "abandonment does not cost enough to offset it. **This is not an "
        "artefact of a weak comparator** — the flat arm is the same lever, at the "
        "same price, differing only in that it does not consult the score.\n\n"
        "**2. Targeting beats a random cohort of the same size — {br} of {n} — "
        "but by less than the thesis implies.** Median premium over random "
        "selection at equal volume is **{med:.0f}%** of the targeted arm's own "
        "CM. For the COD fee specifically the premium is small, and the reason is "
        "structural: a ₹39 fee is profitable across most of the risk "
        "distribution, so choosing *which* orders to charge adds little when "
        "charging almost any of them works. **The risk engine earns most where "
        "the lever is expensive and least where the lever is cheap** — which is "
        "the opposite of where a fee sits.\n\n"
        "**3. What separates the targeted policy is not margin — it is the "
        "guardrails, and they were pre-committed in Phase 1.** {ships} of seven "
        "flat arms clear §12.1, and {kills} are outright KILLs on a conversion "
        "floor. So the honest statement of the result is weaker than H10 "
        "claims:\n\n"
        "> Risk-based targeting does not beat one-size-fits-all on contribution "
        "margin. It beats it on **margin per unit of harm to low-risk "
        "customers** — and the only reason that is the deciding criterion is "
        "that Phase 1 wrote the harm constraint down before anyone had a number "
        "to argue with.\n\n"
        "**A reader who rejects §12.1's conversion floors should read this table "
        "as §12.2's kill clause:** *effect exists in the flat arm too ⇒ ship "
        "flat, kill the risk engine.* The floors are what stand between this "
        "project and that conclusion, and the floors are an [A]. That is the "
        "most important sentence in this report and it is not the one the thesis "
        "wanted.\n\n"
        "**The number Phase 6 should be powered to detect is the premium over "
        "the random-matched arm** — same volume, same cash, no score. That is "
        "the risk engine's actual contribution. Powering on the "
        "flat-versus-targeted gap that §10.2 reports would measure the size of "
        "the intervention and call it the value of the model.",
        {}, br=beats_random, bf=beats_flat, n=n, ships=flat_ships,
        kills=flat_kills, deg=len(frame) - n, med=median_premium,
        lost=n - beats_flat))
    return "\n\n".join(blocks)


def _sensitivity(ctx: dict) -> str:
    sweeps = ctx["sweeps"]
    anchor = sweeps["dgp_fee_anchor"]
    blocks = [_fill(
        "## 8. Sensitivity — the two assumptions that drive everything\n\n"
        "Everything economic here is derived; everything behavioural is assumed. "
        "Two assumptions carry most of the variance: the **COD-to-prepaid switch "
        "rate**, which scales every carrot and *is* the mechanism of G, and the "
        "**abandonment rate under a fee**, which sets the sign of the stick "
        "levers.\n\n"
        "> **A second, independent anchor for the fee's conversion cost.** The "
        "DGP carries a planted `conversion_model.shipping_fee_charged_gt0 = "
        "−0.45` that never fired, because `params.yaml` sets "
        "`shipping_fee_charged: 0`. Applied to this window's conversion it "
        "implies an abandonment rate of **{a:.1%}** — far above §10.1's 5-10% "
        "prior. It is marked on the sweep, not substituted for the prior: it was "
        "planted for a *shipping* fee and nobody calibrated it against ₹39 of COD "
        "fee. If it is the better anchor, every fee result moves to the "
        "right-hand end of the grid.", {}, a=anchor)]
    for i, key in enumerate(("B", "G"), start=1):
        s = sweeps[key]
        lever = ctx["levers"][key]
        central = ctx["cells"][key][s["depth"]]["resp"]
        cross, crossm = s["crossover"], s["crossover_matched"]
        surface = s["targeted"]
        gated = key == "G"

        if gated:
            # G forces abandon = 1 - switch, so the abandonment axis is a
            # constant and a 2-D grid of it would be seven identical columns.
            table = surface[np.isclose(surface["abandon"], 0.0)][
                ["switch", "d_cm_per_session", "d_conversion_rel_pct",
                 "low_conv_rel_pct", "d_rto_pp", "clears_ship_bar"]].copy()
            table.insert(1, "abandon (= 1 - switch)",
                         (1 - table["switch"]).round(2))
            axis_note = (
                "Gating has no third branch, so abandonment is **1 − switch by "
                "construction** and there is no second axis to sweep. One "
                "assumption decides the whole lever.")
        else:
            table = surface.pivot_table(index="switch", columns="abandon",
                                        values="d_cm_per_session").round(2)
            axis_note = ("Rows are the switch rate, columns the abandonment "
                         "rate. §12.1's ship bar is ₹{:.2f}.".format(
                             sensitivity.SHIP_BAR))

        row = surface[np.isclose(surface["switch"], central["switch"])]
        span = (float(row["d_cm_per_session"].max()
                      - row["d_cm_per_session"].min()) if len(row) else 0.0)
        conv_at_max = (float(row.sort_values("abandon").iloc[-1]
                             ["d_conversion_rel_pct"]) if len(row) else 0.0)
        blocks.append(_fill(
            "### 8.{i} {id} — {name}   (targeted at {depth})\n\n"
            "ΔCM per session. {axis}\n\n<<table>>\n\n{reading}\n\n"
            "**Targeting premium.** Against the flat arm it is negative in "
            "**{nl} of {nc}** grid cells. Against the random, volume-matched arm "
            "it is negative in **{nlm} of {ncm}**. The second number is the one "
            "about targeting; the first is mostly about volume.",
            {"table": md(table, index=not gated)},
            i=i, id=key, name=lever.name,
            depth=decision.DEPTH_LABEL[s["depth"]], axis=axis_note,
            reading=(
                "**Abandonment barely moves the margin.** Across the whole "
                "0-30% abandonment range at the central {s:.0%} switch rate, "
                "ΔCM/session spans **₹{span:.2f}** — because every treated order "
                "sits above p-star, where §10.2's insight bites: an abandoned "
                "order there was worth less than nothing, so losing it is close "
                "to free. What abandonment destroys is not margin but "
                "**conversion**: at the top of that range the aggregate "
                "checkout-conversion move is {conv:.2f}% relative, against a "
                "−1.0% floor. **The binding constraint on a fee is the guardrail, "
                "not the economics** — which is why §6.4 sweeps volume and not "
                "price.".format(s=central["switch"], span=span, conv=conv_at_max)
                if not gated else
                "The lever clears the ship bar at every switch rate on the grid, "
                "including **zero** — a gate that converts nobody and loses every "
                "treated order still raises margin, because the orders it "
                "destroys had negative expected value. That is the cleanest "
                "possible statement of §10.2's insight, and it is also why CM "
                "cannot be the criterion here: at switch = 0 this lever deletes "
                "{lost:.0f}% of orders and the §12.1 conversion floors reject it "
                "on sight.".format(
                    lost=abs(float(surface[np.isclose(surface['switch'], 0.0)]
                                   ['d_conversion_rel_pct'].iloc[0])))),
            nl=int((~cross["targeted_wins"]).sum()), nc=len(cross),
            nlm=int((~crossm["targeted_wins"]).sum()), ncm=len(crossm)))
    blocks.append(
        "### 8.3 Where the risk-based policy stops winning\n\n"
        "Three failure modes, and only one of them is about targeting.\n\n"
        "1. **The conversion floor binds before the economics do.** For a lever "
        "aimed above p-star, more abandonment is nearly free in margin terms and "
        "expensive in conversion terms. The lever does not stop being profitable; "
        "it stops being *permissible*. This is the mode that actually decides "
        "every stick in this library, and it is why §6.4's answer is a volume "
        "window rather than a price.\n"
        "2. **The flat arm out-earns the targeted one.** True across most of both "
        "grids, and it is §7's result. The targeted policy is preferred on the "
        "§12.1 guardrails, not on margin.\n"
        "3. **The lever's own assumption set is wrong.** Only G is genuinely "
        "fragile this way, and the sweep shows why: it clears the margin bar at "
        "every switch rate including zero, so the switch rate does not decide "
        "whether G *works* — it decides how much conversion G destroys on the "
        "way. That makes it a guardrail parameter, not an economics parameter, "
        "and it is measured by an experiment rather than argued from a model.\n\n"
        "**The single number the whole answer turns on is G's switch rate**, and "
        "nobody has measured it on this platform or any other. It is the first "
        "thing Phase 6 should be powered to estimate.")
    return "\n\n".join(blocks)


def _naive(ctx: dict) -> str:
    rows = []
    for key, by_depth in ctx["cells"].items():
        depth = by_depth["_best_depth"]
        true_total = float(_total(by_depth[depth])["d_cm_total"])
        naive_total = float(by_depth[depth]["naive_d_cm_total"])
        rows.append({
            "id": key, "lever": ctx["levers"][key].name,
            "ΔCM causal (₹)": round(true_total, 0),
            "ΔCM naive (₹)": round(naive_total, 0),
            "naive / causal": ("n/a" if abs(true_total) < 1
                               else "{:.2f}x".format(naive_total / true_total)),
        })
    cod = ctx["truth"]["planted_causal_effects"]["cod_on_rto"]
    beta = float(ctx["truth"]["coefficient_ledger"]["rto_model"]["is_cod"])
    gap = float(cod["naive_observed_gap_pp"])
    profile = counterfactual.naive_bias_profile(beta, gap / 100.0)
    crossover = counterfactual.naive_crossover(beta, gap / 100.0)
    return _fill(
        "## 9. What the obvious analysis would have concluded\n\n"
        "Blueprint §10.2 prices a switch at `(24.0% − 4.1%) × ₹416` — the "
        "**observed** COD/prepaid RTO gap, applied flat to every switched order. "
        "`_truth.json` measures that gap at **{n:.2f}pp** against a true average "
        "marginal effect of **{a:.2f}pp**: the naive figure is **{m:.2f}x** the "
        "truth, because {sel:.1%} of it is selection.\n\n"
        "Same simulation, one substitution — the counterfactual prepaid "
        "probability comes from the flat rate gap instead of the planted "
        "structural shift:\n\n<<rows>>\n\n"
        "### 9.1 The naive analysis is not uniformly optimistic, and that is the "
        "finding\n\n"
        "**{lower} of the {tot} levers come out LOWER under the naive channel, "
        "not higher.** That looks like it contradicts the 1.77x headline. It "
        "does not, and the reconciliation is the most transferable thing in this "
        "section.\n\n"
        "The 1.77x is a statement about **population averages**. Applied to an "
        "individual order it does not carry, and it does not even carry with a "
        "consistent sign, because a logit shift is not a constant pp shift — it "
        "is largest near p = 0.5 and vanishes at both extremes:\n\n<<profile>>\n\n"
        "The two agree at **p = {cx:.3f}**, and diverge in opposite directions "
        "either side of it. Below that line a flat gap **overstates** the value "
        "of switching an order; above it, it **understates**. Every lever in this "
        "library is aimed above the line — which is the half of the distribution "
        "where the obvious analysis is *conservative*.\n\n"
        "> **The lesson is not \"naive overstates\". It is that a population rate "
        "gap is not a per-order effect.** An analyst who applies the 17.7pp gap "
        "order by order will misprice every order that is not at the population "
        "mean, and the direction of the error flips at {cx:.1%}. The headline "
        "figure is right about the aggregate and useless for targeting — which "
        "is precisely why §10.2's flat-arm arithmetic and its targeted-arm "
        "arithmetic cannot both be built on it.\n\n"
        "The crossover sits close to p-star ({ps:.4f}) and that is a "
        "**coincidence of this parameterisation**, not a result: it depends on "
        "β = {beta}, on the observed gap, and on nothing about the cost model "
        "that produces p-star. It should not be presented as though the two were "
        "connected.\n\n"
        "> And read L15 before quoting the residual. Adjustment closes ~71% of "
        "the naive-to-truth gap; the remaining ~29% is irreducible because "
        "purchase intent is unobservable — and on real data that residual would "
        "likely be **larger**, because our simulated treatment assignment is "
        "unusually recoverable. **~29% is an optimistic floor, not a realistic "
        "estimate.**",
        {"rows": md(pd.DataFrame(rows)), "profile": md(profile)},
        n=gap, a=float(cod["average_marginal_effect_pp"]),
        m=float(cod["naive_over_truth_multiple"]),
        sel=float(cod["selection_share_of_naive_gap"]),
        cx=crossover, ps=ctx["boundaries"]["med_high"], beta=beta,
        lower=sum(1 for r in rows
                  if str(r["naive / causal"]).endswith("x")
                  and float(str(r["naive / causal"]).rstrip("x")) < 1.0),
        tot=len(rows))


def _exclusions(ctx: dict) -> str:
    ex = ctx["cfg"]["excluded"]["H6_shorten_promise"]
    return (
        "## 10. What is deliberately absent\n\n"
        "### 10.1 H6 — no \"shorten the promise\" lever\n\n"
        "**There is no \"shorten the delivery promise\" lever in this library "
        "and there will not be one.** "
        + " ".join(str(ex["reason"]).split()) + "\n\n"
        "Delay is a real and large RTO driver — the single strongest signal in "
        "the warehouse, and the most tempting feature anyone will propose. It is "
        "nonetheless a **logistics** fix (courier mix, dispatch SLA, network "
        "coverage), and putting it in this library would put a lever in the PRD "
        "that checkout cannot pull. Evidence: " + str(ex["evidence"]) + ".\n\n"
        "Stated rather than omitted, because a silently missing lever reads as an "
        "oversight and this one is a ruling.\n\n"
        "### 10.2 The Stage-4 bar\n\n"
        "`attempt_delay_days`, `delivery_delay_days`, `delivery_attempts` and "
        "`actual_delivery_days` appear in no scoring path, no eligibility rule "
        "and no counterfactual in this phase. `dataset.assert_firewall` "
        "re-asserts them by name on the frame this simulation was built from. The "
        "support/NDR line is taken at its registry mean specifically to avoid "
        "needing `delivery_attempts`.\n\n"
        "### 10.3 What Phase 4 priced on one side only\n\n"
        "The A47 fairness constraint cost ₹3.92 Cr/year and the other side of "
        "that trade was never priced. Nothing in this phase changes that, and "
        "nothing here should be read as having closed it. The per-tier policy "
        "remains the natural control arm for measuring it, and that is a Phase 6 "
        "experiment.")


def _next(ctx: dict) -> str:
    return _fill(
        "## 11. What Stage 2 inherits\n\n"
        "| Artefact | What it is |\n|---|---|\n"
        "| `data/processed/phase5_levers.parquet` | the decision table: lever × "
        "depth × tier |\n"
        "| `config/interventions.yaml` | every [A]; change one number and re-run |\n"
        "| §3's derived tier lines | LOW/MED **{lm:.4f}**, MED/HIGH **{mh:.4f}** |\n\n"
        "**Open, and blocking a confident recommendation:**\n\n"
        "1. **G's switch rate.** One assumption fixes the entire lever and nobody "
        "has measured it. Phase 6 should be powered for it first.\n"
        "2. **D's commitment dose.** §10.1 gives partial payment no magnitudes at "
        "all; the {d:.0%} dose is a Phase 5 judgement and D's rank moves with it.\n"
        "3. **Whether §10.1's fee-abandonment prior or the DGP's planted −0.45 is "
        "the better anchor.** They disagree by roughly a factor of two, and the "
        "sign of the flat-versus-targeted comparison moves with them.\n"
        "4. **F's recovered sessions.** The {pf:,} payment-failure abandons have "
        "no score and no order row, so F is reported here as a floor. Stage 2 "
        "needs an explicit imputation or an explicit statement that it has none.\n"
        "5. **§8.3's tier shares were priors and the HIGH tier is 28.1%, not "
        "17%.** Every restriction volume and every fairness exposure in Stage 2 "
        "inherits that.", {},
        lm=ctx["boundaries"]["low_med"], mh=ctx["boundaries"]["med_high"],
        d=ctx["cells"]["D"][ctx["cells"]["D"]["_best_depth"]]["resp"]["dose"],
        pf=ctx["pop"]["payment_failure_abandons"])
