-- step 1.3 issue 1 decision: dedup key is the full row; drop exact duplicates.
select distinct
    msno,
    payment_method_id,
    payment_plan_days,
    plan_list_price,
    actual_amount_paid,
    is_auto_renew,
    transaction_date,
    membership_expire_date,
    is_cancel
from {{ source('raw', 'transactions') }}
