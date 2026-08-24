"""Modules 20–21 — customer roll-up and the ground-truth file.

Module 20 writes the four columns that are **the subtlest leakage trap in the
schema** (CLAUDE.md rule 6): ``hist_orders_final``, ``hist_rto_rate_final``,
``hist_cod_share_final`` and ``clv_estimate``. Each is an end-of-window aggregate
that *includes the current order*, and each looks exactly like an innocent
customer attribute. They stay in the schema because dashboards need them; the
firewall is ``vw_risk_model_input``, which must not select them. Keeping the trap
present and the firewall explicit is the point.

Module 21 writes ``_truth.json``. Two things in it are **derived, not asserted**:
the average marginal effect of COD (decision A6) and the break-even RTO
probability (decision A38). Everything downstream quotes this file rather than
the spec prose, because the spec's narrative figures belong to an earlier
parameterisation and no longer describe the dataset (limitation L8).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.models.logit import logistic


def rollup_customers(
    customers: pd.DataFrame,
    orders: pd.DataFrame,
    economics: pd.DataFrame,
) -> pd.DataFrame:
    """Module 20 — the end-of-window aggregates. 🔒 LEAKAGE by construction."""
    merged = orders.merge(economics[["order_id", "contribution_margin"]], on="order_id")
    rto = merged["rto_flag"].fillna(False).to_numpy(bool)
    is_cod = (merged["payment_method"] == "COD").to_numpy()
    resolved = merged["rto_flag"].notna().to_numpy()

    grouped = pd.DataFrame({
        "customer_id": merged["customer_id"],
        "orders": 1,
        "rto": rto.astype(int),
        "resolved": resolved.astype(int),
        "cod": is_cod.astype(int),
        "cm": merged["contribution_margin"],
    }).groupby("customer_id").sum()

    frame = customers.copy()
    idx = frame["customer_id"]
    in_window = grouped.reindex(idx).fillna(0.0)

    # DQ-07b's ledger identity: pre-window + in-window = final, for every customer,
    # with no exceptions and independent of resolution state.
    frame["hist_orders_final"] = (
        frame["pre_window_orders"].to_numpy() + in_window["orders"].to_numpy()
    ).astype(np.int64)

    total_rto = frame["pre_window_rto_count"].to_numpy() + in_window["rto"].to_numpy()
    total_resolved = (
        frame["pre_window_delivered"].to_numpy() + frame["pre_window_rto_count"].to_numpy()
        + in_window["resolved"].to_numpy()
    )
    frame["hist_rto_rate_final"] = np.round(
        np.divide(total_rto, total_resolved,
                  out=np.zeros_like(total_rto, dtype=float), where=total_resolved > 0), 3
    )
    total_cod = frame["pre_window_cod_orders"].to_numpy() + in_window["cod"].to_numpy()
    frame["hist_cod_share_final"] = np.round(
        np.divide(total_cod, frame["hist_orders_final"].to_numpy(),
                  out=np.zeros(len(frame)), where=frame["hist_orders_final"].to_numpy() > 0), 3
    )
    frame["clv_estimate"] = np.round(in_window["cm"].to_numpy(), 2)
    frame["analytics_segment"] = _segment(frame)
    return frame


def _segment(frame: pd.DataFrame) -> np.ndarray:
    """Blueprint §8.2's tenure x COD 3x3 label. Dashboard only, never a feature."""
    tenure = frame["tenure_days_at_window_start"].to_numpy()
    cod = frame["hist_cod_share_final"].to_numpy()
    tenure_band = np.where(tenure < 180, "NEW", np.where(tenure < 540, "GROWING", "ESTABLISHED"))
    cod_band = np.where(cod >= 0.8, "COD", np.where(cod >= 0.3, "MIXED", "PREPAID"))
    return np.char.add(np.char.add(tenure_band.astype(str), "_"), cod_band.astype(str))


