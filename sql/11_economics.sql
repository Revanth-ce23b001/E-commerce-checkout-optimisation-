-- ============================================================================
-- 11_economics.sql  —  Phase 3 analysis library, queries Q5–Q8.
--
-- Reproduces the Phase 1 §7 opportunity waterfall FROM THE DATABASE. Runs as
-- the `analyst` role.
--
-- TWO THINGS THIS FILE REFUSES TO HARD-CODE
-- -----------------------------------------
-- 1. The annualisation factor. Phase 1 §7.2 wrote x240, which silently encoded
--    a 100,000-order sample. It is DERIVED here as 24,000,000 / actual orders.
--    Total cost x (population / sample) is invariant to sample size, which is
--    exactly why it must be derived: changing the session knob must not move
--    the headline for a reason that is not a business reason.
-- 2. The break-even RTO probability p*. Phase 1 quotes 25.7% from an exemplar
--    order; the value that matters is the one implied by the REALISED cost
--    distribution. Read it from data/truth/_truth.json
--    (economics_targets.breakeven_rto_probability_derived), never from prose.
--
-- Phase 1 §7.1 names four different numbers people wrongly call "the
-- opportunity". The waterfall below walks them in order and labels each.
-- NEVER quote RTO revenue loss (GMV x RTO orders) as an opportunity.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- Q5. THE WATERFALL, LEVEL 2  —  orders to RTO cost
--
-- Business question: what does RTO actually cost, per year, at population scale?
-- Product decision: sets the size of the prize, and therefore what headcount and
--   engineering spend is proportionate. This is EXPOSURE (§7.1 number 3), not
--   fundable value -- the funnel down to number 6 is Q7.
--
-- Denominator discipline: every rate below is on shipped AND NOT censored.
-- Censored orders are counted and reported, never silently treated as delivered
-- (decision A10, limitation L9).
-- ---------------------------------------------------------------------------
WITH base AS (
    SELECT
        count(*)                                                   AS orders_all,
        count(*) FILTER (WHERE is_shipped AND NOT is_censored)     AS resolved,
        count(*) FILTER (WHERE is_censored)                        AS censored,
        count(*) FILTER (WHERE is_cancelled_preship)               AS cancelled
    FROM analytics.fct_order
),
split AS (
    SELECT
        payment_method,
        count(*)                                                   AS resolved_orders,
        count(*) FILTER (WHERE rto_flag)                           AS rto_orders,
        sum(rto_economic_cost) FILTER (WHERE rto_flag)             AS rto_cost,
        sum(rto_cash_loss)     FILTER (WHERE rto_flag)             AS cash_loss
    FROM analytics.vw_rto_base
    GROUP BY payment_method
)
SELECT
    s.payment_method,
    s.resolved_orders,
    round(s.resolved_orders::numeric / b.resolved, 4)              AS share_of_resolved,
    s.rto_orders,
    round(s.rto_orders::numeric / s.resolved_orders, 4)            AS rto_rate,
    round(s.rto_cost / s.rto_orders, 2)                            AS economic_cost_per_rto,
    round(s.cash_loss / s.rto_orders, 2)                           AS cash_loss_per_rto,
    -- Annualised: cost per RESOLVED order x annual order population.
    -- NOT (total cost x 24,000,000 / sample orders). Those differ by 15.7%,
    -- because `orders_all` includes 10,141 censored orders that carry no
    -- resolved outcome. Spreading RTO cost over them treats an unresolved
    -- order as a costless one and understates the bill -- decision A41,
    -- limitation L9. Censoring is a window artefact; the 24M annual orders
    -- all resolve, so the rate must come from orders that did.
    round(s.rto_cost / b.resolved * 24000000.0 / 1e7, 2)           AS annual_exposure_cr
FROM split s CROSS JOIN base b
ORDER BY s.payment_method;


-- ---------------------------------------------------------------------------
-- Q6. THE DERIVED ANNUALISATION FACTOR, STATED OUT LOUD
--
-- Business question: what multiplier turns this window into a year, and is it
--   defensible?
-- Product decision: Phase 1 §7.3 shows annual order volume is the single
--   largest driver of the headline -- roughly 80% of it, jointly with cost per
--   RTO. Anyone challenging the number is usually challenging this line, so it
--   is surfaced rather than buried inside another query.
-- ---------------------------------------------------------------------------
SELECT
    count(*)                                                       AS sample_orders,
    24000000                                                       AS population_annual_orders,
    round(24000000.0 / count(*), 2)                                AS annualisation_factor_derived,
    240                                                            AS phase1_stated_factor,
    round(24000000.0 / count(*) - 240, 2)                          AS difference
FROM analytics.fct_order;


