-- ============================================================================
-- 01_schema_truth.sql
--
-- The hidden schema. Two tables that hold the planted ground truth: the four
-- customer latents, and the true probability behind every draw.
--
-- WHY THIS IS A SEPARATE SCHEMA WITH ITS OWN PERMISSIONS
-- ------------------------------------------------------
-- latent_intent is the single most important object in this project. It is the
-- unobservable that makes COD *look* more causal than it is; it is why the
-- adjusted effect is smaller than the naive effect; and test GT-03 is designed
-- to FAIL to fully recover the truth precisely because it stays unobservable.
-- If an analyst could join to it, the entire exercise collapses -- the naive and
-- adjusted estimates would converge and the project's central finding would
-- evaporate.
--
-- So leakage protection here is a PERMISSIONS BOUNDARY, not a code-review
-- convention. The analyst role is granted nothing on this schema. A leak becomes
-- a permission error at query time rather than a discipline failure nobody
-- notices. Test LK-05 verifies the grants; CLAUDE.md rule 4 states the rule.
--
-- The validation harness runs as a SEPARATE role (validator) which CAN read
-- truth -- it has to, in order to compute the AUC ceiling in GT-05 and the
-- planted-vs-recovered table in report section 8.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS truth;

-- ---------------------------------------------------------------------------
-- truth_customer_latent  (spec §3.13)  -- 55,000 rows
--
-- Drawn at customer creation, correlated (not orthogonal: perfectly independent
-- latents would be unrealistic and would make the confounding too easy to
-- disentangle). Pre-window history is then generated FROM these, which is what
-- makes the confounding causal rather than fitted.
-- ---------------------------------------------------------------------------
CREATE TABLE truth.truth_customer_latent (
    customer_id             VARCHAR(12)  PRIMARY KEY
                            REFERENCES analytics.dim_customer(customer_id),
    -- Belief that money sent online results in the right goods arriving.
    -- Low -> COD, and mildly -> RTO.
    latent_trust            NUMERIC(6,4) NOT NULL,
    -- Cash/credit access and budgeting style. Low -> COD, and -> cash-at-door
    -- failures at delivery.
    latent_liquidity        NUMERIC(6,4) NOT NULL,
    -- *** THE CORE CONFOUNDER ***
    -- Low-commitment / free-optionality trait: "order it, decide later".
    -- High -> COD (moderately) AND -> RTO (strongly). Because it drives both, a
    -- naive COD-vs-prepaid crosstab attributes its effect to COD. Recovering
    -- roughly two thirds of that excess, and no more, is what GT-03 tests.
    latent_intent           NUMERIC(6,4) NOT NULL,
    latent_price_sensitivity NUMERIC(6,4) NOT NULL,
    -- Customer-level mean P(COD) across their sessions. Diagnostic.
    -- NULLABLE, and deliberately so. 3,284 of 55,000 customers (6.0%) open no
    -- session inside the 90-day window, so this mean has no denominator. Spec
    -- §3.13 declares the type only; the NOT NULL was added here and was wrong.
    -- Decision A18's rule applies unchanged: a rate with an empty denominator is
    -- NULL, never imputed -- imputing 0.62 would invent 3,284 fictitious
    -- COD-average customers in the truth table itself.
    true_cod_propensity     NUMERIC(5,4),

    CONSTRAINT lat_propensity_range CHECK (true_cod_propensity BETWEEN 0 AND 1)
);

