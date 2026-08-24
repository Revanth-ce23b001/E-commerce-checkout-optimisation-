-- ============================================================================
-- 00_schema_analytics.sql
--
-- The twelve analyst-visible tables. Spec §3 is the data dictionary; this file
-- is the CONTRACT. Written before any generator code, per brief §20.1:
-- "Write sql/00_schema_analytics.sql and sql/01_schema_truth.sql FIRST -- the
--  schema is the contract."
--
-- Conventions (brief §11):
--   * NUMERIC for money. Never FLOAT -- rounding drift would corrupt the CM
--     reconciliation that EC-03..EC-06 test to the rupee.
--   * TIMESTAMP for events, DATE for calendar grain, JSONB for traces.
--   * Primary keys everywhere; foreign keys enforced on every relationship.
--   * CHECK constraints carry the business invariants, so a bad row cannot be
--     written at all -- not merely detected later by a validation test.
--
-- Indexes live in 02_indexes.sql. Views live in 03_views_core.sql and
-- 04_view_risk_model_input.sql (the leakage firewall).
--
-- LEAKAGE NOTE. Several columns here are deliberate traps and are commented as
-- such. They stay in the schema because dashboards need them; they are
-- firewalled out of analytics.vw_risk_model_input. Keeping the trap present and
-- the firewall explicit is the point -- see CLAUDE.md rule 6.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS analytics;
SET search_path TO analytics;

-- ---------------------------------------------------------------------------
-- dim_date  (spec §3.1)  -- 90 rows
-- ---------------------------------------------------------------------------
CREATE TABLE dim_date (
    date_id                 DATE         PRIMARY KEY,
    day_index               INT          NOT NULL,
    day_of_week             SMALLINT     NOT NULL,
    is_weekend              BOOLEAN      NOT NULL,
    day_of_month            SMALLINT     NOT NULL,
    is_month_end_window     BOOLEAN      NOT NULL,   -- day_of_month >= 26; COD liquidity effect
    is_salary_week          BOOLEAN      NOT NULL,   -- day_of_month <= 7
    demand_index            NUMERIC(6,4) NOT NULL,

    CONSTRAINT dim_date_day_index_range  CHECK (day_index >= 0),
    CONSTRAINT dim_date_dow_range        CHECK (day_of_week BETWEEN 1 AND 7),
    CONSTRAINT dim_date_dom_range        CHECK (day_of_month BETWEEN 1 AND 31),
    CONSTRAINT dim_date_demand_positive  CHECK (demand_index > 0)
);

-- ---------------------------------------------------------------------------
-- dim_geography  (spec §3.2)  -- 500 rows
-- ---------------------------------------------------------------------------
CREATE TABLE dim_geography (
    geography_id            VARCHAR(10)  PRIMARY KEY,
    pincode_prefix          VARCHAR(3)   NOT NULL,
    city_name               VARCHAR(40)  NOT NULL,
    state_name              VARCHAR(40)  NOT NULL,
    geo_tier                VARCHAR(8)   NOT NULL,
    serviceability_score    NUMERIC(4,3) NOT NULL,
    courier_reliability_score NUMERIC(4,3) NOT NULL,
    base_delivery_days      NUMERIC(3,1) NOT NULL,
    -- Exists so geography can drive COD WITHOUT being a pure trust proxy. If the
    -- only path from geography to COD ran through distrust, the analysis would
    -- trivially conclude "Tier-3 = low trust" -- the lazy conclusion blueprint
    -- §1.5 warns against.
    cod_cultural_index      NUMERIC(4,3) NOT NULL,
    forward_freight_base    NUMERIC(6,2) NOT NULL,

    CONSTRAINT dim_geo_tier_enum         CHECK (geo_tier IN ('METRO','TIER1','TIER2','TIER3')),
    CONSTRAINT dim_geo_serviceability    CHECK (serviceability_score BETWEEN 0 AND 1),
    CONSTRAINT dim_geo_courier           CHECK (courier_reliability_score BETWEEN 0 AND 1),
    CONSTRAINT dim_geo_cultural          CHECK (cod_cultural_index BETWEEN 0 AND 1),
    CONSTRAINT dim_geo_delivery_positive CHECK (base_delivery_days > 0),
    CONSTRAINT dim_geo_freight_positive  CHECK (forward_freight_base >= 0)
);

-- ---------------------------------------------------------------------------
-- dim_seller  (spec §3.3)  -- 1,200 rows
-- ---------------------------------------------------------------------------
CREATE TABLE dim_seller (
    seller_id               VARCHAR(12)  PRIMARY KEY,
    seller_name             VARCHAR(60)  NOT NULL,
    seller_tenure_days      INT          NOT NULL,
    seller_rating           NUMERIC(3,2) NOT NULL,
    seller_rating_count     INT          NOT NULL,
    seller_sla_breach_rate  NUMERIC(4,3) NOT NULL,
    seller_cancellation_rate NUMERIC(4,3) NOT NULL,
    fulfilment_model        VARCHAR(12)  NOT NULL,
    seller_tier             VARCHAR(10)  NOT NULL,

    CONSTRAINT dim_seller_tenure         CHECK (seller_tenure_days >= 0),
    CONSTRAINT dim_seller_rating_range   CHECK (seller_rating BETWEEN 1.0 AND 5.0),
    CONSTRAINT dim_seller_rating_count   CHECK (seller_rating_count >= 0),
    CONSTRAINT dim_seller_sla_range      CHECK (seller_sla_breach_rate BETWEEN 0 AND 1),
    CONSTRAINT dim_seller_cancel_range   CHECK (seller_cancellation_rate BETWEEN 0 AND 1),
    CONSTRAINT dim_seller_fulfilment     CHECK (fulfilment_model IN ('PLATFORM','SELLER_SHIP')),
    CONSTRAINT dim_seller_tier_enum      CHECK (seller_tier IN ('GOLD','SILVER','BRONZE'))
);

