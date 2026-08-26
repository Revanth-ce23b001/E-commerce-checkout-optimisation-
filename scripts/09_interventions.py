"""Phase 5 Stage 1 — per-intervention simulation and the decision table.

    python scripts/09_interventions.py

Writes reports/phase5_interventions.md and data/processed/phase5_levers.parquet.

Needs `make load` (the view is the only permitted feature source) and `make m2`
(m2_scores.parquet is the scored population, consumed and never re-fitted).

Scope, per the Phase 5 brief: the six §10.1 levers plus COD gating, the
risk-based pricing decision table, and the sensitivity sweep on the two
assumptions that drive everything. NOT in scope here: the five-scenario CM
comparison, which is Stage 2; the A/B design and power analysis, which is
Phase 6; the PRD, which is Phase 7.

Exit codes: 0 normal, 2 if a binding Phase 4 constraint is violated.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.config.loader import load_params  # noqa: E402
from src.interventions import (counterfactual, decision, economics, feasible,  # noqa: E402
                               library, population, report, sensitivity,
                               simulate, tiers)
from src.risk.fairness import PROXY_RATIO_LIMIT  # noqa: E402

SCORES = REPO_ROOT / "data" / "processed" / "m2_scores.parquet"
OUT = REPO_ROOT / "reports" / "phase5_interventions.md"
LEVER_TABLE = REPO_ROOT / "data" / "processed" / "phase5_levers.parquet"


def run_arm(sim, lever, frame, tier, score, boundaries, depth, level="central",
            rng=None, matched_to=None):
    """One (lever, depth, assumption-level) cell, end to end."""
    mask = library.eligible(lever, frame, tier, score, boundaries, depth,
                            rng=rng, matched_to=matched_to)
    resp = library.response(
        lever, library.pp_denominator(frame, lever, tier, score, boundaries), level)
    result = sim.run(mask, library.switchable(lever, frame), resp)
    summary = simulate.summarise(sim, result, tier, depth)
    summary.insert(0, "lever", lever.id)
    return {"mask": mask, "resp": resp, "result": result, "summary": summary}


def main() -> int:
    warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
    params = load_params(str(REPO_ROOT / "config" / "params.yaml"))
    truth = json.loads((REPO_ROOT / "data" / "truth" / "_truth.json").read_text())
    cfg = yaml.safe_load(
        (REPO_ROOT / "config" / "interventions.yaml").read_text(encoding="utf-8"))

    print("building the population from analytics.vw_risk_model_input ...")
    pop = population.build(REPO_ROOT, params, SCORES)
    frame = pop["frame"]
    print("   {:,} scored orders, {:,} checkout sessions, window {} to {}".format(
        len(frame), pop["sessions"], pop["window"][0].date(),
        (pop["window"][1] - pd.Timedelta(days=1)).date()))

    # --- the two derivations, each checked against _truth.json ---------------
    pg_rate = economics.effective_pg_rate(frame, params)
    econ = economics.OrderEconomics(frame, params, pg_rate)
    ledger = economics.reconcile(econ, frame)

    orders = pd.read_parquet(REPO_ROOT / "data" / "raw" / "fct_order.parquet")
    truth_p = pd.read_parquet(
        REPO_ROOT / "data" / "raw" / "truth_order_probability.parquet")
    canonical = counterfactual.reconcile_canonical(orders, truth_p, truth)
    if not canonical["matches"]:
        print("   STOP: the rebuilt AME ({:.4f}pp) does not match _truth.json "
              "({:.4f}pp). The coefficient ledger and the generator have "
              "diverged.".format(canonical["rebuilt_pp"], canonical["truth_file_pp"]))
        return 2
    print("   AME reconciles on {:,} orders: {:.4f}pp = _truth.json".format(
        canonical["orders"], canonical["rebuilt_pp"]))

    effect = counterfactual.CodEffect(frame, truth)
    pstar = float(truth["economics_targets"]["breakeven_rto_probability_derived"])
    boundaries = tiers.solve(econ, effect, cfg["tier_boundaries"], pstar)
    tier = tiers.assign(frame["m2_score"].to_numpy(), boundaries["low_med"],
                        boundaries["med_high"])
    print("   tier lines DERIVED: LOW/MED at p = {:.4f} (Rs {:.0f} anchor), "
          "MED/HIGH at p* = {:.4f}".format(
              boundaries["low_med"], boundaries["anchor_rupees"],
              boundaries["med_high"]))

    sim = simulate.Simulation(frame, econ, effect, pop["sessions"])
    levers = library.load(cfg)
    agreement = library.classification_check(levers)
    if not bool(agreement["agrees"].all()):
        print("   STOP: the Phase 5 lever config disagrees with the A47 "
              "classification in src/risk/interventions.py.")
        print(agreement.to_string(index=False))
        return 2

    # --- every lever, every depth, every band point --------------------------
    rng = np.random.default_rng(int(params.require("seed.master")))
    score = frame["m2_score"].to_numpy()
    naive_gap = float(truth["planted_causal_effects"]["cod_on_rto"]
                      ["naive_observed_gap_pp"])

    cells, guard, bands, fairness = {}, {}, [], []
    for key, lever in levers.items():
        cells[key] = {}
        for depth in library.DEPTHS:
            cell = run_arm(sim, lever, frame, tier, score, boundaries, depth)
            cell["naive_d_cm_total"] = simulate.naive_contrast(
                sim, cell["mask"], library.switchable(lever, frame),
                cell["resp"], naive_gap)["d_cm_total"]
            cells[key][depth] = cell
            guard[(key, depth)] = decision.guardrails(cell["summary"])

        best = decision.best_depth(cells[key], guard, key, lever.blueprint_depth)
        cells[key]["random_matched"] = run_arm(
            sim, lever, frame, tier, score, boundaries, "random_matched",
            rng=rng, matched_to=cells[key][best]["mask"])
        cells[key]["_best_depth"] = best

        for level in library.LEVELS:
            cell = run_arm(sim, lever, frame, tier, score, boundaries, best, level)
            total = cell["summary"][cell["summary"]["tier"] == "ALL"].iloc[0]
            bands.append({"lever": key, "name": lever.name, "depth": best,
                          "level": level,
                          "switch": round(cell["resp"]["switch"], 4),
                          "abandon": round(cell["resp"]["abandon"], 4),
                          "d_cm_per_session": float(total["d_cm_per_session"]),
                          "d_rto_pp": float(total["d_rto_pp"]),
                          "d_conversion_rel_pct": float(total["d_conversion_rel_pct"])})

        if lever.restrictive:
            for depth in ("high_only", "med_high"):
                ratio = decision.geo_ratio(frame, cells[key][depth]["mask"])
                fairness.append({
                    "lever": key, "name": lever.name, "depth": depth,
                    "treated": int(cells[key][depth]["mask"].sum()),
                    "worst_over_best": round(ratio, 2), "limit": PROXY_RATIO_LIMIT,
                    "verdict": "PASS" if ratio <= PROXY_RATIO_LIMIT else "FAIL"})

    fairness = pd.DataFrame(fairness)
    if len(fairness) and not bool((fairness["verdict"] == "PASS").all()):
        print("   STOP: a restrictive lever breaches the FA-01 limit.")
        print(fairness.to_string(index=False))
        return 2
    print("   FA-01 re-measured on every restrictive lever and depth: worst "
          "{:.2f}x against a {}x limit".format(
              fairness["worst_over_best"].max(), PROXY_RATIO_LIMIT))

    table = decision.build_table(cells, levers, guard)
    recommendation = decision.recommend(cells, levers, guard, boundaries)
    premium = {k: decision.targeting_premium(cells[k], levers[k]) for k in cells}
    sweeps = sensitivity.run(sim, levers, frame, tier, score, boundaries, cfg, cells)
    print("   searching for a shippable configuration per lever ...")
    feas = feasible.summarise_feasibility(
        sim, levers, frame, tier, score, boundaries, cells)

    ctx = {
        "pop": pop, "frame": frame, "params": params, "truth": truth, "cfg": cfg,
        "ledger": ledger, "pg_rate": pg_rate, "canonical": canonical,
        "effect_summary": effect.summary(truth), "boundaries": boundaries,
        "tier": tier, "tier_table": tiers.describe(tier, frame, boundaries),
        "baseline": sim.baseline(tier), "levers": levers, "agreement": agreement,
        "cells": cells, "guard": guard, "premium": premium,
        "bands": pd.DataFrame(bands), "fairness": fairness,
        "table": table, "recommendation": recommendation, "sweeps": sweeps,
        "feasible": feas,
        "sim": sim, "naive_gap": naive_gap,
    }
    report.write(ctx, OUT)
    table.to_parquet(LEVER_TABLE, index=False)
    print("\n   wrote " + str(OUT.relative_to(REPO_ROOT)))
    print("   wrote " + str(LEVER_TABLE.relative_to(REPO_ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
