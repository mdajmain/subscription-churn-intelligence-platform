-- Raw tables, one per source file. Grain and types mirror the KKBox schema
-- so the rest of the guide (spells, snapshots, features) is unaffected by
-- the synthetic-data substitution documented in README.md.

DROP TABLE IF EXISTS members CASCADE;
CREATE TABLE members (
    msno                    TEXT,
    city                    INT,
    bd                      INT,
    gender                  TEXT,
    registered_via          INT,
    registration_init_time  DATE
);

DROP TABLE IF EXISTS transactions CASCADE;
CREATE TABLE transactions (
    msno                    TEXT,
    payment_method_id       INT,
    payment_plan_days       INT,
    plan_list_price         NUMERIC,
    actual_amount_paid      NUMERIC,
    is_auto_renew           SMALLINT,
    transaction_date        DATE,
    membership_expire_date  DATE,
    is_cancel               SMALLINT
);

DROP TABLE IF EXISTS user_logs CASCADE;
CREATE TABLE user_logs (
    msno        TEXT,
    date        DATE,
    num_25      INT,
    num_50      INT,
    num_75      INT,
    num_985     INT,
    num_100     INT,
    num_unq     INT,
    total_secs  NUMERIC
);

-- Validation-only table. Plays the role of KKBox's train_v2.csv: used in
-- step 2.3 to check rebuilt labels, never joined into features or training data.
DROP TABLE IF EXISTS ground_truth_membership CASCADE;
CREATE TABLE ground_truth_membership (
    msno         TEXT,
    spell_start  DATE,
    spell_expire DATE,
    true_churn   SMALLINT
);

CREATE INDEX ON transactions (msno, transaction_date);
CREATE INDEX ON user_logs (msno, date);
CREATE INDEX ON ground_truth_membership (msno, spell_expire);
