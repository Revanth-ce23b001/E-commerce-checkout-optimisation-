-- ============================================================================
-- 03_views_core.sql  —  the three analyst-facing convenience views.
--
-- Unlike 04_view_risk_model_input.sql, these are NOT firewalled. They exist for
-- dashboards and diagnosis, and they deliberately expose outcome-derived
-- columns — realised CM, RTO reasons, delivery delay. That is the point: the
-- avoidability waterfall and the funnel diagnosis need exactly the fields the
-- risk model must never see.
--
-- Never train on these.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- vw_funnel — one row per session, the Branch-5 diagnosis.
--
-- The north-star metric is contribution margin per checkout session STARTED, so
-- the denominator is sessions, not orders. Non-converting sessions are the whole
-- reason this view exists.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS analytics.vw_funnel;

CREATE VIEW analytics.vw_funnel AS
SELECT
    s.session_id,
    s.customer_id,
    s.date_id,
    s.device_type,
    g.geo_tier,
    s.cart_size,
    s.estimated_delivery_days,
    s.address_completeness_score,
    s.checkout_started,
    s.address_completed,
    s.payment_page_reached,
    s.intended_payment_method,
    s.final_payment_method,
    s.switched_to_cod_after_failure,
    s.payment_attempt_count,
    s.checkout_abandoned,
    -- ADDRESS / PAYMENT_PAGE / FEE_REVEAL / PAYMENT_FAILURE (decision A25).
    -- FEE_REVEAL is absent in the baseline because shipping_fee_charged is 0 --
    -- it is the diagnosis waiting for the intervention, not a dead branch.
    s.abandon_step,
    s.order_id IS NOT NULL AS converted
FROM analytics.fct_checkout_session s
JOIN analytics.dim_geography g ON s.delivery_geography_id = g.geography_id;

COMMENT ON VIEW analytics.vw_funnel IS 'Session-grain funnel. Denominator for CM-per-session-started. Not for training.';


-- ---------------------------------------------------------------------------
-- vw_order_enriched — one row per order, joined to its economics.
--
-- 🔒 CONTAINS LEAKAGE BY DESIGN: realised contribution margin, RTO reasons and
-- delivery outcomes. Dashboards and the §7 waterfall need them.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS analytics.vw_order_enriched;

CREATE VIEW analytics.vw_order_enriched AS
SELECT
    o.order_id,
    o.session_id,
    o.customer_id,
    o.order_date,
    p.category,
    p.weight_band,
    sl.seller_tier,
    g.geo_tier,
    o.payment_method,
    o.paid_via_switch,
    o.quantity,
    o.gmv,
    o.discount_pct,
    o.order_value,
    o.order_status,
    o.is_cancelled_preship,
    o.is_shipped,
    o.is_censored,
    o.is_delivered,
    o.rto_flag,
    o.rto_reason,
    o.rto_reason_class,
    o.delivery_attempts,
    o.actual_delivery_days,
    o.delivery_delay_days,        -- NULL on every RTO, by definition (A8)
    o.outcome_resolved_date,
    e.net_revenue,
    e.cogs,
    e.total_variable_cost,
    e.contribution_margin,
    e.counterfactual_cm_if_delivered,
    e.rto_cash_loss,
    e.foregone_cm,
    e.rto_economic_cost
FROM analytics.fct_order o
JOIN analytics.fct_order_economics e USING (order_id)
JOIN analytics.dim_product   p  ON o.product_id            = p.product_id
JOIN analytics.dim_seller    sl ON o.seller_id             = sl.seller_id
JOIN analytics.dim_geography g  ON o.delivery_geography_id = g.geography_id;

COMMENT ON VIEW analytics.vw_order_enriched IS 'Order grain with realised economics. CONTAINS LEAKAGE by design. Never train on it.';


-- ---------------------------------------------------------------------------
-- vw_rto_base — the RTO population, on the correct denominator.
--
-- shipped AND NOT censored. Getting this wrong makes every RTO rate in the
-- project wrong, so it is defined once here rather than in each query.
-- A censored order is a real future outcome, not a delivered one (decision A10);
-- annualising over it understates the opportunity by ~15% (limitation L9).
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS analytics.vw_rto_base;

CREATE VIEW analytics.vw_rto_base AS
SELECT
    o.order_id,
    o.customer_id,
    o.order_date,
    p.category,
    g.geo_tier,
    o.payment_method,
    o.paid_via_switch,
    o.order_value,
    o.rto_flag,
    o.rto_reason,
    o.rto_reason_class,
    e.rto_economic_cost,
    e.rto_cash_loss,
    e.counterfactual_cm_if_delivered
FROM analytics.fct_order o
JOIN analytics.fct_order_economics e USING (order_id)
JOIN analytics.dim_product   p ON o.product_id            = p.product_id
JOIN analytics.dim_geography g ON o.delivery_geography_id = g.geography_id
WHERE o.is_shipped = TRUE
  AND o.is_censored = FALSE;

COMMENT ON VIEW analytics.vw_rto_base IS 'The RTO denominator: shipped AND NOT censored. Use for every RTO rate.';


GRANT SELECT ON analytics.vw_funnel, analytics.vw_order_enriched,
                analytics.vw_rto_base TO analyst;
GRANT SELECT ON analytics.vw_funnel, analytics.vw_order_enriched,
                analytics.vw_rto_base TO validator;
