-- ============================================================================
-- 12_hypotheses.sql  —  Phase 3 analysis library, queries Q11–Q13.
--
-- H1: how much of the COD–RTO gap is causation, and how much is selection?
--
-- SQL can carry the first two of the four estimates — the raw crosstab and the
-- stratified cells. It cannot carry the logistic regression or the propensity
-- match; those live in `src/analysis/h1_decomposition.py`. That division is
-- deliberate and is itself a finding: **the two estimates a warehouse can
-- produce on its own are the two that overstate the effect most.** An analyst
-- with SQL alone would report 17.73pp or 14.59pp, against a truth of 9.99pp.
--
-- Denominator: shipped AND NOT censored, via analytics.vw_rto_base.
-- Runs as the `analyst` role, which is denied on schema `truth` — so nothing
-- here can see the answer it is trying to estimate. That is the point.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- Q11. RAW CROSSTAB  —  the comparison anyone runs first
--
-- Business question: do COD orders come back more often than prepaid ones?
-- Product decision: NONE, on its own. This number is the reason the rest of the
--   file exists. Acting on it would mean pricing a COD fee against a 17.73pp
--   effect that is 1.77x the truth.
--
-- Report it, then immediately qualify it. It is the single most quotable and
-- most misleading figure in the project.
-- ---------------------------------------------------------------------------
SELECT
    payment_method,
    count(*)                                                   AS resolved_orders,
    count(*) FILTER (WHERE rto_flag)                           AS rto_orders,
    round(count(*) FILTER (WHERE rto_flag)::numeric / count(*), 6) AS rto_rate
FROM analytics.vw_rto_base
GROUP BY payment_method
ORDER BY payment_method;


-- ---------------------------------------------------------------------------
-- Q12. STRATIFIED  —  RTO rate within tenure x geo cells
--
-- Business question: does the COD gap survive when we compare like with like?
-- Product decision: this is the first honest test of whether payment method is
--   a lever. If the gap vanished inside cells, COD would be a marker and not a
--   cause. It does not vanish — it shrinks.
--
-- Cells with fewer than 30 orders on either side are excluded rather than
-- smoothed: a 3-order cell contributes noise carrying the authority of a rate.
-- The excluded count is reported by the Python path, not hidden.
--
-- RECOMBINING THESE CELLS IS WHERE THE ERROR LIVES. The cell gaps must be
-- weighted by the COD distribution, not the prepaid one, because the estimand
-- is the effect on the TREATED — "what would have happened to these COD orders
-- had they been prepaid". Weighting by prepaid answers the mirror question and
-- lands 3.7pp lower, which looks closer to the truth for entirely the wrong
-- reason. The three weightings are computed side by side so the choice is
-- visible rather than defaulted.
-- ---------------------------------------------------------------------------
WITH cells AS (
    SELECT
        CASE WHEN s.pit_orders_delivered = 0 THEN '0'
             WHEN s.pit_orders_delivered <= 2 THEN '1-2'
             WHEN s.pit_orders_delivered <= 5 THEN '3-5'
             WHEN s.pit_orders_delivered <= 15 THEN '6-15'
             ELSE '16+' END                                      AS tenure,
        b.geo_tier,
        count(*) FILTER (WHERE b.payment_method = 'COD')         AS n_cod,
        count(*) FILTER (WHERE b.payment_method = 'PREPAID')     AS n_prepaid,
        avg((b.rto_flag)::int::numeric) FILTER (WHERE b.payment_method = 'COD')     AS cod_rate,
        avg((b.rto_flag)::int::numeric) FILTER (WHERE b.payment_method = 'PREPAID') AS prepaid_rate
    FROM analytics.vw_rto_base b
    JOIN analytics.fct_order o USING (order_id)
    JOIN analytics.fct_customer_state_at_session s ON s.session_id = o.session_id
    GROUP BY 1, 2
),
kept AS (
    SELECT *, (cod_rate - prepaid_rate) * 100 AS gap_pp
    FROM cells WHERE n_cod >= 30 AND n_prepaid >= 30
)
SELECT tenure, geo_tier, n_cod, n_prepaid,
       round(cod_rate, 4)     AS cod_rate,
       round(prepaid_rate, 4) AS prepaid_rate,
       round(gap_pp, 4)       AS gap_pp
FROM kept
ORDER BY gap_pp DESC;


-- ---------------------------------------------------------------------------
-- Q13. THE THREE STANDARDISATIONS, SIDE BY SIDE
--
-- Business question: what is the stratified estimate, once the cells are
--   recombined?
-- Product decision: fixes the estimand. ATT is the one to quote here, because
--   the policy question is about the COD orders we currently take.
-- ---------------------------------------------------------------------------
WITH cells AS (
    SELECT
        CASE WHEN s.pit_orders_delivered = 0 THEN '0'
             WHEN s.pit_orders_delivered <= 2 THEN '1-2'
             WHEN s.pit_orders_delivered <= 5 THEN '3-5'
             WHEN s.pit_orders_delivered <= 15 THEN '6-15'
             ELSE '16+' END                                      AS tenure,
        b.geo_tier,
        count(*) FILTER (WHERE b.payment_method = 'COD')         AS n_cod,
        count(*) FILTER (WHERE b.payment_method = 'PREPAID')     AS n_prepaid,
        avg((b.rto_flag)::int::numeric) FILTER (WHERE b.payment_method = 'COD')     AS cod_rate,
        avg((b.rto_flag)::int::numeric) FILTER (WHERE b.payment_method = 'PREPAID') AS prepaid_rate
    FROM analytics.vw_rto_base b
    JOIN analytics.fct_order o USING (order_id)
    JOIN analytics.fct_customer_state_at_session s ON s.session_id = o.session_id
    GROUP BY 1, 2
),
kept AS (
    SELECT n_cod, n_prepaid, (cod_rate - prepaid_rate) * 100 AS gap_pp
    FROM cells WHERE n_cod >= 30 AND n_prepaid >= 30
)
SELECT
    count(*)                                                        AS cells_used,
    round(sum(gap_pp * n_cod)              / sum(n_cod), 4)         AS att_cod_weighted,
    round(sum(gap_pp * (n_cod + n_prepaid))/ sum(n_cod + n_prepaid), 4) AS ate_pooled_weighted,
    round(sum(gap_pp * n_prepaid)          / sum(n_prepaid), 4)     AS atu_prepaid_weighted
FROM kept;
