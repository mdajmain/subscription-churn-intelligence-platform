-- Step 3.1 — feature table with an enforced point-in-time cutoff.
--
-- Every feature below is computed from rows strictly BEFORE cutoff_date
-- (transaction_date < cutoff_date, user_logs.date < cutoff_date). This is
-- the one rule the whole project depends on: no feature may read data
-- dated at or after its prediction cutoff. tests/test_no_leakage.py checks
-- this holds, and checks it would actually fail if it didn't.

DROP TABLE IF EXISTS fct_features;

CREATE TABLE fct_features AS
SELECT
    s.msno,
    s.cutoff_date,
    s.spell_id,
    s.spell_start,
    s.churn,
    s.label_censored,

    -- member attributes (static, not time-varying — always safe)
    m.city,
    CASE WHEN m.bd BETWEEN 10 AND 90 THEN m.bd ELSE NULL END AS bd,
    (m.bd BETWEEN 10 AND 90) AS bd_is_valid,
    NULLIF(m.gender, '') AS gender,
    m.registered_via,
    (s.cutoff_date - m.registration_init_time) AS tenure_since_registration_days,

    -- current plan, as of the transaction that produced this cutoff
    s.payment_plan_days,
    s.plan_list_price,
    s.actual_amount_paid,
    (s.plan_list_price - s.actual_amount_paid) AS discount,
    s.is_auto_renew,
    s.payment_method_id,

    -- prior renewal / cancellation / plan-change history, strictly before cutoff
    (SELECT count(*) FROM transactions t
      WHERE t.msno = s.msno AND t.transaction_date < s.cutoff_date AND t.is_cancel = 0) AS prior_renewal_count,
    (SELECT count(*) FROM transactions t
      WHERE t.msno = s.msno AND t.transaction_date < s.cutoff_date AND t.is_cancel = 1) AS prior_cancellation_count,
    (SELECT count(DISTINCT t.payment_method_id) FROM transactions t
      WHERE t.msno = s.msno AND t.transaction_date < s.cutoff_date) AS distinct_payment_methods_used,
    (SELECT count(*) FROM (
        SELECT payment_plan_days, LAG(payment_plan_days) OVER (ORDER BY transaction_date) AS prev_days
        FROM transactions t2 WHERE t2.msno = s.msno AND t2.transaction_date < s.cutoff_date
     ) x WHERE x.prev_days IS NOT NULL AND x.payment_plan_days != x.prev_days) AS prior_plan_changes,

    -- activity windows, strictly before cutoff
    COALESCE((SELECT sum(l.total_secs) FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date AND l.date >= s.cutoff_date - 7), 0) AS total_secs_7d,
    COALESCE((SELECT sum(l.total_secs) FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date AND l.date >= s.cutoff_date - 30), 0) AS total_secs_30d,
    COALESCE((SELECT sum(l.total_secs) FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date AND l.date >= s.cutoff_date - 90), 0) AS total_secs_90d,
    COALESCE((SELECT sum(l.num_unq) FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date AND l.date >= s.cutoff_date - 30), 0) AS num_unq_30d,
    COALESCE((SELECT count(*) FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date AND l.date >= s.cutoff_date - 7), 0) AS active_days_7d,
    COALESCE((SELECT count(*) FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date AND l.date >= s.cutoff_date - 30), 0) AS active_days_30d,
    COALESCE((SELECT count(*) FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date AND l.date >= s.cutoff_date - 90), 0) AS active_days_90d,
    (SELECT max(l.date) FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date) AS last_activity_date,
    (SELECT sum(l.num_985 + l.num_100)::float FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date AND l.date >= s.cutoff_date - 30) AS plays_98_5plus_30d,
    (SELECT sum(l.num_25)::float FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date AND l.date >= s.cutoff_date - 30) AS plays_below25_30d,
    (SELECT sum(l.num_25 + l.num_50 + l.num_75 + l.num_985 + l.num_100)::float FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date AND l.date >= s.cutoff_date - 30) AS total_plays_30d,
    NOT EXISTS (SELECT 1 FROM user_logs l WHERE l.msno = s.msno AND l.date < s.cutoff_date) AS missing_activity_flag
FROM fct_snapshot s
JOIN members m ON m.msno = s.msno;

ALTER TABLE fct_features ADD COLUMN days_since_last_activity INT;
UPDATE fct_features SET days_since_last_activity = (cutoff_date - last_activity_date);

ALTER TABLE fct_features ADD COLUMN activity_ratio_7d_to_30d_avg NUMERIC;
UPDATE fct_features SET activity_ratio_7d_to_30d_avg =
    CASE WHEN total_secs_30d > 0 THEN (total_secs_7d / 7.0) / (total_secs_30d / 30.0) ELSE NULL END;

ALTER TABLE fct_features ADD COLUMN share_plays_98_5plus_30d NUMERIC;
ALTER TABLE fct_features ADD COLUMN share_plays_below25_30d NUMERIC;
UPDATE fct_features SET
    share_plays_98_5plus_30d = CASE WHEN total_plays_30d > 0 THEN plays_98_5plus_30d / total_plays_30d ELSE NULL END,
    share_plays_below25_30d  = CASE WHEN total_plays_30d > 0 THEN plays_below25_30d / total_plays_30d ELSE NULL END;

CREATE INDEX ON fct_features (msno, cutoff_date);
CREATE INDEX ON fct_features (cutoff_date);
