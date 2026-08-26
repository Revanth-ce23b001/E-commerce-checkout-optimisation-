"""GT-03 diagnostics runner — measure, do not fix.

Answers the three questions the 2026-08-26 ruling asked about GT-03's failure
and writes ``reports/gt03_diagnostics.md``. It changes no parameter, no
threshold, no feature set and no test. GT-03 remains FAIL.

**This script reads the truth schema on purpose.** ``truth_customer_latent``
holds the three latents that CLAUDE.md invariant 4 keeps away from every
analyst-visible surface. Measurement 2 asks whether the analyst-visible features
can *reconstruct* them, which cannot be answered without holding the answer
next to the attempt. Nothing it reads reaches a model, a view or an export —
the same standing the validation suite has.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis import gt03_diagnostics as D  # noqa: E402

POPULATION = REPO_ROOT / "data" / "processed" / "h1_population.parquet"
LATENTS = REPO_ROOT / "data" / "raw" / "truth_customer_latent.parquet"
TRUTH = REPO_ROOT / "data" / "truth" / "_truth.json"
OUT = REPO_ROOT / "reports" / "gt03_diagnostics.md"

# GT-03's ceiling. Quoted, not re-derived: the point of the report is to explain
# a breach of this number, so it has to be the number the test uses.
CEILING = 0.65
RECONSTRUCTIBLE_R2 = 0.35


def table(frame: pd.DataFrame, floats: dict) -> str:
    """Plain pipe table. No box-drawing characters (CLAUDE.md reporting rule)."""
    out = frame.copy()
    for column, spec in floats.items():
        if column in out.columns:
            out[column] = out[column].map(spec.format)
    header = "| " + " | ".join(out.columns) + " |"
    rule = "|" + "|".join(["---"] * len(out.columns)) + "|"
    body = ["| " + " | ".join(str(v) for v in row) + " |"
            for row in out.itertuples(index=False)]
    return "\n".join([header, rule, *body])


def main() -> int:
    if not POPULATION.exists():
        print(f"{POPULATION} is absent. Run the Phase 3 analysis first.")
        return 1

    pop = pd.read_parquet(POPULATION)
    latents = pd.read_parquet(LATENTS)
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    cod = truth["planted_causal_effects"]["cod_on_rto"]
    ame = cod["average_marginal_effect_pp"]
    naive = cod["naive_observed_gap_pp"]

    print("1/3  refitting without", D.SUSPECT, flush=True)
    drop = D.without_pit_rto_rate(pop, ame, naive)
    print("2/3  regressing latents on the safe feature set", flush=True)
    recon = D.latent_reconstructibility(pop, latents)
    print("3/3  per-confounder deviance and closure", flush=True)
    contrib = D.confounder_contributions(pop, ame, naive)

    recon_table = recon["table"]
    only_latents = recon_table[recon_table["kind"] == "latent"]
    channel = recon_table[recon_table["kind"] == "choice channel"]
    worst = only_latents["customer_r2"].max()
    worst_order = only_latents["order_r2"].max()
    worst_latent = only_latents.loc[only_latents["customer_r2"].idxmax(), "target"]
    channel_r2 = channel["customer_r2"].max()
    top = contrib["table"].head(10)

    lines = [
        "# GT-03 diagnostics — measured, not fixed",
        "",
        f"Population: `{POPULATION.relative_to(REPO_ROOT).as_posix()}`, "
        f"n = {len(pop):,} (shipped AND NOT censored).",
        f"AME **{ame:.2f}pp**, naive **{naive:.2f}pp**, selection component "
        f"**{naive - ame:.2f}pp**. GT-03's closure ceiling is **{CEILING:.0%}**.",
        "",
        "GT-03 is **not restated and not waived**. This report answers the three "
        "questions the ruling asked before ruling further. No parameter, "
        "threshold, feature set or test changed.",
        "",
        "---",
        "",
        f"## 1. Refit without `{D.SUSPECT}`",
        "",
        "Dropped columns: " + ", ".join(f"`{c}`" for c in drop["removed_columns"]) + ".",
        "",
        table(drop["table"], {
            "logit_att_pp": "{:.2f}", "logit_ate_pp": "{:.2f}",
            "closed_att": "{:.1%}", "closed_ate": "{:.1%}",
            "cod_coefficient": "{:.4f}", "pseudo_r2": "{:.4f}"}),
        "",
        "Propensity matching (GT-03's PRIMARY estimate, ATT):",
        "",
        "| Specification | Estimate | Closes | PS AUC | Unmatched COD |",
        "|---|---|---|---|---|",
        "| full confounder set | {:.2f}pp | {:.1%} | {:.4f} | {:.1%} |".format(
            drop["psm_full_pp"], drop["psm_full_closed"],
            drop["psm_full_ps_auc"], drop["psm_full_unmatched"]),
        "| minus `{}` | {:.2f}pp | {:.1%} | {:.4f} | {:.1%} |".format(
            D.SUSPECT, drop["psm_reduced_pp"], drop["psm_reduced_closed"],
            drop["psm_reduced_ps_auc"], drop["psm_reduced_unmatched"]),
        "",
        "---",
        "",
        "## 2. Are the latents reconstructible from safe features?",
        "",
        "R² of an OLS regression of each latent on the safe feature set. "
        "Order-level is what an adjustment exploits; customer-level is the "
        "honest per-person figure and is what any 'unobservable by "
        f"construction' claim should be qualified against. Threshold for "
        f"'substantially reconstructible': **R² > {RECONSTRUCTIBLE_R2:.2f}**.",
        "",
        table(recon_table, {"order_r2": "{:.4f}", "customer_r2": "{:.4f}"}),
        "",
        "Highest R² on any LATENT: **{:.4f}** order-level, **{:.4f}** "
        "customer-level (`{}`). Threshold {:.2f} — {}.".format(
            worst_order, worst, worst_latent, RECONSTRUCTIBLE_R2,
            "EXCEEDED, the 'unobservable by construction' claim needs "
            "qualifying in every document that makes it"
            if worst > RECONSTRUCTIBLE_R2 else
            "NOT exceeded on any latent, at either level"),
        "",
        "`{}` is not a latent. It is the composite the three latents drive — "
        "the CHOICE channel — and it reaches **{:.4f}** customer-level R². A "
        "latent can be unrecoverable while the choice it produces is highly "
        "predictable, and the two are different findings that look identical "
        "in a closure figure.".format(D.CHOICE_CHANNEL, channel_r2),
        "",
        "---",
        "",
        "## 3. Which confounders close the gap",
        "",
        "Deviance contribution is the likelihood-ratio statistic for dropping "
        "the block from the full RTO model — the definition H6 and BR-09 use. "
        "`closure_lost_pp` is how many percentage points of GT-03's closure "
        "disappear when the block is dropped, so a term can rank high on "
        "deviance and near-zero on closure if it is uncorrelated with COD "
        "choice.",
        "",
        "Full model closes **{:.1%}**; with no confounders at all the estimate "
        "is **{:.2f}pp**, closing **{:.1%}**. Total explained deviance "
        "**{:.1f}**.".format(
            contrib["full_closed"], contrib["unadjusted_att_pp"],
            contrib["unadjusted_closed"], contrib["total_explained"]),
        "",
        "Top 10 by deviance contribution:",
        "",
        table(top, {"deviance_contribution": "{:.1f}",
                    "share_of_explained": "{:.1%}",
                    "att_without_pp": "{:.2f}",
                    "closed_without": "{:.1%}",
                    "closure_lost_pp": "{:+.2f}"}),
        "",
        "Same blocks reordered by closure lost:",
        "",
        table(contrib["table"].sort_values(
            "closure_lost_pp", ascending=False, ignore_index=True).head(10),
            {"deviance_contribution": "{:.1f}", "share_of_explained": "{:.1%}",
             "att_without_pp": "{:.2f}", "closed_without": "{:.1%}",
             "closure_lost_pp": "{:+.2f}"}),
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", OUT.relative_to(REPO_ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
