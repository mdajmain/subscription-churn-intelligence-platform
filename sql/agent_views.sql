-- Step 8.1 — the investigation agent's four tools query ONLY these views,
-- never a raw table or a dbt mart directly. "Approved views" isn't just a
-- naming convention: a dedicated `agent_readonly` Postgres role (created
-- below) has SELECT granted on these four views and nothing else in the
-- database — even a compromised or misbehaving agent can't see a raw
-- table, write anything, or query outside this surface, because the
-- database itself refuses it, not because the agent's own code chose not
-- to ask. See sql/agent_grants.sql for the role and grants.

DROP VIEW IF EXISTS v_monthly_metrics;
CREATE VIEW v_monthly_metrics AS
SELECT month, n_active_subscribers, n_snapshots, n_labeled, n_churn,
       churn_rate, n_renewed, n_new_spells, renewal_rate
FROM mart_monthly_metrics;

-- One row per (msno, cutoff_date) snapshot with segment-defining columns
-- and outcomes — query_metrics/compare_segments aggregate this
-- dynamically rather than reading off a pre-aggregated table, so a tool
-- call can answer a segment cut nobody anticipated when the marts were
-- built.
DROP VIEW IF EXISTS v_segment_snapshot;
CREATE VIEW v_segment_snapshot AS
SELECT
    msno, cutoff_date, churn, label_censored,
    payment_plan_days, city, gender, registered_via,
    tenure_since_registration_days,
    calibrated_churn_probability, revenue_exposure_30d,
    date_trunc('month', cutoff_date)::date AS cutoff_month
FROM mart_risk_exposure;

-- check_freshness: last activity date per subscriber + overall
-- data-recency signal, distinct from v_segment_snapshot's prediction-time
-- grain (this is about the RAW activity log's own recency, not a
-- snapshot's derived features).
DROP VIEW IF EXISTS v_activity_freshness;
CREATE VIEW v_activity_freshness AS
SELECT
    c.msno,
    max(a.date) AS last_activity_date,
    count(a.date) AS n_activity_rows,
    (max(a.date) IS NULL) AS has_no_activity_ever
FROM dim_customer c
LEFT JOIN fct_activity_daily a ON a.msno = c.msno
GROUP BY c.msno;

-- get_prediction_summary: risk distribution + exposure + the packed
-- top-3 feature-contribution string per row, for a cohort. Left as
-- unparsed text (not exploded into rows) -- the tool parses it in Python,
-- since a raw view is not the place to embed that logic and it keeps
-- this view a straight passthrough of mart_risk_exposure's own columns.
DROP VIEW IF EXISTS v_prediction_summary;
CREATE VIEW v_prediction_summary AS
SELECT
    msno, cutoff_date, churn, label_censored,
    payment_plan_days, city, gender, registered_via,
    tenure_since_registration_days,
    calibrated_churn_probability, revenue_exposure_30d,
    model_version, top_contributing_features
FROM mart_risk_exposure;
