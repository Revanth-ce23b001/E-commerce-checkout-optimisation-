-- ============================================================================
-- 10_funnel.sql  —  Phase 3 analysis library, queries Q1–Q4.
--
-- Runs as the `analyst` role. Reads only schema `analytics`. Nothing here needs
-- schema `truth`; if it did, that would be a finding about the query.
--
-- Every metric below is defined in Phase 1 §5.3 and is locked. The one that
-- matters most is the pair in Q1: checkout conversion and NET conversion.
-- Checkout conversion is the metric a checkout team is usually measured on.
-- Net conversion is the one the business actually banks. The gap between them
-- is this project's opening argument.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- Q1. THE FUNNEL, END TO END
--
-- Business question: of every 100 checkout sessions started, how many end in
--   an order, and how many end in goods the customer keeps and pays for?
-- Product decision: whether checkout optimisation should be measured on orders
--   or on delivered orders. If the two conversions differ materially, every
--   target, bonus and A/B readout pinned to checkout conversion is measuring
--   the wrong thing — an intervention can raise orders and lower net.
--
-- Denominator note: sessions started, throughout. Censored orders (shipped, not
-- yet resolved at window close) are counted separately and are NOT treated as
-- delivered — decision A10, limitation L9.
-- ---------------------------------------------------------------------------
WITH s AS (
    SELECT
        count(*)                                                    AS sessions,
        count(*) FILTER (WHERE address_completed)                   AS address_completed,
        count(*) FILTER (WHERE payment_page_reached)                AS payment_page_reached,
        count(*) FILTER (WHERE NOT checkout_abandoned)              AS orders,
        count(*) FILTER (WHERE final_payment_method = 'COD')        AS cod_orders,
        count(*) FILTER (WHERE final_payment_method = 'PREPAID')    AS prepaid_orders,
        count(*) FILTER (WHERE switched_to_cod_after_failure)       AS switched_to_cod
    FROM analytics.fct_checkout_session
),
o AS (
    SELECT
        count(*) FILTER (WHERE is_cancelled_preship)                AS cancelled_preship,
        count(*) FILTER (WHERE is_shipped)                          AS shipped,
        count(*) FILTER (WHERE is_censored)                         AS censored,
        count(*) FILTER (WHERE is_shipped AND NOT is_censored)      AS resolved,
        count(*) FILTER (WHERE is_delivered)                        AS delivered,
        count(*) FILTER (WHERE rto_flag)                            AS rto
    FROM analytics.fct_order
)
SELECT 'sessions started'          AS step, sessions        AS n, 1.0                                  AS pct_of_sessions FROM s
UNION ALL SELECT 'address completed',      address_completed,    address_completed::numeric / sessions    FROM s
UNION ALL SELECT 'payment page reached',   payment_page_reached, payment_page_reached::numeric / sessions FROM s
UNION ALL SELECT 'orders placed',          orders,               orders::numeric / sessions               FROM s
UNION ALL SELECT 'shipped',       (SELECT shipped   FROM o), (SELECT shipped   FROM o)::numeric / sessions FROM s
UNION ALL SELECT 'delivered',     (SELECT delivered FROM o), (SELECT delivered FROM o)::numeric / sessions FROM s;


-- ---------------------------------------------------------------------------
-- Q2. THE TWO CONVERSIONS, SIDE BY SIDE  (Phase 1 §5.2 identity)
--
-- Business question: how much of what checkout "converts" survives to delivery?
-- Product decision: sets the north-star metric. Also produces the CM/CSS
--   baseline every intervention in Phase 5 will be scored against.
--
-- CM/CSS is computed on realised contribution margin over sessions STARTED —
-- including sessions that never ordered, which contribute zero. That is the
-- point of the denominator: an intervention that wins orders by taking on
-- doomed ones shows up here as a loss.
-- ---------------------------------------------------------------------------
WITH s AS (SELECT count(*) AS sessions FROM analytics.fct_checkout_session),
o AS (
    SELECT
        count(*)                                               AS orders,
        count(*) FILTER (WHERE is_delivered)                   AS delivered,
        count(*) FILTER (WHERE is_shipped AND NOT is_censored) AS resolved,
        count(*) FILTER (WHERE rto_flag)                       AS rto,
        count(*) FILTER (WHERE is_censored)                    AS censored
    FROM analytics.fct_order
),
e AS (
    SELECT
        sum(contribution_margin)                                            AS total_cm,
        sum(contribution_margin) FILTER (WHERE o.is_delivered)              AS delivered_cm,
        sum(rto_economic_cost)   FILTER (WHERE o.rto_flag)                  AS rto_cost
    FROM analytics.fct_order_economics e
    JOIN analytics.fct_order o USING (order_id)
)
SELECT
    (SELECT sessions FROM s)                                              AS sessions,
    (SELECT orders   FROM o)                                              AS orders,
    round((SELECT orders FROM o)::numeric    / (SELECT sessions FROM s), 4) AS checkout_conversion,
    round((SELECT delivered FROM o)::numeric / (SELECT sessions FROM s), 4) AS net_conversion,
    -- The leak, in percentage points of session. This is the headline gap.
    round(((SELECT orders FROM o) - (SELECT delivered FROM o))::numeric
          / (SELECT sessions FROM s), 4)                                  AS conversion_leak,
    (SELECT censored FROM o)                                              AS censored_excluded,
    round((SELECT total_cm FROM e)     / (SELECT sessions FROM s), 2)     AS cm_per_session_started,
    round((SELECT delivered_cm FROM e) / (SELECT delivered FROM o), 2)    AS cm_per_delivered_order,
    round((SELECT rto_cost FROM e)     / (SELECT sessions FROM s), 2)     AS rto_drag_per_session,
    -- Censoring correction. 10,141 shipped orders had not resolved by window
    -- close, so they are neither delivered nor RTO and drag raw net conversion
    -- DOWN mechanically. This restates it on the resolved population: what net
    -- conversion would be if the censored orders resolved at the observed rate.
    -- Reported alongside, never instead of -- L9.
    round((SELECT orders FROM o)::numeric / (SELECT sessions FROM s)
          * ((SELECT resolved FROM o) - (SELECT rto FROM o))::numeric
          / (SELECT resolved FROM o), 4)                                  AS net_conversion_resolved_basis;


