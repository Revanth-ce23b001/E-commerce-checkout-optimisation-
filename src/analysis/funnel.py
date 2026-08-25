"""Phase 3 — funnel and RTO economics, computed in pandas.

Why this exists as well as `sql/10_funnel.sql` and `sql/11_economics.sql`
------------------------------------------------------------------------
Phase 1 §17 requires every metric to be reproducible from **both** SQL and
Python, with matching values. That is not ceremony. The two paths share only
the data: one reads PostgreSQL through the `analyst` role, the other reads
Parquet. A metric that agrees across both has survived two independent
implementations of its definition — and the definitions are where the errors
live, not the arithmetic.

The check is an assertion, not a visual comparison. See
``scripts/05_crosscheck.py``.

Denominator discipline, enforced here as it is in the SQL
---------------------------------------------------------
* RTO rate denominator is **shipped AND NOT censored**. Never orders placed:
  an intervention that raises pre-ship cancellations would otherwise *appear*
  to cut RTO while doing nothing (Phase 1 §5.3, the definitional trap).
* Censored orders are reported separately wherever they would distort a rate.
* Annualisation uses the cost per **resolved** order times the annual order
  population — not the total cost times a sample multiplier. The two differ by
  15.7% here, because spreading RTO cost across censored orders treats an
  unresolved outcome as a costless one (decision A41, limitation L9).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW = REPO_ROOT / "data" / "raw"

# Phase 1 §1.1 population framing. The only literal here; everything else is
# measured. The annualisation FACTOR is derived from it, never hard-coded.
POPULATION_ANNUAL_ORDERS = 24_000_000


def load(name: str) -> pd.DataFrame:
    return pd.read_parquet(RAW / f"{name}.parquet")


def load_tables() -> dict[str, pd.DataFrame]:
    return {n: load(n) for n in
            ("fct_checkout_session", "fct_order", "fct_order_economics",
             "dim_product", "dim_geography")}


# ---------------------------------------------------------------------------
# A — the funnel
# ---------------------------------------------------------------------------


def funnel_steps(tables: dict) -> pd.DataFrame:
    """Q1. Sessions to delivered goods, every step on the session denominator."""
    s, o = tables["fct_checkout_session"], tables["fct_order"]
    n = len(s)
    rows = [
        ("sessions started", n),
        ("address completed", int(s["address_completed"].sum())),
        ("payment page reached", int(s["payment_page_reached"].sum())),
        ("orders placed", int((~s["checkout_abandoned"]).sum())),
        ("shipped", int(o["is_shipped"].sum())),
        ("delivered", int(o["is_delivered"].fillna(False).sum())),
    ]
    frame = pd.DataFrame(rows, columns=["step", "n"])
    frame["pct_of_sessions"] = frame["n"] / n
    return frame


def conversions(tables: dict) -> dict:
    """Q2. Checkout conversion and net conversion, plus the CM/CSS baseline.

    ``net_conversion`` is delivered / sessions. It is mechanically depressed by
    censoring, because a censored order is neither delivered nor RTO — so
    ``net_conversion_resolved_basis`` restates it as if the censored orders
    resolved at the observed rate. Both are reported. Neither replaces the
    other.
    """
    s, o = tables["fct_checkout_session"], tables["fct_order"]
    e = tables["fct_order_economics"]
    joined = o.merge(e, on="order_id", validate="one_to_one")

    sessions = len(s)
    orders = len(o)
    delivered = int(o["is_delivered"].fillna(False).sum())
    censored = int(o["is_censored"].sum())
    resolved = int((o["is_shipped"] & ~o["is_censored"]).sum())
    rto = int(o["rto_flag"].fillna(False).sum())

    is_delivered = joined["is_delivered"].fillna(False)
    is_rto = joined["rto_flag"].fillna(False)
    return {
        "sessions": sessions,
        "orders": orders,
        "checkout_conversion": orders / sessions,
        "net_conversion": delivered / sessions,
        "conversion_leak": (orders - delivered) / sessions,
        "censored_excluded": censored,
        "cm_per_session_started": float(joined["contribution_margin"].sum()) / sessions,
        "cm_per_delivered_order":
            float(joined.loc[is_delivered, "contribution_margin"].sum()) / delivered,
        "rto_drag_per_session":
            float(joined.loc[is_rto, "rto_economic_cost"].sum()) / sessions,
        "net_conversion_resolved_basis":
            orders / sessions * (resolved - rto) / resolved,
    }


def abandon_steps(tables: dict) -> pd.DataFrame:
    """Q3. Where sessions die. Converted sessions get a NULL abandon share.

    A converted session did not abandon, so giving it a share of the abandons
    would invent a denominator it is not a member of.
    """
    s = tables["fct_checkout_session"]
    step = s["abandon_step"].fillna("CONVERTED")
    frame = step.value_counts().rename_axis("step").reset_index(name="sessions")
    frame["pct_of_sessions"] = frame["sessions"] / len(s)
    abandons = int(s["abandon_step"].notna().sum())
    frame["pct_of_abandons"] = frame.apply(
        lambda r: None if r["step"] == "CONVERTED" else r["sessions"] / abandons, axis=1)
    return frame.sort_values("sessions", ascending=False, ignore_index=True)


def payment_success(tables: dict) -> dict:
    """Q4. Prepaid reliability, and how much COD it manufactures.

    Phase 1 §5.3: COD has no payment-success analogue. These are prepaid-only
    figures and must never be blended with COD.
    """
    s = tables["fct_checkout_session"]
    attempted = int((s["payment_attempt_count"] > 0).sum())
    prepaid_orders = int((s["final_payment_method"] == "PREPAID").sum())
    cod_orders = int((s["final_payment_method"] == "COD").sum())
    switched = int(s["switched_to_cod_after_failure"].sum())
    return {
        "sessions_attempting_prepaid": attempted,
        "total_attempts": int(s["payment_attempt_count"].sum()),
        "prepaid_orders": prepaid_orders,
        "prepaid_success_rate": prepaid_orders / attempted,
        "switched_to_cod": switched,
        "pct_of_cod_from_failure": switched / cod_orders,
        "abandoned_at_failure": int((s["abandon_step"] == "PAYMENT_FAILURE").sum()),
    }


# ---------------------------------------------------------------------------
# B — RTO economics
# ---------------------------------------------------------------------------


def rto_base(tables: dict) -> pd.DataFrame:
    """The RTO population: shipped AND NOT censored. Defined once."""
    o, e = tables["fct_order"], tables["fct_order_economics"]
    joined = o.merge(e, on="order_id", validate="one_to_one")
    return joined[joined["is_shipped"] & ~joined["is_censored"]].copy()


def annualisation_factor(tables: dict) -> float:
    """DERIVED, never the literal 240 (decision A32, test EC-08)."""
    return POPULATION_ANNUAL_ORDERS / len(tables["fct_order"])


def rto_by_method(tables: dict) -> pd.DataFrame:
    """Q5. The COD/prepaid split of the RTO bill."""
    base = rto_base(tables)
    resolved_total = len(base)
    out = []
    for method, group in base.groupby("payment_method"):
        rto = group[group["rto_flag"].fillna(False)]
        out.append({
            "payment_method": method,
            "resolved_orders": len(group),
            "share_of_resolved": len(group) / resolved_total,
            "rto_orders": len(rto),
            "rto_rate": len(rto) / len(group),
            "economic_cost_per_rto": float(rto["rto_economic_cost"].mean()),
            "cash_loss_per_rto": float(rto["rto_cash_loss"].mean()),
            "annual_exposure_cr": float(rto["rto_economic_cost"].sum())
                                  / resolved_total * POPULATION_ANNUAL_ORDERS / 1e7,
        })
    return pd.DataFrame(out).sort_values("payment_method", ignore_index=True)


def avoidability(tables: dict) -> pd.DataFrame:
    """Q7. Addressable vs structural, measured on COST as well as on count.

    Phase 1 §7.2 *assumed* 65% addressable. This measures it. The cost share and
    the count share are different numbers whenever the reason classes carry
    different average costs, so both are returned — quoting one as the other is
    the error this guards against.
    """
    rto = rto_base(tables)
    rto = rto[rto["rto_flag"].fillna(False)]
    grouped = rto.groupby("rto_reason_class")["rto_economic_cost"]
    frame = pd.DataFrame({
        "rto_orders": grouped.size(),
        "total_cost": grouped.sum(),
        "avg_cost_per_rto": grouped.mean(),
    }).reset_index()
    frame["share_of_rto_count"] = frame["rto_orders"] / frame["rto_orders"].sum()
    frame["share_of_rto_cost"] = frame["total_cost"] / frame["total_cost"].sum()
    return frame.sort_values("total_cost", ascending=False, ignore_index=True)


def waterfall(tables: dict, efficacy: float = 0.30) -> dict:
    """Q8. Exposure down to recoverable.

    ``efficacy`` is an ASSUMPTION carried from Phase 1 §7.2 and flagged as such.
    Only the Phase 6 experiment can replace it with a measured ATE — until then
    every figure below the addressable line is assumption-dependent and is
    labelled that way in the report.
    """
    base = rto_base(tables)
    rto = base[base["rto_flag"].fillna(False)]
    resolved = len(base)

    def annual(total: float) -> float:
        return total / resolved * POPULATION_ANNUAL_ORDERS / 1e7

    exposure = annual(float(rto["rto_economic_cost"].sum()))
    cash = annual(float(rto["rto_cash_loss"].sum()))
    addressable_cost = float(
        rto.loc[rto["rto_reason_class"] == "ADDRESSABLE", "rto_economic_cost"].sum())
    addressable = annual(addressable_cost)
    return {
        "exposure_cr": exposure,
        "cash_cr": cash,
        "foregone_cm_cr": exposure - cash,
        "structural_cr": exposure - addressable,
        "addressable_cr": addressable,
        "addressable_share": addressable_cost / float(rto["rto_economic_cost"].sum()),
        "efficacy_assumed": efficacy,
        "recoverable_cr": addressable * efficacy,
        "resolved_orders": resolved,
        "rto_orders": len(rto),
        "censored_excluded": int(tables["fct_order"]["is_censored"].sum()),
    }