-- ---------------------------------------------------------------------------
-- dim_product  (spec §3.4)  -- 8,000 rows
-- ---------------------------------------------------------------------------
CREATE TABLE dim_product (
    product_id              VARCHAR(12)  PRIMARY KEY,
    seller_id               VARCHAR(12)  NOT NULL REFERENCES dim_seller(seller_id),
    category                VARCHAR(24)  NOT NULL,
    sub_category            VARCHAR(32)  NOT NULL,
    list_price              NUMERIC(10,2) NOT NULL,
    product_rating          NUMERIC(3,2) NOT NULL,
    review_count            INT          NOT NULL,
    base_discount_pct       NUMERIC(4,3) NOT NULL,
    cogs_ratio              NUMERIC(4,3) NOT NULL,
    weight_band             VARCHAR(8)   NOT NULL,
    shrink_rate             NUMERIC(4,3) NOT NULL,
    is_returnable           BOOLEAN      NOT NULL,

    CONSTRAINT dim_product_category      CHECK (category IN
        ('FASHION','BEAUTY','HOME_KITCHEN','MOBILE_ACC','ELECTRONICS','GROCERY_FMCG')),
    CONSTRAINT dim_product_price_positive CHECK (list_price > 0),
    CONSTRAINT dim_product_rating_range  CHECK (product_rating BETWEEN 1.0 AND 5.0),
    CONSTRAINT dim_product_reviews       CHECK (review_count >= 0),
    CONSTRAINT dim_product_discount      CHECK (base_discount_pct BETWEEN 0 AND 1),
    CONSTRAINT dim_product_cogs          CHECK (cogs_ratio BETWEEN 0 AND 1),
    CONSTRAINT dim_product_weight_band   CHECK (weight_band IN ('LIGHT','MEDIUM','HEAVY')),
    CONSTRAINT dim_product_shrink        CHECK (shrink_rate BETWEEN 0 AND 1)
);

-- ---------------------------------------------------------------------------
-- dim_customer  (spec §3.5)  -- 55,000 rows
--
-- !! THE SUBTLEST LEAKAGE TRAP IN THE SCHEMA (CLAUDE.md rule 6) !!
-- Every hist_*_final column and clv_estimate is an END-OF-WINDOW aggregate that
-- INCLUDES the current order. They look like innocent customer attributes.
-- They are Stage-5 leakage. They stay here because dashboards need them; the
-- firewall is analytics.vw_risk_model_input, which must not select them.
-- The risk model reads the point-in-time versions from
-- fct_customer_state_at_session instead. Test LK-02 enforces this.
-- ---------------------------------------------------------------------------
CREATE TABLE dim_customer (
    customer_id             VARCHAR(12)  PRIMARY KEY,
    signup_date             DATE         NOT NULL,
    tenure_days_at_window_start INT      NOT NULL,
    home_geography_id       VARCHAR(10)  NOT NULL REFERENCES dim_geography(geography_id),
    age_bucket              VARCHAR(10)  NOT NULL,
    acquisition_channel     VARCHAR(16)  NOT NULL,
    has_saved_prepaid_instrument BOOLEAN NOT NULL,

    -- Pre-window history. Generated FROM the latents (module 07) -- this is what
    -- creates the confounding, rather than fitting it after the fact.
    pre_window_orders       INT          NOT NULL,
    pre_window_delivered    INT          NOT NULL,
    pre_window_rto_count    INT          NOT NULL,
    pre_window_cod_orders   INT          NOT NULL,
    pre_window_prepaid_success INT       NOT NULL,
    pre_window_payment_failures INT      NOT NULL,

    -- vvv STAGE 5. LEAKAGE. Never in vw_risk_model_input. vvv
    hist_orders_final       INT,
    hist_rto_rate_final     NUMERIC(4,3),
    hist_cod_share_final    NUMERIC(4,3),
    clv_estimate            NUMERIC(10,2),   -- blocked from the model; allowed in the
                                             -- fairness/value overlay (blueprint §9.2)
    analytics_segment       VARCHAR(24),     -- tenure x COD 3x3 label; dashboard only
    -- ^^^ STAGE 5. LEAKAGE. ^^^

    CONSTRAINT dim_cust_age_bucket   CHECK (age_bucket IN ('18-24','25-34','35-44','45-54','55+')),
    CONSTRAINT dim_cust_channel      CHECK (acquisition_channel IN
        ('ORGANIC','PAID_SOCIAL','PAID_SEARCH','REFERRAL')),
    CONSTRAINT dim_cust_pre_nonneg   CHECK (
        pre_window_orders >= 0 AND pre_window_delivered >= 0 AND
        pre_window_rto_count >= 0 AND pre_window_cod_orders >= 0 AND
        pre_window_prepaid_success >= 0 AND pre_window_payment_failures >= 0),
    -- Brief §9.5 consistency constraints: enforce, do not hope.
    CONSTRAINT dim_cust_outcomes_fit CHECK (pre_window_delivered + pre_window_rto_count
                                            <= pre_window_orders),
    CONSTRAINT dim_cust_cod_fits     CHECK (pre_window_cod_orders <= pre_window_orders),
    CONSTRAINT dim_cust_prepaid_fits CHECK (pre_window_prepaid_success
                                            <= pre_window_orders - pre_window_cod_orders),
    CONSTRAINT dim_cust_rates_range  CHECK (
        (hist_rto_rate_final  IS NULL OR hist_rto_rate_final  BETWEEN 0 AND 1) AND
        (hist_cod_share_final IS NULL OR hist_cod_share_final BETWEEN 0 AND 1))
);

