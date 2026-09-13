-- Step 2.2 — snapshot table. Grain: fct_snapshot(msno, cutoff_date).
--
-- Note this grain is FINER than fct_membership_spell: a spell can span many
-- renewal transactions (merged under the 30-day tolerance), and each of
-- those transactions' own membership_expire_date is a separate prediction
-- moment — "will THIS renewal cycle be followed by another one." Only the
-- LAST snapshot in a spell can be a true churn; every other snapshot in a
-- continuing spell is, by construction of the 30-day merge tolerance,
-- followed by a later transaction within the window.

DROP TABLE IF EXISTS fct_snapshot;

CREATE TABLE fct_snapshot AS
WITH collapsed AS (
    -- one row per (msno, transaction_date) — step 1.3 issue 4
    SELECT DISTINCT ON (msno, transaction_date)
        msno, transaction_date, membership_expire_date, is_cancel,
        is_auto_renew, payment_plan_days, plan_list_price, actual_amount_paid, payment_method_id
    FROM transactions
    ORDER BY msno, transaction_date, membership_expire_date DESC, is_cancel ASC
),
collapsed_by_cutoff AS (
    -- A cancel row and the renewal it's paired with can carry the SAME
    -- membership_expire_date on two different transaction_dates (a cancel
    -- reuses the current expiry rather than reducing it in this dataset —
    -- see notebooks/04_spell_reconstruction_spotcheck.md's noted
    -- limitation). Without this second collapse, fct_snapshot grain breaks:
    -- (msno, cutoff_date) is supposed to be unique per step 2.2's "done
    -- when," and a first pass keyed only on transaction_date produced 3,462
    -- duplicate groups. Collapse to one row per (msno, cutoff_date),
    -- preferring the latest transaction_date, non-cancel as tie-break.
    SELECT DISTINCT ON (msno, membership_expire_date)
        msno, transaction_date, membership_expire_date, is_cancel,
        is_auto_renew, payment_plan_days, plan_list_price, actual_amount_paid, payment_method_id
    FROM collapsed
    ORDER BY msno, membership_expire_date, transaction_date DESC, is_cancel ASC
),
data_cutoff AS (
    SELECT max(transaction_date) AS max_tx_date FROM transactions
)
SELECT
    c.msno,
    c.membership_expire_date AS cutoff_date,
    c.transaction_date,
    sp.spell_id,
    sp.spell_start,
    sp.spell_expire,
    c.is_auto_renew,
    c.payment_plan_days,
    c.plan_list_price,
    c.actual_amount_paid,
    c.payment_method_id,
    c.is_cancel,
    -- Renewed iff this cutoff isn't the spell's final expiry — i.e. a later
    -- transaction in the SAME spell extends coverage past it. This reuses
    -- fct_membership_spell's 30-day merge tolerance rather than re-deriving
    -- an independent "transaction after cutoff" check: an early first-pass
    -- version required the next transaction_date to be strictly AFTER the
    -- cutoff, which misclassified early renewals (a renewal recorded a day
    -- or two before the prior cycle's expiry — common in this data) as
    -- churn. The spell table already gets this right (a negative gap is
    -- still "within 30 days"), so the label should be read off it directly.
    (c.membership_expire_date < sp.spell_expire) AS renewed_in_spell,
    (c.membership_expire_date = sp.spell_expire
        AND (sp.spell_expire + INTERVAL '30 days') > (SELECT max_tx_date FROM data_cutoff)) AS label_censored
FROM collapsed_by_cutoff c
JOIN fct_membership_spell sp
    ON sp.msno = c.msno
   AND c.membership_expire_date BETWEEN sp.spell_start AND sp.spell_expire;

-- churn = 1 iff this is the spell's last snapshot (no later transaction in
-- the spell extends coverage) AND the 30-day window has fully elapsed
-- (not censored). Censored snapshots get a NULL label — they must never be
-- used for training or evaluation (mirrors the gap left between train/val/
-- test splits in step 3.2).
ALTER TABLE fct_snapshot ADD COLUMN churn SMALLINT;
UPDATE fct_snapshot
SET churn = CASE
    WHEN label_censored THEN NULL
    WHEN renewed_in_spell THEN 0
    ELSE 1
END;

CREATE INDEX ON fct_snapshot (msno, cutoff_date);
CREATE INDEX ON fct_snapshot (cutoff_date);
