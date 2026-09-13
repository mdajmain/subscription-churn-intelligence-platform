{{ config(indexes=[{'columns': ['msno', 'transaction_date']}]) }}

-- Grain: one row per (msno, transaction_date). Step 1.3 issue 4 decision:
-- when multiple raw transactions share a transaction_date, the row with
-- the latest membership_expire_date governs, with a non-cancel row
-- breaking ties over a same-day cancel.
select distinct on (msno, transaction_date)
    msno,
    transaction_date,
    membership_expire_date,
    is_cancel,
    is_auto_renew,
    payment_plan_days,
    plan_list_price,
    actual_amount_paid,
    payment_method_id
from {{ ref('stg_transactions') }}
order by msno, transaction_date, membership_expire_date desc, is_cancel asc