-- ---------------------------------------------------------------------------
-- fct_checkout_session  (spec §3.6)  -- ~147,059 rows
--
-- Sessions are generated SEPARATELY from orders. The north-star metric is
-- contribution margin per checkout session STARTED, so sessions that never
-- convert are part of the denominator and must exist as rows.
-- ---------------------------------------------------------------------------
CREATE TABLE fct_checkout_session (
    session_id              VARCHAR(16)  PRIMARY KEY,
    customer_id             VARCHAR(12)  NOT NULL REFERENCES dim_customer(customer_id),
    session_start_ts        TIMESTAMP    NOT NULL,
    date_id                 DATE         NOT NULL REFERENCES dim_date(date_id),
    device_type             VARCHAR(10)  NOT NULL,
    candidate_product_id    VARCHAR(12)  NOT NULL REFERENCES dim_product(product_id),
    cart_size               SMALLINT     NOT NULL,
    cart_value              NUMERIC(10,2) NOT NULL,
    delivery_geography_id   VARCHAR(10)  NOT NULL REFERENCES dim_geography(geography_id),
    estimated_delivery_days SMALLINT     NOT NULL,   -- SAFE Stage-2 risk feature
    address_completeness_score NUMERIC(4,3) NOT NULL, -- SAFE; the cheapest intervention

    -- Funnel
    checkout_started        BOOLEAN      NOT NULL DEFAULT TRUE,
    address_completed       BOOLEAN      NOT NULL,
    payment_page_reached    BOOLEAN      NOT NULL,
    intended_payment_method VARCHAR(8),                -- the FIRST choice; H11 numerator
    final_payment_method    VARCHAR(8),                -- NULL if abandoned
    switched_to_cod_after_failure BOOLEAN NOT NULL DEFAULT FALSE,  -- H11 primary metric
    payment_attempt_count   SMALLINT     NOT NULL DEFAULT 0,
    checkout_abandoned      BOOLEAN      NOT NULL,
    abandon_step            VARCHAR(24),               -- Branch-5 diagnosis
    order_id                VARCHAR(14),               -- FK added after fct_order exists

    CONSTRAINT ses_device_enum       CHECK (device_type IN ('ANDROID','IOS','WEB')),
    CONSTRAINT ses_cart_size_range   CHECK (cart_size BETWEEN 1 AND 5),
    CONSTRAINT ses_cart_value_nonneg CHECK (cart_value >= 0),
    CONSTRAINT ses_edd_positive      CHECK (estimated_delivery_days > 0),
    CONSTRAINT ses_address_range     CHECK (address_completeness_score BETWEEN 0 AND 1),
    CONSTRAINT ses_intended_enum     CHECK (intended_payment_method IN ('COD','PREPAID')),
    CONSTRAINT ses_final_enum        CHECK (final_payment_method IN ('COD','PREPAID')),
    CONSTRAINT ses_abandon_step_enum CHECK (abandon_step IN
        ('ADDRESS','PAYMENT_PAGE','PAYMENT_FAILURE','FEE_REVEAL')),
    CONSTRAINT ses_attempts_nonneg   CHECK (payment_attempt_count >= 0),
    -- An abandoned session has no order and no final method; a converted one has both.
    CONSTRAINT ses_abandon_coherent  CHECK (
        (checkout_abandoned = TRUE  AND order_id IS NULL AND final_payment_method IS NULL
                                    AND abandon_step IS NOT NULL) OR
        (checkout_abandoned = FALSE AND order_id IS NOT NULL AND final_payment_method IS NOT NULL
                                    AND abandon_step IS NULL)),
    -- A switch means they intended prepaid and ended on COD.
    CONSTRAINT ses_switch_coherent   CHECK (
        switched_to_cod_after_failure = FALSE OR
        (intended_payment_method = 'PREPAID' AND final_payment_method = 'COD')),
    -- Funnel monotonicity: you cannot reach payment without completing address.
    CONSTRAINT ses_funnel_monotone   CHECK (payment_page_reached = FALSE OR address_completed = TRUE)
);

