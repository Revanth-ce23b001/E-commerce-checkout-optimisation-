"""Assert that every Phase 3 metric agrees between SQL and Python.

Phase 1 §17: "Every metric reproducible from both SQL and Python, with matching
values." This asserts it rather than displaying two tables side by side for a
human to compare, because eyeballing two 6-row tables is exactly how a
definitional difference survives review.

The two paths share only the underlying data. SQL reads PostgreSQL through the
`analyst` role; Python reads Parquet. They are independent implementations of
the same definitions, and the definitions are where the errors are.

    python scripts/05_crosscheck.py

Exit 0 if every metric matches, 1 otherwise. Prints one line per metric so a
passing run is still a readable record of what was checked.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.analysis import funnel as F  # noqa: E402

# Money is compared to the paisa; rates to 4 decimal places, which is the
# precision the SQL rounds to. Counts must be exact.
RATE_TOL = 1e-4
MONEY_TOL = 0.01
CR_TOL = 0.01


def analyst_dsn() -> dict:
    env = {}
    for line in (REPO_ROOT / "config" / "database.env").read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return {
        "host": env.get("PGHOST", "localhost"),
        "port": env.get("PGPORT", "5432"),
        "dbname": env.get("PGDATABASE", "checkout_rto"),
        # Deliberately the RESTRICTED role. If a Phase 3 query needs `truth`,
        # that is a finding about the query, not a reason to escalate.
        "user": "analyst",
        "password": "analyst_dev_only",
    }


def query(sql: str) -> list[tuple]:
    import psycopg2
    with psycopg2.connect(**analyst_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checked = 0

    def eq(self, label: str, sql_value, py_value, tol: float = 0.0) -> None:
        self.checked += 1
        a = float(sql_value) if isinstance(sql_value, (Decimal, int, float)) else sql_value
        b = float(py_value) if isinstance(py_value, (Decimal, int, float)) else py_value
        ok = (a == b) if tol == 0 else abs(a - b) <= tol
        mark = "ok " if ok else "FAIL"
        shown = f"{a:,.4f}" if isinstance(a, float) else str(a)
        print(f"   [{mark}] {label:<46} {shown}")
        if not ok:
            self.failures.append(f"{label}: SQL {a!r} != Python {b!r}")


def main() -> int:
    tables = F.load_tables()
    c = Checker()
    rule = "=" * 74
    print(f"{rule}\nSQL vs PYTHON CROSS-CHECK  (analyst role vs parquet)\n{rule}")

    # -- A: funnel ---------------------------------------------------------
    print("\nQ1  funnel steps")
    sql_steps = dict(query("""
        SELECT 'sessions started', count(*) FROM analytics.fct_checkout_session
        UNION ALL SELECT 'address completed', count(*) FROM analytics.fct_checkout_session WHERE address_completed
        UNION ALL SELECT 'payment page reached', count(*) FROM analytics.fct_checkout_session WHERE payment_page_reached
        UNION ALL SELECT 'orders placed', count(*) FROM analytics.fct_checkout_session WHERE NOT checkout_abandoned
        UNION ALL SELECT 'shipped', count(*) FROM analytics.fct_order WHERE is_shipped
        UNION ALL SELECT 'delivered', count(*) FROM analytics.fct_order WHERE is_delivered;"""))
    py_steps = F.funnel_steps(tables).set_index("step")["n"].to_dict()
    for step, n in sql_steps.items():
        c.eq(f"count: {step}", n, py_steps[step])

    print("\nQ2  conversions and CM baseline")
    (sessions, orders, checkout_conv, net_conv, leak, censored,
     cm_css, cm_delivered, drag, net_resolved), = query("""
        WITH s AS (SELECT count(*) n FROM analytics.fct_checkout_session),
        o AS (SELECT count(*) orders,
                     count(*) FILTER (WHERE is_delivered) delivered,
                     count(*) FILTER (WHERE is_shipped AND NOT is_censored) resolved,
                     count(*) FILTER (WHERE rto_flag) rto,
                     count(*) FILTER (WHERE is_censored) censored
              FROM analytics.fct_order),
        e AS (SELECT sum(contribution_margin) total_cm,
                     sum(contribution_margin) FILTER (WHERE o.is_delivered) delivered_cm,
                     sum(rto_economic_cost) FILTER (WHERE o.rto_flag) rto_cost
              FROM analytics.fct_order_economics e JOIN analytics.fct_order o USING (order_id))
        SELECT (SELECT n FROM s), (SELECT orders FROM o),
               (SELECT orders FROM o)::numeric / (SELECT n FROM s),
               (SELECT delivered FROM o)::numeric / (SELECT n FROM s),
               ((SELECT orders FROM o) - (SELECT delivered FROM o))::numeric / (SELECT n FROM s),
               (SELECT censored FROM o),
               (SELECT total_cm FROM e) / (SELECT n FROM s),
               (SELECT delivered_cm FROM e) / (SELECT delivered FROM o),
               (SELECT rto_cost FROM e) / (SELECT n FROM s),
               (SELECT orders FROM o)::numeric / (SELECT n FROM s)
                 * ((SELECT resolved FROM o) - (SELECT rto FROM o))::numeric / (SELECT resolved FROM o);""")
    py = F.conversions(tables)
    c.eq("sessions", sessions, py["sessions"])
    c.eq("orders", orders, py["orders"])
    c.eq("checkout_conversion", checkout_conv, py["checkout_conversion"], RATE_TOL)
    c.eq("net_conversion", net_conv, py["net_conversion"], RATE_TOL)
    c.eq("conversion_leak", leak, py["conversion_leak"], RATE_TOL)
    c.eq("censored_excluded", censored, py["censored_excluded"])
    c.eq("cm_per_session_started", cm_css, py["cm_per_session_started"], MONEY_TOL)
    c.eq("cm_per_delivered_order", cm_delivered, py["cm_per_delivered_order"], MONEY_TOL)
    c.eq("rto_drag_per_session", drag, py["rto_drag_per_session"], MONEY_TOL)
    c.eq("net_conversion_resolved_basis", net_resolved, py["net_conversion_resolved_basis"], RATE_TOL)

    print("\nQ3  abandon steps")
    sql_ab = dict(query("""
        SELECT coalesce(abandon_step, 'CONVERTED'), count(*)
        FROM analytics.fct_checkout_session GROUP BY 1;"""))
    py_ab = F.abandon_steps(tables).set_index("step")["sessions"].to_dict()
    for step, n in sql_ab.items():
        c.eq(f"abandon: {step}", n, py_ab[step])

    print("\nQ4  payment success")
    (attempted, total_attempts, prepaid_orders, success_rate,
     switched, pct_cod_failure, abandoned_failure), = query("""
        SELECT count(*) FILTER (WHERE payment_attempt_count > 0),
               sum(payment_attempt_count),
               count(*) FILTER (WHERE final_payment_method = 'PREPAID'),
               count(*) FILTER (WHERE final_payment_method = 'PREPAID')::numeric
                 / count(*) FILTER (WHERE payment_attempt_count > 0),
               count(*) FILTER (WHERE switched_to_cod_after_failure),
               count(*) FILTER (WHERE switched_to_cod_after_failure)::numeric
                 / count(*) FILTER (WHERE final_payment_method = 'COD'),
               count(*) FILTER (WHERE abandon_step = 'PAYMENT_FAILURE')
        FROM analytics.fct_checkout_session;""")
    py = F.payment_success(tables)
    c.eq("sessions_attempting_prepaid", attempted, py["sessions_attempting_prepaid"])
    c.eq("total_attempts", total_attempts, py["total_attempts"])
    c.eq("prepaid_orders", prepaid_orders, py["prepaid_orders"])
    c.eq("prepaid_success_rate", success_rate, py["prepaid_success_rate"], RATE_TOL)
    c.eq("switched_to_cod", switched, py["switched_to_cod"])
    c.eq("pct_of_cod_from_failure", pct_cod_failure, py["pct_of_cod_from_failure"], RATE_TOL)
    c.eq("abandoned_at_failure", abandoned_failure, py["abandoned_at_failure"])

    # -- B: economics ------------------------------------------------------
    print("\nQ5  RTO by payment method")
    sql_method = query("""
        WITH b AS (SELECT count(*) FILTER (WHERE is_shipped AND NOT is_censored) resolved
                   FROM analytics.fct_order)
        SELECT payment_method, count(*), count(*) FILTER (WHERE rto_flag),
               count(*) FILTER (WHERE rto_flag)::numeric / count(*),
               sum(rto_economic_cost) FILTER (WHERE rto_flag)
                 / count(*) FILTER (WHERE rto_flag),
               sum(rto_economic_cost) FILTER (WHERE rto_flag)
                 / (SELECT resolved FROM b) * 24000000.0 / 1e7
        FROM analytics.vw_rto_base GROUP BY payment_method ORDER BY payment_method;""")
    py_method = F.rto_by_method(tables).set_index("payment_method")
    for method, resolved, rto_n, rate, cost, exposure in sql_method:
        row = py_method.loc[method]
        c.eq(f"{method}: resolved orders", resolved, row["resolved_orders"])
        c.eq(f"{method}: rto orders", rto_n, row["rto_orders"])
        c.eq(f"{method}: rto rate", rate, row["rto_rate"], RATE_TOL)
        c.eq(f"{method}: economic cost per rto", cost, row["economic_cost_per_rto"], MONEY_TOL)
        c.eq(f"{method}: annual exposure Cr", exposure, row["annual_exposure_cr"], CR_TOL)

    print("\nQ6  derived annualisation factor")
    (factor,), = query("SELECT 24000000.0 / count(*) FROM analytics.fct_order;")
    c.eq("annualisation_factor_derived", factor, F.annualisation_factor(tables), 1e-6)

    print("\nQ7  addressable vs structural")
    sql_av = query("""
        SELECT rto_reason_class, count(*), sum(rto_economic_cost),
               sum(rto_economic_cost) / sum(sum(rto_economic_cost)) OVER ()
        FROM analytics.vw_rto_base WHERE rto_flag GROUP BY 1 ORDER BY 1;""")
    py_av = F.avoidability(tables).set_index("rto_reason_class")
    for klass, n, total, share in sql_av:
        c.eq(f"{klass}: rto orders", n, py_av.loc[klass, "rto_orders"])
        c.eq(f"{klass}: total cost", total, py_av.loc[klass, "total_cost"], MONEY_TOL)
        c.eq(f"{klass}: share of cost", share, py_av.loc[klass, "share_of_rto_cost"], RATE_TOL)

    print("\nQ8  waterfall")
    (exposure, cash, addressable), = query("""
        WITH b AS (SELECT count(*) FILTER (WHERE is_shipped AND NOT is_censored) resolved
                   FROM analytics.fct_order)
        SELECT sum(rto_economic_cost) / (SELECT resolved FROM b) * 24000000.0 / 1e7,
               sum(rto_cash_loss)     / (SELECT resolved FROM b) * 24000000.0 / 1e7,
               sum(rto_economic_cost) FILTER (WHERE rto_reason_class = 'ADDRESSABLE')
                 / (SELECT resolved FROM b) * 24000000.0 / 1e7
        FROM analytics.vw_rto_base WHERE rto_flag;""")
    w = F.waterfall(tables)
    c.eq("total exposure Cr", exposure, w["exposure_cr"], CR_TOL)
    c.eq("cash out the door Cr", cash, w["cash_cr"], CR_TOL)
    c.eq("addressable Cr", addressable, w["addressable_cr"], CR_TOL)

    # -- C: H1 decomposition ----------------------------------------------
    print("\nQ11-Q13  H1 decomposition")
    import pandas as pd
    from src.analysis import h1_decomposition as H

    population = pd.read_parquet(REPO_ROOT / "data" / "processed" / "h1_population.parquet")
    sql_raw = dict((m, (n, r)) for m, n, r in query("""
        SELECT payment_method, count(*) FILTER (WHERE rto_flag),
               count(*) FILTER (WHERE rto_flag)::numeric / count(*)
        FROM analytics.vw_rto_base GROUP BY 1;"""))
    py_raw = H.raw_crosstab(population)
    c.eq("COD rto rate", sql_raw["COD"][1], py_raw["cod_rate"], RATE_TOL)
    c.eq("PREPAID rto rate", sql_raw["PREPAID"][1], py_raw["prepaid_rate"], RATE_TOL)
    c.eq("raw gap (pp)",
         (float(sql_raw["COD"][1]) - float(sql_raw["PREPAID"][1])) * 100,
         py_raw["estimate_pp"], RATE_TOL)

    # The three standardisations. Weighting choice moves this by 3.7pp, so all
    # three are checked rather than only the one being quoted.
    (cells, att, ate, atu), = query("""
        WITH cells AS (
            SELECT CASE WHEN s.pit_orders_delivered = 0 THEN '0'
                        WHEN s.pit_orders_delivered <= 2 THEN '1-2'
                        WHEN s.pit_orders_delivered <= 5 THEN '3-5'
                        WHEN s.pit_orders_delivered <= 15 THEN '6-15'
                        ELSE '16+' END AS tenure, b.geo_tier,
                   count(*) FILTER (WHERE b.payment_method = 'COD') n_cod,
                   count(*) FILTER (WHERE b.payment_method = 'PREPAID') n_prepaid,
                   avg((b.rto_flag)::int::numeric) FILTER (WHERE b.payment_method='COD') cod_rate,
                   avg((b.rto_flag)::int::numeric) FILTER (WHERE b.payment_method='PREPAID') prepaid_rate
            FROM analytics.vw_rto_base b
            JOIN analytics.fct_order o USING (order_id)
            JOIN analytics.fct_customer_state_at_session s ON s.session_id = o.session_id
            GROUP BY 1, 2),
        kept AS (SELECT n_cod, n_prepaid, (cod_rate - prepaid_rate) * 100 gap_pp
                 FROM cells WHERE n_cod >= 30 AND n_prepaid >= 30)
        SELECT count(*), sum(gap_pp*n_cod)/sum(n_cod),
               sum(gap_pp*(n_cod+n_prepaid))/sum(n_cod+n_prepaid),
               sum(gap_pp*n_prepaid)/sum(n_prepaid) FROM kept;""")
    py_strat = H.stratified(population)
    c.eq("stratified cells used", cells, py_strat["cells_used"])
    c.eq("stratified ATT  (cod-weighted)", att, py_strat["estimate_att_cod_weighted_pp"], RATE_TOL)
    c.eq("stratified ATE  (pooled)", ate, py_strat["estimate_ate_pooled_weighted_pp"], RATE_TOL)
    c.eq("stratified ATU  (prepaid-weighted)", atu, py_strat["estimate_atu_prepaid_weighted_pp"], RATE_TOL)

    # The H1 population itself must be the RTO denominator, not orders placed.
    (resolved,), = query("SELECT count(*) FROM analytics.vw_rto_base;")
    c.eq("H1 population = shipped AND NOT censored", resolved, len(population))

    # -- the leakage boundary is part of the contract ----------------------
    print("\nLK  the analyst role cannot reach the truth schema")
    c.checked += 1
    try:
        query("SELECT count(*) FROM truth.truth_order_probability;")
        c.failures.append("analyst could read truth.truth_order_probability")
        print("   [FAIL] analyst was NOT denied on schema truth")
    except Exception as exc:  # psycopg2.errors.InsufficientPrivilege
        code = getattr(exc, "pgcode", None)
        ok = code == "42501"
        print(f"   [{'ok ' if ok else 'FAIL'}] analyst denied on truth (SQLSTATE {code})")
        if not ok:
            c.failures.append(f"expected SQLSTATE 42501, got {code}: {exc}")

    print(f"\n{rule}")
    if c.failures:
        print(f"{c.checked} checks | {len(c.failures)} MISMATCH")
        for f in c.failures:
            print(f"   {f}")
        print(rule)
        return 1
    print(f"{c.checked} checks | all agree between SQL and Python")
    print(rule)
    return 0


if __name__ == "__main__":
    sys.exit(main())