def write_truth(
    path: Path,
    params,
    solved: dict,
    metrics,
    orders: pd.DataFrame,
    economics: pd.DataFrame,
    auc_ceiling: float,
    ledger=None,
) -> dict:
    """Module 21 — ``_truth.json``. The DERIVED figures are the authority.

    Limitation L8: the spec's 19.9pp naive gap, ~13.4pp effect and 33% selection
    share belong to ``noise_sd = 0.85`` and no longer describe this dataset.
    Everything downstream reads the ``*_derived`` fields here, never the prose.
    """
    from src.economics.order_economics import derive_breakeven

    beta_cod = float(params.require("rto_model.coefficients.is_cod"))
    naive_gap = (metrics.rto_rate_cod - metrics.rto_rate_prepaid) * 100
    ame = _average_marginal_effect(metrics, beta_cod)
    selection_share = (naive_gap - ame) / naive_gap if naive_gap > 0 else float("nan")

    breakeven = derive_breakeven(economics, orders)
    factor = float(params.require("scale.population_annual_orders")) / metrics.n_orders

    truth = {
        "run_manifest": {
            "master_seed": int(params.require("seed.master")),
            "params_sha256": params.sha256,
            "dgp_sha256": params.dgp_sha256,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "generator_version": params.require("meta.generator_version"),
        },
        "calibrated_levels": {
            "cod_model_beta0": solved["beta_0"],
            "rto_model_gamma0": solved["gamma_0"],
            "conversion_model_alpha0": solved["alpha_0"],
            "pre_window_cod_pi0": solved["pi_cod0"],
            "pre_window_rto_pi0": solved["pi_rto0"],
            "product_price_scalar": solved["price_scalar"],
            "support_ndr_base": float(params.require("economics.support_ndr_base_solved")),
        },
        "frozen": {
            # Decision A38: solved once against GT-05, then frozen. CAL-11 sits
            # 0.02 from its ceiling and rises with noise, so this must not move.
            "post_dispatch_noise_sd": float(
                params.require("rto_model.post_dispatch_shock.noise_sd")
            ),
            "post_dispatch_noise_sd_spec_value": float(
                params.require("rto_model.post_dispatch_shock.noise_sd_spec_value")
            ),
        },
        "planted_causal_effects": {
            "cod_on_rto": {
                "logit_coefficient": beta_cod,
                "odds_ratio": float(np.exp(beta_cod)),
                # DERIVED (A6). The spec's 13.4pp was computed at a baseline that
                # no longer exists; this is measured over the actual population.
                "average_marginal_effect_pp": ame,
                "naive_observed_gap_pp": naive_gap,
                "selection_share_of_naive_gap": selection_share,
                "naive_over_truth_multiple": naive_gap / ame if ame else float("nan"),
                "spec_prose_values_superseded": {
                    "marginal_effect_pp": 13.4, "naive_gap_pp": 19.9,
                    "selection_share": 0.327,
                    "note": "belong to noise_sd = 0.85; see limitation L8",
                },
            },
            "address_completeness_on_rto": {
                "logit_coefficient": float(
                    params.require("rto_model.coefficients.address_completeness")
                )
            },
        },
        "economics_targets": {
            "mean_gmv_per_order": metrics.mean_gmv,
            "mean_order_value_per_order": metrics.mean_order_value,
            **breakeven,
            "breakeven_rto_probability_expected": 0.257,
            "annualization_factor_derived": factor,
            "annualization_factor_expected": float(
                params.require("scale.annualization_factor_expected")
            ),
        },
        # The runtime record of every coefficient consumed. Persisted so the
        # standalone validation run can perform a REAL CAL-09 rather than
        # comparing params.yaml to a copy of itself.
        "coefficient_ledger": ledger.as_dict() if ledger is not None else {},
        "achieved": {
            "n_orders": metrics.n_orders,
            "cod_share": metrics.cod_share,
            "checkout_conversion": metrics.conversion_rate,
            "rto_rate_blended": metrics.rto_rate_blended,
            "rto_rate_cod_emergent": metrics.rto_rate_cod,
            "rto_rate_prepaid_emergent": metrics.rto_rate_prepaid,
            "auc_ceiling_precheckout": auc_ceiling,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return truth


def _average_marginal_effect(metrics, beta_cod: float) -> float:
    """Decision A6's canonical effect, over the actual shipped COD population."""
    e = metrics.extra
    mask = e["shipped"] & ~e["censored"] & e["is_cod_order"]
    p = np.clip(e["p_rto_final"][mask], 1e-12, 1 - 1e-12)
    lp = np.log(p / (1 - p))
    return float(np.mean(logistic(lp) - logistic(lp - beta_cod))) * 100