-- ---------------------------------------------------------------------------
-- fct_customer_state_at_session  (spec §3.7)  -- 147,059 rows, 1:1 with sessions
--
-- THE LEAKAGE FIREWALL. Every column is point-in-time as of session_start_ts and
-- is SAFE by construction. A prior order counts here ONLY if its outcome had
-- RESOLVED before this timestamp -- outcomes take 4-25 days, so an order placed
-- three days ago has not resolved. Test LK-04 re-derives every snapshot
-- independently and asserts zero violations.
-- ---------------------------------------------------------------------------
CREATE TABLE fct_customer_state_at_session (
    session_id              VARCHAR(16)  PRIMARY KEY
                            REFERENCES fct_checkout_session(session_id),
    customer_id             VARCHAR(12)  NOT NULL REFERENCES dim_customer(customer_id),
    pit_tenure_days         INT          NOT NULL,
    pit_orders_placed       INT          NOT NULL,
    pit_orders_delivered    INT          NOT NULL,
    -- Decision A20: pit_orders_resolved was used in a formula but was never a
    -- column. It is the denominator of pit_rto_rate_raw and the trials input to
    -- empirical-Bayes shrinkage, so it is materialised rather than recomputed.
    pit_orders_resolved     INT          NOT NULL,
    pit_rto_count           INT          NOT NULL,
    pit_rto_rate_raw        NUMERIC(5,4),           -- NULL when nothing has resolved
    -- Decision A18 exception: empirical-Bayes shrinkage at n=0 RETURNS the
    -- declared prior by construction. That is a computed value, not an imputed
    -- one, so this column is never NULL. LK-06 asserts the prior is the declared
    -- constant and not something derived from the generated population.
    pit_rto_rate_shrunk     NUMERIC(5,4) NOT NULL,  -- EB, k=8. Preferred risk feature
    pit_cod_orders          INT          NOT NULL,
    -- vvv Decision A18 (RULED): DO NOT IMPUTE. vvv
    -- These are NULL for a customer with no prior orders. Imputing 0.62 into a
    -- column that is then multiplied by +2.20 would manufacture a signal that
    -- does not exist; imputing 0 would assert evidence that was never observed.
    -- The customer's COD share is UNKNOWN, and the table says so. Analyst-side
    -- models use the missing-indicator pattern via pit_has_history.
    pit_cod_share           NUMERIC(5,4),           -- habit feature (H2/H4)
    pit_prepaid_success_count INT        NOT NULL,
    pit_payment_failure_count INT        NOT NULL,
    pit_payment_failure_rate NUMERIC(5,4),
    pit_days_since_last_order INT,
    pit_avg_order_value     NUMERIC(10,2),
    -- The missing indicator itself.
    pit_has_history         BOOLEAN      NOT NULL,  -- pit_orders_placed > 0
    -- ^^^ Decision A18. ^^^
    pit_is_new_customer     BOOLEAN      NOT NULL,  -- pit_orders_delivered = 0
    pit_has_clean_record    BOOLEAN      NOT NULL,  -- >=3 delivered AND 0 RTO
    -- Decision A21: the blueprint §9.3 rule baseline is "payment method + prior
    -- RTO + tenure", but payment_method is Stage 3 and cannot appear in a
    -- Stage-2 feature. This column therefore carries the M1 (pre-selection)
    -- baseline: prior RTO + tenure only. The M2 version, which does include
    -- payment_method, lives on fct_order as order_risk_tier_rule_based and is
    -- hard-blocked from the M1 feature set.
    pit_risk_tier_rule_based VARCHAR(8)  NOT NULL,

    CONSTRAINT pit_counts_nonneg   CHECK (
        pit_orders_placed >= 0 AND pit_orders_delivered >= 0 AND
        pit_orders_resolved >= 0 AND pit_rto_count >= 0 AND
        pit_cod_orders >= 0 AND pit_prepaid_success_count >= 0 AND
        pit_payment_failure_count >= 0),
    CONSTRAINT pit_resolved_fits   CHECK (pit_orders_resolved <= pit_orders_placed),
    CONSTRAINT pit_outcomes_fit    CHECK (pit_orders_delivered + pit_rto_count
                                          <= pit_orders_resolved),
    CONSTRAINT pit_cod_fits        CHECK (pit_cod_orders <= pit_orders_placed),
    CONSTRAINT pit_rates_range     CHECK (
        (pit_rto_rate_raw         IS NULL OR pit_rto_rate_raw         BETWEEN 0 AND 1) AND
        pit_rto_rate_shrunk BETWEEN 0 AND 1 AND
        (pit_cod_share            IS NULL OR pit_cod_share            BETWEEN 0 AND 1) AND
        (pit_payment_failure_rate IS NULL OR pit_payment_failure_rate BETWEEN 0 AND 1)),
    -- Decision A18: missingness is not optional or accidental. A customer with no
    -- prior orders MUST carry NULL on the history-derived rates, and one with
    -- prior orders MUST carry a value. This makes "unknown" a checked state
    -- rather than something a generator bug could silently fill in.
    CONSTRAINT pit_history_flag    CHECK (pit_has_history = (pit_orders_placed > 0)),
    CONSTRAINT pit_missing_iff_no_history CHECK (
        (pit_cod_share IS NULL) = (pit_orders_placed = 0)),
    -- pit_avg_order_value is deliberately NOT in the constraint above.
    -- It was, and the live load rejected the very first row -- correctly.
    -- Decision A30 came later and scoped this column to IN-WINDOW orders only:
    -- dim_customer has no pre-window order-value column, so a customer with five
    -- pre-window orders and none yet in the window has history AND a NULL average.
    -- That condition is not expressible against pit_orders_placed, so it is
    -- asserted in the generator and recorded as limitation L2 rather than faked
    -- here with a constraint that would only look like a guarantee.
    CONSTRAINT pit_raw_rate_missing CHECK (
        (pit_rto_rate_raw IS NULL) = (pit_orders_resolved = 0)),
    CONSTRAINT pit_new_coherent    CHECK (pit_is_new_customer = (pit_orders_delivered = 0)),
    CONSTRAINT pit_clean_coherent  CHECK (pit_has_clean_record =
                                          (pit_orders_delivered >= 3 AND pit_rto_count = 0)),
    CONSTRAINT pit_tier_enum       CHECK (pit_risk_tier_rule_based IN ('LOW','MED','HIGH')),
    CONSTRAINT pit_recency_nonneg  CHECK (pit_days_since_last_order IS NULL
                                          OR pit_days_since_last_order >= 0)
);

