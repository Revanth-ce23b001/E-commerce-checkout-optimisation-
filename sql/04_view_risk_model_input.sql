-- ============================================================================
-- 04_view_risk_model_input.sql  —  THE LEAKAGE FIREWALL (spec §4.4)
--
-- This is the ONLY table the risk-model training script may read from.
--
-- What matters here is what the view does NOT select. Four columns on
-- dim_customer — hist_orders_final, hist_rto_rate_final, hist_cod_share_final
-- and clv_estimate — are end-of-window aggregates that INCLUDE THE CURRENT
-- ORDER. They look exactly like innocent customer attributes. They are Stage-5
-- leakage, and they are the subtlest trap in the schema (CLAUDE.md rule 6).
-- They stay in dim_customer because dashboards need them. The firewall is here.
--
-- Also absent, deliberately: every fct_delivery_event field (Stage 4), every
-- fct_order_economics field (realised CM depends on the outcome), rto_reason,
-- rto_reason_class, ndr_code, actual_delivery_days, delivery_delay_days,
-- attempt_delay_days, delivery_attempts, order_status, is_delivered,
-- is_cancelled_preship, is_censored, outcome_resolved_date, analytics_segment,
-- and order_risk_tier_rule_based (the M2 baseline — decision A21).
--
-- LK-01 asserts this view's column list is a SUBSET of
-- params.leakage_guard.safe_feature_whitelist. LK-02 asserts none of
-- params.leakage_guard.hard_blocked appears.
--
-- M1 / M2 — spec §4.3, ONE VIEW, documented choice
-- ------------------------------------------------
-- payment_method, paid_via_switch and payment_attempt_count ARE selected. They
-- are Stage-3, legitimately known before shipment, and the M2 (post-selection)
-- model needs them. **M1 training MUST project these three away.**
--
-- One view rather than two, because two views drift: the day someone adds a
-- feature to the M1 view and forgets the M2 view, the two models stop being
-- comparable and nobody notices. A single view with three named columns to drop
-- keeps the difference explicit and in one place.
--
-- The ₹25.7% break-even threshold belongs to M2, not M1 (spec §4.3): it is
-- derived from the expected value of a *COD* order. Applying an M1 score to the
-- M2 threshold is a category error that would mis-tier a large share of traffic.
-- Phase 3 must use the DERIVED p* from _truth.json, not the nominal 25.7%.
--
-- Row filter: is_shipped AND NOT is_censored. Shipped is the RTO-rate
-- denominator (CLAUDE.md invariant 8); censored orders have no observable
-- outcome yet (decision A10), so training on them would teach the model that an
-- unresolved order is a delivered one.
-- ============================================================================

DROP VIEW IF EXISTS analytics.vw_risk_model_input;

CREATE VIEW analytics.vw_risk_model_input AS
SELECT
    -- keys
    s.session_id,
    s.customer_id,
    s.session_start_ts,

    -- Stage 1: point-in-time customer state. SAFE by construction — every value
    -- is as of session_start_ts, and an order counts only once its outcome had
    -- RESOLVED (decision A1, test LK-04).
    st.pit_tenure_days,
    st.pit_orders_placed,
    st.pit_orders_delivered,
    st.pit_orders_resolved,
    st.pit_rto_count,
    st.pit_rto_rate_raw,
    st.pit_rto_rate_shrunk,
    st.pit_cod_orders,
    st.pit_cod_share,
    st.pit_prepaid_success_count,
    st.pit_payment_failure_count,
    st.pit_payment_failure_rate,
    st.pit_days_since_last_order,
    st.pit_avg_order_value,
    st.pit_is_new_customer,
    st.pit_has_clean_record,
    st.pit_has_history,               -- the A18 missing indicator
    st.pit_risk_tier_rule_based,      -- M1 baseline only (decision A21)

    -- Stage 1: immutable customer, geography, product and seller attributes
    c.has_saved_prepaid_instrument,
    c.age_bucket,
    c.acquisition_channel,
    g.geo_tier,
    g.serviceability_score,
    g.courier_reliability_score,
    g.cod_cultural_index,
    p.category,
    p.list_price,
    p.product_rating,
    p.review_count,
    p.is_returnable,
    sl.seller_rating,
    sl.seller_rating_count,
    sl.seller_sla_breach_rate,
    d.day_of_week,
    d.is_month_end_window,

    -- Stage 2: known during checkout, BEFORE payment-method selection.
    -- This is the decision moment the model has to work at.
    s.cart_size,
    s.cart_value,
    s.device_type,
    s.delivery_geography_id,
    s.estimated_delivery_days,
    s.address_completeness_score,
    o.order_value,
    o.discount_pct,
    EXTRACT(HOUR FROM s.session_start_ts)::SMALLINT AS hour_of_day,

    -- Stage 3: M2 ONLY. M1 training must project these three away.
    o.payment_method,
    o.paid_via_switch,
    s.payment_attempt_count,

    -- TARGET, and the denominator flag
    o.rto_flag,
    o.is_shipped
FROM analytics.fct_checkout_session   s
JOIN analytics.fct_customer_state_at_session st USING (session_id)
JOIN analytics.fct_order              o  USING (session_id)
JOIN analytics.dim_customer           c  ON o.customer_id            = c.customer_id
JOIN analytics.dim_product            p  ON o.product_id             = p.product_id
JOIN analytics.dim_seller             sl ON o.seller_id              = sl.seller_id
JOIN analytics.dim_geography          g  ON o.delivery_geography_id  = g.geography_id
JOIN analytics.dim_date               d  ON o.order_date             = d.date_id
WHERE o.is_shipped = TRUE
  AND o.is_censored = FALSE;

COMMENT ON VIEW analytics.vw_risk_model_input IS 'The only permitted risk-model training source. Excludes dim_customer.hist_*_final, clv_estimate, analytics_segment and every Stage-4/5 field. M1 must project away payment_method, paid_via_switch and payment_attempt_count. See spec 4.3/4.4.';

GRANT SELECT ON analytics.vw_risk_model_input TO analyst;
GRANT SELECT ON analytics.vw_risk_model_input TO validator;