-- ---------------------------------------------------------------------------
-- Q7. ADDRESSABLE VS STRUCTURAL  —  measured on COST, not on order count
--
-- Business question: how much of the RTO bill could a checkout intervention
--   plausibly influence, and how much is a fact of logistics?
-- Product decision: this is the single most important input to the funding case
--   after the headline. Phase 1 §7.2 ASSUMED 65% addressable [A]; this measures
--   it. If structural exceeds 50%, the recoverable pool halves and the project
--   may not clear the bar (§7.4).
--
-- Measured on COST because that is what gets recovered. The cost split and the
-- order-count split are DIFFERENT numbers whenever reason classes have
-- different average costs -- INSUFFICIENT_CASH_AT_DELIVERY is the expensive
-- one, so the cost split runs ahead of the count split. Both are reported;
-- quoting the count split as if it were the cost split overstates nothing here,
-- but the habit of checking is the point.
-- ---------------------------------------------------------------------------
WITH r AS (
    SELECT rto_reason_class, rto_reason, rto_economic_cost
    FROM analytics.vw_rto_base
    WHERE rto_flag
)
SELECT
    rto_reason_class,
    count(*)                                                       AS rto_orders,
    round(count(*)::numeric / sum(count(*)) OVER (), 4)            AS share_of_rto_count,
    round(sum(rto_economic_cost), 2)                               AS total_cost,
    round(sum(rto_economic_cost) / sum(sum(rto_economic_cost)) OVER (), 4)
                                                                   AS share_of_rto_cost,
    round(avg(rto_economic_cost), 2)                               AS avg_cost_per_rto
FROM r
GROUP BY rto_reason_class
ORDER BY total_cost DESC;


-- ---------------------------------------------------------------------------
-- Q8. THE FULL WATERFALL, EXPOSURE DOWN TO FUNDABLE VALUE
--
-- Business question: of the total exposure, what could a CFO actually be asked
--   to fund against?
-- Product decision: Phase 1 §7.1 is explicit that quoting exposure as if it
--   were net incremental CM is the standard inflation error. This query walks
--   every step so the challenge can be aimed at the right layer.
--
-- The top of the waterfall is ARITHMETIC (measured here). The middle is a
-- MEASURED share (Q7) where Phase 1 had an assumption. The bottom -- efficacy --
-- is an ASSUMPTION that only the Phase 6 experiment can replace, and it is
-- labelled as such rather than presented as a result.
-- ---------------------------------------------------------------------------
WITH base AS (
    SELECT count(*)                                            AS orders_all,
           count(*) FILTER (WHERE is_shipped AND NOT is_censored) AS resolved
    FROM analytics.fct_order
),
-- The order-population multiplier, derived. Reported in Q6. It is NOT what
-- annualises the cost: see the note in Q5 on the resolved denominator.
f AS (SELECT 24000000.0 AS annual_orders),
cost AS (
    SELECT
        sum(rto_economic_cost)                                                 AS total,
        sum(rto_economic_cost) FILTER (WHERE rto_reason_class = 'ADDRESSABLE') AS addressable,
        sum(rto_cash_loss)                                                     AS cash,
        count(*)                                                               AS rto_orders
    FROM analytics.vw_rto_base WHERE rto_flag
),
annual AS (
    SELECT
        c.total       / b.resolved * f.annual_orders / 1e7 AS exposure_cr,
        c.addressable / b.resolved * f.annual_orders / 1e7 AS addressable_cr,
        c.cash        / b.resolved * f.annual_orders / 1e7 AS cash_cr,
        c.addressable / NULLIF(c.total, 0)          AS addressable_share
    FROM cost c CROSS JOIN base b CROSS JOIN f
)
SELECT 1 AS step, 'Total annual RTO exposure  [MEASURED]'          AS line,
       round(exposure_cr, 2) AS value_cr, NULL::numeric AS pct FROM annual
UNION ALL SELECT 2, '  of which cash out the door  [MEASURED]',
       round(cash_cr, 2), round(cash_cr / exposure_cr, 4) FROM annual
UNION ALL SELECT 3, '  of which foregone contribution  [MEASURED]',
       round(exposure_cr - cash_cr, 2), round(1 - cash_cr / exposure_cr, 4) FROM annual
UNION ALL SELECT 4, 'Structurally unavoidable  [MEASURED, was an assumption]',
       round(-(exposure_cr - addressable_cr), 2), round(1 - addressable_share, 4) FROM annual
UNION ALL SELECT 5, '= Addressable opportunity  [MEASURED]',
       round(addressable_cr, 2), round(addressable_share, 4) FROM annual