-- ---------------------------------------------------------------------------
-- fct_checkout_event  (spec §3.8)  -- ~700,000 rows
--
-- Decision A12: this is a PROJECTION, not an independent stochastic process.
-- It is emitted deterministically from resolved session state at the end of
-- module 12, once conversion and abandon_step are known: walk each session's
-- realised path and write one row per step reached, timestamps interpolated
-- from session_start_ts. No new randomness, no new parameters, no new substream.
-- ---------------------------------------------------------------------------
CREATE TABLE fct_checkout_event (
    event_id                BIGSERIAL    PRIMARY KEY,
    session_id              VARCHAR(16)  NOT NULL
                            REFERENCES fct_checkout_session(session_id),
    event_seq               SMALLINT     NOT NULL,
    event_ts                TIMESTAMP    NOT NULL,
    event_name              VARCHAR(28)  NOT NULL,
    event_detail            JSONB,
    seconds_since_session_start INT      NOT NULL,

    CONSTRAINT evt_seq_positive   CHECK (event_seq >= 1),
    CONSTRAINT evt_dwell_nonneg   CHECK (seconds_since_session_start >= 0),
    CONSTRAINT evt_name_enum      CHECK (event_name IN
        ('CHECKOUT_STARTED','ADDRESS_COMPLETED','PAYMENT_PAGE_REACHED','FEE_DISPLAYED',
         'METHOD_SELECTED','PAYMENT_ATTEMPTED','METHOD_SWITCHED','ORDER_PLACED','ABANDONED')),
    CONSTRAINT evt_unique_seq     UNIQUE (session_id, event_seq)
);

-- ---------------------------------------------------------------------------
-- fct_payment_attempt  (spec §3.9)  -- ~78,000 rows
--
-- The H11 evidence base. Without the attempt-level grain there is no way to
-- separate COD-by-preference from COD-by-coercion.
-- ---------------------------------------------------------------------------
CREATE TABLE fct_payment_attempt (
    payment_attempt_id      BIGSERIAL    PRIMARY KEY,
    session_id              VARCHAR(16)  NOT NULL
                            REFERENCES fct_checkout_session(session_id),
    attempt_seq             SMALLINT     NOT NULL,
    payment_rail            VARCHAR(16)  NOT NULL,
    attempt_ts              TIMESTAMP    NOT NULL,
    attempt_amount          NUMERIC(10,2) NOT NULL,
    payment_success         BOOLEAN      NOT NULL,
    failure_reason          VARCHAR(32),
    is_retry                BOOLEAN      NOT NULL,
    post_failure_action     VARCHAR(20),

    CONSTRAINT pay_seq_range      CHECK (attempt_seq BETWEEN 1 AND 3),
    CONSTRAINT pay_rail_enum      CHECK (payment_rail IN
        ('UPI_INTENT','UPI_COLLECT','CARD','NETBANKING','WALLET')),
    CONSTRAINT pay_amount_nonneg  CHECK (attempt_amount >= 0),
    CONSTRAINT pay_reason_enum    CHECK (failure_reason IN
        ('BANK_DECLINE','TIMEOUT','OTP_FAILURE','INSUFFICIENT_FUNDS','PSP_DOWNTIME','USER_CANCELLED')),
    CONSTRAINT pay_action_enum    CHECK (post_failure_action IN
        ('RETRY_SAME','SWITCH_RAIL','SWITCH_TO_COD','ABANDON')),
    -- A success has no failure reason and no post-failure action, and vice versa.
    CONSTRAINT pay_outcome_coherent CHECK (
        (payment_success = TRUE  AND failure_reason IS NULL AND post_failure_action IS NULL) OR
        (payment_success = FALSE AND failure_reason IS NOT NULL)),
    CONSTRAINT pay_retry_coherent CHECK (is_retry = (attempt_seq > 1)),
    CONSTRAINT pay_unique_seq     UNIQUE (session_id, attempt_seq)
);