-- ---------------------------------------------------------------------------
-- truth_order_probability  (spec §3.13)  -- 147,059 rows, one per SESSION
--
-- One row per session, not per order, because p_convert and p_cod_intent exist
-- for sessions that never became orders -- and those sessions are the CAL-06
-- denominator.
--
-- p_rto_precheckout is THE AUC CEILING. It is the RTO probability computed from
-- Stage-1/2 information only, frozen before any Stage-4 fact exists. No risk
-- model reading safe features can beat it. The gap between it and p_rto_final
-- is the post-dispatch shock: courier quality, actual transit delay, whether the
-- customer happened to be home. That gap is the honest source of the ~0.76
-- ceiling -- which is a REQUIREMENT, not a defect (CLAUDE.md).
-- ---------------------------------------------------------------------------
CREATE TABLE truth.truth_order_probability (
    session_id              VARCHAR(16)  PRIMARY KEY
                            REFERENCES analytics.fct_checkout_session(session_id),
    p_convert               NUMERIC(6,5),
    p_cod_intent            NUMERIC(6,5),
    -- Every additive term in the COD logit, by name. Lets Phase 5 decompose an
    -- individual session's probability into its named drivers.
    --
    -- DECISION A45 -- POPULATED FOR A DOCUMENTED SAMPLE, NOT FOR EVERY ROW.
    -- These columns exist to make GT-01 auditable: a reviewer opens ONE order and
    -- reads every term that produced its probability. That is a lookup, never a
    -- scan, so 2,000 stratified sessions are traced rather than all 155,000 --
    -- full population cost ~190 MB of JSONB for a query nobody runs in bulk.
    -- The strata are a random draw plus the three cases anyone would actually
    -- open: a COD order that came back, a prepaid order that came back, and a
    -- high-risk order that arrived safely. The rule lives in params.yaml under
    -- `truth_sampling` and is drawn from its own seed substream.
    --
    -- Each trace carries its own totals (`__total__`, `__probability__`), so the
    -- decomposition can be checked by adding the named terms up. The generator
    -- already does exactly that and refuses to store a trace that disagrees with
    -- the probability the day loop recorded.
    logit_cod_components    JSONB,
    -- NULL for sessions that produced no shipped order.
    p_rto_precheckout       NUMERIC(6,5),
    p_rto_final             NUMERIC(6,5),
    -- Both RTO stages. Stage-1 terms are bare; the post-dispatch shock's four
    -- terms are prefixed `shock.`, so a reader can always tell which half of the
    -- trace was knowable at checkout. NULL where the session produced no order.
    logit_rto_components    JSONB,
    post_dispatch_shock     NUMERIC(6,4),
    -- Decision A45. Says whether this row was drawn into the audit sample, which
    -- is what turns ~153,000 NULL traces from an unexplained gap into a stated
    -- one. Without it "no components" and "not sampled" are indistinguishable,
    -- and an ambiguous NULL is exactly the defect class A44 was written about.
    -- Deliberately NO DEFAULT: the loader's pre-flight exempts defaulted
    -- columns from its "declared but absent from the frame" check, so a
    -- default would quietly excuse the generator from ever emitting this.
    components_populated    BOOLEAN      NOT NULL,

    CONSTRAINT tp_prob_range CHECK (
        (p_convert         IS NULL OR p_convert         BETWEEN 0 AND 1) AND
        (p_cod_intent      IS NULL OR p_cod_intent      BETWEEN 0 AND 1) AND
        (p_rto_precheckout IS NULL OR p_rto_precheckout BETWEEN 0 AND 1) AND
        (p_rto_final       IS NULL OR p_rto_final       BETWEEN 0 AND 1)),
    -- The pre-checkout score must be frozen BEFORE the shock is known, so both
    -- exist together or neither does.
    CONSTRAINT tp_rto_pair   CHECK ((p_rto_precheckout IS NULL) = (p_rto_final IS NULL)),
    -- The flag is not a label the loader can get wrong independently of the data:
    -- every sampled session carries a COD trace, and only sampled sessions do.
    CONSTRAINT tp_components_flag CHECK (
        components_populated = (logit_cod_components IS NOT NULL)),
    -- An RTO trace exists exactly where the row was sampled AND an RTO logit was
    -- computed. Stated as an equality rather than an implication so neither a
    -- missing trace nor an orphaned one can slip through.
    CONSTRAINT tp_components_rto CHECK (
        (logit_rto_components IS NOT NULL)
        = (components_populated AND p_rto_precheckout IS NOT NULL))
);

-- ============================================================================
-- THE PERMISSIONS BOUNDARY  (brief §10, test LK-05, CLAUDE.md rule 4)
--
-- Roles are created IF NOT EXISTS-style via DO blocks so this file is
-- idempotent and safe to re-run by the drop-and-recreate loader.
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst') THEN
        CREATE ROLE analyst NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'validator') THEN
        CREATE ROLE validator NOLOGIN;
    END IF;
END
$$;

-- The analyst sees everything in analytics, and nothing else.
GRANT USAGE ON SCHEMA analytics TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO analyst;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO analyst;

-- The analyst sees NOTHING in truth. Explicit REVOKE as well as absence of
-- GRANT, because PUBLIC may otherwise inherit privileges.
REVOKE ALL ON SCHEMA truth                    FROM analyst;
REVOKE ALL ON ALL TABLES IN SCHEMA truth      FROM analyst;
REVOKE ALL ON SCHEMA truth                    FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA truth      FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA truth      REVOKE ALL ON TABLES FROM analyst;
ALTER DEFAULT PRIVILEGES IN SCHEMA truth      REVOKE ALL ON TABLES FROM PUBLIC;

-- The validation harness needs truth in order to compute the AUC ceiling (GT-05)
-- and the planted-vs-recovered coefficient table. It is a different role.
GRANT USAGE ON SCHEMA truth TO validator;
GRANT SELECT ON ALL TABLES IN SCHEMA truth TO validator;
GRANT USAGE ON SCHEMA analytics TO validator;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO validator;
ALTER DEFAULT PRIVILEGES IN SCHEMA truth     GRANT SELECT ON TABLES TO validator;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO validator;