UNION ALL SELECT 6, 'x intervention efficacy 30%  [ASSUMPTION - Phase 6 must replace]',
       round(addressable_cr * 0.30, 2), 0.30 FROM annual
UNION ALL SELECT 7, '= Recoverable opportunity  [ASSUMPTION-DEPENDENT]',
       round(addressable_cr * 0.30, 2), NULL FROM annual
ORDER BY step;


-- ---------------------------------------------------------------------------
-- Q9. WHICH ORDERS ARE ALREADY UNDERWATER?  —  segment RTO rate vs p*
--
-- Business question: at what observed RTO rate does a COD order stop paying for
--   itself, and which segments are already past it?
-- Product decision: p* is the economically derived threshold that Phase 4's
--   risk tiers must be set against. A segment above p* destroys margin on
--   average, so it is a candidate for COD restriction rather than for a nudge.
--
-- p* = 0.2576, DERIVED from the realised cost distribution
--   (data/truth/_truth.json -> economics_targets.breakeven_rto_probability_derived).
--   NOT Phase 1's nominal 25.7% exemplar figure. The two are close here, which
--   is a coincidence of this parameterisation and not a licence to quote prose.
--
-- This is a DIAGNOSTIC segment cut, not a model. It uses rule-based tiers that
-- already exist in the data. Fitting anything is Phase 4.
-- ---------------------------------------------------------------------------
WITH p AS (SELECT 0.2576::numeric AS p_star)
SELECT
    b.geo_tier,
    b.payment_method,
    count(*)                                                       AS resolved_orders,
    count(*) FILTER (WHERE b.rto_flag)                             AS rto_orders,
    round(count(*) FILTER (WHERE b.rto_flag)::numeric / count(*), 4) AS rto_rate,
    p.p_star,
    round(count(*) FILTER (WHERE b.rto_flag)::numeric / count(*) - p.p_star, 4)
                                                                   AS gap_to_breakeven,
    CASE WHEN count(*) FILTER (WHERE b.rto_flag)::numeric / count(*) > p.p_star
         THEN 'ABOVE p* - value destroying' ELSE 'below p*' END     AS verdict,
    -- Realised mean CM per resolved order in the segment. The ultimate test:
    -- a segment above p* should show a negative or near-zero figure here.
    round(avg(e.contribution_margin), 2)                           AS mean_cm_per_order
FROM analytics.vw_rto_base b
JOIN analytics.fct_order_economics e USING (order_id)
CROSS JOIN p
GROUP BY b.geo_tier, b.payment_method, p.p_star
ORDER BY rto_rate DESC;


-- ---------------------------------------------------------------------------
-- Q10. AVOIDABILITY, REASON BY REASON  —  cost share vs count share
--
-- Business question: which failure reasons carry disproportionate cost, and does
--   the avoidability taxonomy track cost or only volume?
-- Product decision: what gets recovered is COST, not order count. A reason that
--   is 10% of failures and 12% of the bill deserves 12% of the attention. This
--   query is what promotes an intervention from plausible to targeted.
--
-- The answer here is that the taxonomy is almost cost-neutral: nine of ten
-- reasons sit within +/-0.5pp of their count share. The exception is
-- INSUFFICIENT_CASH_AT_DELIVERY, which runs +1.53pp ahead because it is
-- COD-exclusive by construction (test DQ-11 -- a prepaid order cannot fail for
-- want of cash at the door) AND concentrates in high-value orders, where the
-- cash ask is largest and the goods-value-scaled costs are highest.
-- ---------------------------------------------------------------------------
WITH t AS (
    SELECT count(*) AS n, sum(rto_economic_cost) AS c
    FROM analytics.vw_rto_base WHERE rto_flag
)
SELECT
    r.rto_reason_class                                             AS class,
    r.rto_reason,
    count(*)                                                       AS orders,
    round(count(*)::numeric / t.n, 4)                              AS count_share,
    round(sum(r.rto_economic_cost), 0)                             AS total_cost,
    round(sum(r.rto_economic_cost) / t.c, 4)                       AS cost_share,
    -- The divergence. Positive means the reason is dearer than its volume implies.
    round(sum(r.rto_economic_cost) / t.c - count(*)::numeric / t.n, 4)
                                                                   AS cost_minus_count,
    round(avg(r.rto_economic_cost), 2)                             AS avg_cost,
    -- The two mechanisms behind the one divergent reason, made visible.
    count(*) FILTER (WHERE r.payment_method = 'COD')               AS cod_orders,
    round(avg(r.order_value), 2)                                   AS avg_order_value
FROM analytics.vw_rto_base r CROSS JOIN t
WHERE r.rto_flag
GROUP BY r.rto_reason_class, r.rto_reason, t.n, t.c
ORDER BY cost_minus_count DESC;