-- ---------------------------------------------------------------------------
-- fct_order  (spec §3.10)  -- 100,000 rows
--
-- is_shipped IS THE RTO-RATE DENOMINATOR (CLAUDE.md invariant 8). Pre-ship
-- cancellations are removed from the RTO population BEFORE the RTO draw.
--
-- Decision A10 (censoring): orders whose outcome would resolve after the last
-- day of the window carry is_censored = TRUE and NULL on every outcome column.
-- RTO-rate denominators are shipped AND NOT censored. DQ-14 requires censoring
-- to be PRESENT (>=3% of late-window orders) so that blueprint §11 can
-- DEMONSTRATE maturation bias rather than assert it.
-- ---------------------------------------------------------------------------
CREATE TABLE fct_order (
    order_id                VARCHAR(14)  PRIMARY KEY,
    session_id              VARCHAR(16)  NOT NULL UNIQUE
                            REFERENCES fct_checkout_session(session_id),
    customer_id             VARCHAR(12)  NOT NULL REFERENCES dim_customer(customer_id),
    product_id              VARCHAR(12)  NOT NULL REFERENCES dim_product(product_id),
    seller_id               VARCHAR(12)  NOT NULL REFERENCES dim_seller(seller_id),
    delivery_geography_id   VARCHAR(10)  NOT NULL REFERENCES dim_geography(geography_id),
    order_ts                TIMESTAMP    NOT NULL,
    order_date              DATE         NOT NULL REFERENCES dim_date(date_id),
    quantity                SMALLINT     NOT NULL,

    -- Money. Decision A5: the 1,000-rupee headline is mean GMV PER ORDER.
    -- order_value = gmv - discount_amount, and its mean emerges at ~920.
    gmv                     NUMERIC(10,2) NOT NULL,
    discount_amount         NUMERIC(10,2) NOT NULL,
    discount_pct            NUMERIC(5,4) NOT NULL,   -- SAFE Stage-2 risk feature
    order_value             NUMERIC(10,2) NOT NULL,  -- SAFE Stage-2 risk feature
    shipping_fee_charged    NUMERIC(6,2) NOT NULL,
    cod_fee_charged         NUMERIC(6,2) NOT NULL,   -- 0 in baseline; INTERVENTION LEVER

    payment_method          VARCHAR(8)   NOT NULL,
    payment_rail            VARCHAR(16),             -- NULL for COD (DQ-13)
    paid_via_switch         BOOLEAN      NOT NULL,   -- H11; RTO modifier (deviation D5)
    estimated_delivery_days SMALLINT     NOT NULL,
    promised_delivery_date  DATE         NOT NULL,

    -- Decision A21 -- the M2 (post-selection) rule baseline. Contains
    -- payment_method, so it is HARD-BLOCKED from the M1 feature set.
    order_risk_tier_rule_based VARCHAR(8) NOT NULL,

    -- vvv STAGE 3/4/5. LEAKAGE. Never in vw_risk_model_input. vvv
    order_status            VARCHAR(20)  NOT NULL,
    is_cancelled_preship    BOOLEAN      NOT NULL,
    cancel_actor            VARCHAR(10),
    is_shipped              BOOLEAN      NOT NULL,   -- THE RTO-RATE DENOMINATOR
    is_censored             BOOLEAN      NOT NULL,   -- decision A10
    actual_delivery_days    SMALLINT,
    -- Decision A8: this is a DIAGNOSTIC column (H6), legitimately NULL on every
    -- RTO order because the parcel never arrived. It is NEVER a model feature.
    -- The Stage-2 shock input is a DIFFERENT variable -- attempt_delay_days on
    -- fct_delivery_event -- which exists for every shipped order. The spec
    -- collided the two names; they are separated here.
    delivery_delay_days     SMALLINT,
    delivery_attempts       SMALLINT,
    is_delivered            BOOLEAN,
    rto_flag                BOOLEAN,                 -- THE TARGET
    rto_reason              VARCHAR(40),
    rto_reason_class        VARCHAR(16),
    outcome_resolved_date   DATE,
    -- ^^^ STAGE 3/4/5. LEAKAGE. ^^^

    CONSTRAINT ord_qty_range        CHECK (quantity BETWEEN 1 AND 3),
    CONSTRAINT ord_money_nonneg     CHECK (
        gmv >= 0 AND discount_amount >= 0 AND order_value >= 0 AND
        shipping_fee_charged >= 0 AND cod_fee_charged >= 0),
    CONSTRAINT ord_discount_fits    CHECK (discount_amount <= gmv),
    CONSTRAINT ord_value_identity   CHECK (order_value = gmv - discount_amount),
    CONSTRAINT ord_discount_range   CHECK (discount_pct BETWEEN 0 AND 1),
    CONSTRAINT ord_method_enum      CHECK (payment_method IN ('COD','PREPAID')),
    CONSTRAINT ord_rail_enum        CHECK (payment_rail IN
        ('UPI_INTENT','UPI_COLLECT','CARD','NETBANKING','WALLET')),
    -- DQ-13: payment_rail IS NULL if and only if the order is COD.
    CONSTRAINT ord_rail_iff_prepaid CHECK ((payment_rail IS NULL) = (payment_method = 'COD')),
    CONSTRAINT ord_switch_is_cod    CHECK (paid_via_switch = FALSE OR payment_method = 'COD'),
    CONSTRAINT ord_status_enum      CHECK (order_status IN
        ('PLACED','CANCELLED_PRESHIP','SHIPPED','DELIVERED','RTO')),
    CONSTRAINT ord_cancel_actor_enum CHECK (cancel_actor IN ('CUSTOMER','SELLER','SYSTEM')),
    CONSTRAINT ord_tier_enum        CHECK (order_risk_tier_rule_based IN ('LOW','MED','HIGH')),
    CONSTRAINT ord_edd_positive     CHECK (estimated_delivery_days > 0),
    CONSTRAINT ord_promise_after    CHECK (promised_delivery_date >= order_date),
    -- DQ-06
    CONSTRAINT ord_resolved_after   CHECK (outcome_resolved_date IS NULL
                                           OR outcome_resolved_date >= order_date),
    -- DQ-09: a pre-ship cancellation never ships and never RTOs.
    CONSTRAINT ord_cancel_coherent  CHECK (is_cancelled_preship = FALSE OR
        (is_shipped = FALSE AND rto_flag IS NOT TRUE AND cancel_actor IS NOT NULL)),
    -- DQ-08: an RTO implies shipped and not delivered.
    CONSTRAINT ord_rto_coherent     CHECK (rto_flag IS NOT TRUE OR
        (is_shipped = TRUE AND is_delivered = FALSE)),
    -- An outcome exists only for a shipped order.
    CONSTRAINT ord_outcome_needs_ship CHECK (is_shipped = TRUE OR
        (rto_flag IS NULL AND is_delivered IS NULL AND actual_delivery_days IS NULL)),
    -- Decision A10: a censored order carries NO outcome at all.
    CONSTRAINT ord_censored_null    CHECK (is_censored = FALSE OR
        (rto_flag IS NULL AND is_delivered IS NULL AND
         actual_delivery_days IS NULL AND outcome_resolved_date IS NULL)),
    -- ...and a shipped, uncensored order MUST have one. This is what makes
    -- "shipped AND NOT censored" a complete RTO denominator.
    CONSTRAINT ord_resolved_complete CHECK (
        NOT (is_shipped = TRUE AND is_censored = FALSE) OR
        (rto_flag IS NOT NULL AND is_delivered IS NOT NULL
         AND outcome_resolved_date IS NOT NULL)),
    CONSTRAINT ord_terminal_exclusive CHECK (rto_flag IS NULL OR is_delivered IS NULL
                                             OR rto_flag <> is_delivered),
    -- A reason exists only on an RTO. DQ-11's COD gate is enforced below.
    CONSTRAINT ord_reason_needs_rto CHECK (rto_flag IS TRUE OR
        (rto_reason IS NULL AND rto_reason_class IS NULL)),
    CONSTRAINT ord_reason_class_enum CHECK (rto_reason_class IN ('ADDRESSABLE','STRUCTURAL')),
    -- DQ-11: INSUFFICIENT_CASH_AT_DELIVERY can only happen on a COD order.
    CONSTRAINT ord_cash_reason_cod  CHECK (rto_reason <> 'INSUFFICIENT_CASH_AT_DELIVERY'
                                           OR payment_method = 'COD'),
    CONSTRAINT ord_attempts_range   CHECK (delivery_attempts IS NULL
                                           OR delivery_attempts BETWEEN 0 AND 3)
);