-- ---------------------------------------------------------------------------
-- Q3. WHERE SESSIONS DIE  —  abandon_step distribution
--
-- Business question: at which step does checkout lose people, and is any of it
--   a defect rather than a preference?
-- Product decision: separates *friction* from *choice*. PAYMENT_FAILURE
--   abandonment is a reliability defect with zero conversion downside to fix —
--   it is the cheapest intervention in the whole library if it is material.
--   ADDRESS abandonment is a form problem. FEE_REVEAL is expected to be absent
--   in the baseline because no shipping fee is charged; it is the branch that
--   lights up only if a fee intervention ships (decision A25).
-- ---------------------------------------------------------------------------
WITH totals AS (
    SELECT count(*)                                        AS sessions,
           count(*) FILTER (WHERE abandon_step IS NOT NULL) AS abandons
    FROM analytics.fct_checkout_session
)
SELECT
    coalesce(s.abandon_step, 'CONVERTED')                  AS step,
    count(*)                                               AS sessions,
    round(count(*)::numeric / t.sessions, 4)               AS pct_of_sessions,
    -- NULL on the CONVERTED row: a session that converted did not abandon, and
    -- giving it a share of the abandons would invent a denominator it is not in.
    CASE WHEN s.abandon_step IS NULL THEN NULL
         ELSE round(count(*)::numeric / NULLIF(t.abandons, 0), 4) END
                                                           AS pct_of_abandons
FROM analytics.fct_checkout_session s
CROSS JOIN totals t
GROUP BY s.abandon_step, t.sessions, t.abandons
ORDER BY sessions DESC;


-- ---------------------------------------------------------------------------
-- Q4. PAYMENT SUCCESS RATE  —  prepaid only
--
-- Business question: how often does a prepaid attempt fail, and how often does
--   that failure end as a COD order rather than as an abandoned session?
-- Product decision: sizes H11. If a large share of COD is *manufactured* by
--   payment failure rather than chosen, the top intervention is payment
--   reliability — retries, fallback rails, smart routing — which costs no
--   conversion and no margin.
--
-- Phase 1 §5.3: COD has no payment-success analogue. Never blend the two.
-- ---------------------------------------------------------------------------
SELECT
    count(*) FILTER (WHERE payment_attempt_count > 0)                  AS sessions_attempting_prepaid,
    sum(payment_attempt_count)                                         AS total_attempts,
    count(*) FILTER (WHERE final_payment_method = 'PREPAID')           AS prepaid_orders,
    round(count(*) FILTER (WHERE final_payment_method = 'PREPAID')::numeric
          / NULLIF(count(*) FILTER (WHERE payment_attempt_count > 0), 0), 4)
                                                                       AS prepaid_success_rate,
    count(*) FILTER (WHERE switched_to_cod_after_failure)              AS switched_to_cod,
    round(count(*) FILTER (WHERE switched_to_cod_after_failure)::numeric
          / NULLIF(count(*) FILTER (WHERE final_payment_method = 'COD'), 0), 4)
                                                                       AS pct_of_cod_from_failure,
    count(*) FILTER (WHERE abandon_step = 'PAYMENT_FAILURE')           AS abandoned_at_failure
FROM analytics.fct_checkout_session;
