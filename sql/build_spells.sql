-- Step 2.1 — reconstruct membership spells from raw transactions.
--
-- A spell is a run of transactions where each one starts no more than 30
-- days after the running effective expiry — the same grace window the
-- churn label (step 2.3) uses, so a spell boundary and a churn event are
-- defined consistently. A cancel-then-immediate-resubscribe (step 1.3,
-- issue 3) sits inside one spell; a genuine multi-month gap starts a new one.
--
-- "Later transactions overwrite the effective expiry; is_cancel=1 rows can
-- move it earlier" is implemented literally: spell_expire is the
-- membership_expire_date of the chronologically LAST transaction in the
-- spell, not the max ever seen — so a cancel that truly ends a spell early
-- is respected, while a cancel followed by a renewal is correctly overwritten.

DROP TABLE IF EXISTS fct_membership_spell;

CREATE TABLE fct_membership_spell AS
WITH collapsed AS (
    -- One row per (msno, transaction_date). Exact duplicates collapse for
    -- free. Same-day multiple transactions resolve to the row with the
    -- latest membership_expire_date, with a non-cancel row breaking ties
    -- over a cancel row (step 1.3, issue 4 decision).
    SELECT DISTINCT ON (msno, transaction_date)
        msno, transaction_date, membership_expire_date, is_cancel,
        is_auto_renew, payment_plan_days, plan_list_price, actual_amount_paid, payment_method_id
    FROM transactions
    ORDER BY msno, transaction_date, membership_expire_date DESC, is_cancel ASC
),
ordered AS (
    SELECT *,
        LAG(membership_expire_date) OVER (PARTITION BY msno ORDER BY transaction_date) AS prev_expire
    FROM collapsed
),
flagged AS (
    SELECT *,
        CASE
            WHEN prev_expire IS NULL THEN 1
            WHEN transaction_date > prev_expire + INTERVAL '30 days' THEN 1
            ELSE 0
        END AS is_new_spell
    FROM ordered
),
grouped AS (
    SELECT *,
        SUM(is_new_spell) OVER (PARTITION BY msno ORDER BY transaction_date ROWS UNBOUNDED PRECEDING) AS spell_seq
    FROM flagged
)
SELECT
    msno,
    spell_seq AS spell_id,
    MIN(transaction_date) AS spell_start,
    (ARRAY_AGG(membership_expire_date ORDER BY transaction_date DESC))[1] AS spell_expire,
    COUNT(*) AS n_transactions,
    BOOL_OR(is_cancel = 1) AS had_cancel_event,
    (ARRAY_AGG(is_auto_renew ORDER BY transaction_date DESC))[1] AS is_auto_renew_at_end,
    (ARRAY_AGG(payment_plan_days ORDER BY transaction_date DESC))[1] AS payment_plan_days_at_end
FROM grouped
GROUP BY msno, spell_seq;

CREATE INDEX ON fct_membership_spell (msno, spell_expire);