-- Close the 0..1 session -> order loop now that fct_order exists.
ALTER TABLE fct_checkout_session
    ADD CONSTRAINT ses_order_fk FOREIGN KEY (order_id) REFERENCES fct_order(order_id)
    DEFERRABLE INITIALLY DEFERRED;

-- NOTE on DQ-05 (order_ts >= session_start_ts): PostgreSQL CHECK constraints
-- cannot span tables, so this invariant is not enforceable in DDL. It is
-- asserted by validation test DQ-05, which re-derives the comparison across the
-- join. Deliberately left to the harness rather than faked with a trigger.

-- ---------------------------------------------------------------------------
-- fct_delivery_event  (spec §3.11)  -- ~232,000 rows
--
-- Decision A8: attempt_delay_days lives HERE, not on fct_order. It is the days
-- between promised_delivery_date and the delivery attempt, it exists for every
-- shipped order whether it RTOs or not, and it is what the Stage-2 shock
-- coefficient delta_2 = 0.22 multiplies (read from the attempt_number = 1 row).
-- It is Stage-4 information and is HARD-BLOCKED from every model.
-- ---------------------------------------------------------------------------
CREATE TABLE fct_delivery_event (
    delivery_event_id       BIGSERIAL    PRIMARY KEY,
    order_id                VARCHAR(14)  NOT NULL REFERENCES fct_order(order_id),
    event_seq               SMALLINT     NOT NULL,
    event_ts                TIMESTAMP    NOT NULL,
    event_name              VARCHAR(28)  NOT NULL,
    attempt_number          SMALLINT,
    attempt_delay_days      SMALLINT,    -- decision A8; NULL on non-attempt events
    ndr_code                VARCHAR(32),
    courier_partner         VARCHAR(20)  NOT NULL,

    CONSTRAINT dlv_seq_positive   CHECK (event_seq >= 1),
    CONSTRAINT dlv_name_enum      CHECK (event_name IN
        ('ORDER_PLACED','CANCELLED_PRESHIP','DISPATCHED','IN_TRANSIT','OUT_FOR_DELIVERY',
         'DELIVERY_ATTEMPT_FAILED','DELIVERED','RTO_INITIATED','RTO_RECEIVED')),
    CONSTRAINT dlv_attempt_range  CHECK (attempt_number IS NULL
                                         OR attempt_number BETWEEN 1 AND 3),
    -- Decision A22: the ndr_code enumeration, which the spec never gave.
    CONSTRAINT dlv_ndr_enum       CHECK (ndr_code IN
        ('CUSTOMER_REFUSED','CUSTOMER_UNREACHABLE','ADDRESS_INCOMPLETE','CASH_NOT_READY',
         'COURIER_OPERATIONAL','CUSTOMER_UNAVAILABLE','OUT_OF_DELIVERY_AREA',
         'ATTEMPT_OUTSIDE_WINDOW','OTHER')),
    -- An NDR code only exists on a failed attempt.
    CONSTRAINT dlv_ndr_needs_fail CHECK (ndr_code IS NULL
                                         OR event_name = 'DELIVERY_ATTEMPT_FAILED'),
    CONSTRAINT dlv_unique_seq     UNIQUE (order_id, event_seq)
);

-- ---------------------------------------------------------------------------
-- fct_order_economics  (spec §3.12)  -- 100,000 rows, 1:1 with orders
--
-- Every cost line separated, because costs are OUTCOME-CONDITIONAL and the whole
-- opportunity model depends on knowing which line fires when.
-- ALL columns here are Stage-5 LEAKAGE: realised CM depends on the outcome.
--
-- Decision A23:
--   * cogs_value is the COUNTERFACTUAL goods value (as if delivered). It exists
--     because on an RTO net_revenue is zero and therefore cogs is zero -- yet
--     shrink and working-capital costs are still proportional to goods value.
--   * Pre-ship cancelled orders carry ZERO on every line: nothing dispatched,
--     nothing collected, prepaid refunded.
-- ---------------------------------------------------------------------------
CREATE TABLE fct_order_economics (
    order_id                VARCHAR(14)  PRIMARY KEY REFERENCES fct_order(order_id),
    gmv                     NUMERIC(10,2) NOT NULL,
    discount_cost           NUMERIC(10,2) NOT NULL,
    shipping_fee_revenue    NUMERIC(6,2) NOT NULL,
    cod_fee_revenue         NUMERIC(6,2) NOT NULL,
    net_revenue             NUMERIC(10,2) NOT NULL,   -- 0 unless delivered
    cogs                    NUMERIC(10,2) NOT NULL,   -- 0 unless delivered
    cogs_value              NUMERIC(10,2) NOT NULL,   -- decision A23: counterfactual basis

    forward_shipping_cost   NUMERIC(6,2) NOT NULL,    -- on dispatch, always
    reverse_shipping_cost   NUMERIC(6,2) NOT NULL,    -- RTO only
    packaging_cost          NUMERIC(6,2) NOT NULL,    -- on dispatch, always
    payment_processing_fee  NUMERIC(6,2) NOT NULL,    -- prepaid only
    cod_handling_cost       NUMERIC(6,2) NOT NULL,    -- COD + delivered
    cod_failed_attempt_cost NUMERIC(6,2) NOT NULL,    -- COD RTO only
    reverse_handling_cost   NUMERIC(6,2) NOT NULL,    -- RTO only
    shrink_cost             NUMERIC(8,2) NOT NULL,    -- RTO only
    support_ndr_cost        NUMERIC(6,2) NOT NULL,    -- RTO high / delivered low
    working_capital_cost    NUMERIC(6,2) NOT NULL,    -- RTO only
    ops_allocation_cost     NUMERIC(6,2) NOT NULL,    -- delivered only

    total_variable_cost     NUMERIC(10,2) NOT NULL,
    contribution_margin     NUMERIC(10,2) NOT NULL,
    -- Stored so the blueprint §7 waterfall is reproducible from the table with
    -- no re-derivation: the CM formula re-run with is_delivered = TRUE.
    counterfactual_cm_if_delivered NUMERIC(10,2) NOT NULL,
    rto_cash_loss           NUMERIC(10,2) NOT NULL,
    foregone_cm             NUMERIC(10,2) NOT NULL,
    rto_economic_cost       NUMERIC(10,2) NOT NULL,

    -- DQ-04: no negative prices, values or costs. CM may legitimately be
    -- negative; individual cost LINES may not.
    CONSTRAINT eco_costs_nonneg CHECK (
        gmv >= 0 AND discount_cost >= 0 AND shipping_fee_revenue >= 0 AND
        cod_fee_revenue >= 0 AND net_revenue >= 0 AND cogs >= 0 AND cogs_value >= 0 AND
        forward_shipping_cost >= 0 AND reverse_shipping_cost >= 0 AND
        packaging_cost >= 0 AND payment_processing_fee >= 0 AND
        cod_handling_cost >= 0 AND cod_failed_attempt_cost >= 0 AND
        reverse_handling_cost >= 0 AND shrink_cost >= 0 AND support_ndr_cost >= 0 AND
        working_capital_cost >= 0 AND ops_allocation_cost >= 0 AND
        total_variable_cost >= 0),
    CONSTRAINT eco_cm_identity  CHECK (contribution_margin = net_revenue - cogs - total_variable_cost),
    CONSTRAINT eco_rto_identity CHECK (rto_economic_cost = rto_cash_loss + foregone_cm)
);
