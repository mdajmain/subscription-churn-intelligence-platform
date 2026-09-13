{{ config(indexes=[{'columns': ['msno', 'cutoff_date']}, {'columns': ['cutoff_date']}]) }}

-- Step 2.2/2.3 — grain: one row per (msno, cutoff_date). Finer than a
-- spell: every renewal cycle's own membership_expire_date is a separate
-- prediction moment. A second DISTINCT ON collapse (keyed on cutoff_date,
-- not transaction_date) is required here — a cancel-and-resubscribe noise
-- transaction can share a membership_expire_date with the renewal it's
-- paired with on a different transaction_date, which produced 3,462
-- duplicate (msno, cutoff_date) groups before this collapse was added
-- (see notebooks/05_label_validation.md).

with collapsed_by_cutoff as (
    select distinct on (msno, membership_expire_date)
        msno, transaction_date, membership_expire_date, is_cancel,
        is_auto_renew, payment_plan_days, plan_list_price, actual_amount_paid, payment_method_id
    from {{ ref('fct_transaction') }}
    order by msno, membership_expire_date, transaction_date desc, is_cancel asc
),
data_cutoff as (
    select max(transaction_date) as max_tx_date from {{ ref('fct_transaction') }}
),
joined as (
    select
        c.msno,
        c.membership_expire_date as cutoff_date,
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
        -- renewed iff this cutoff isn't the spell's final expiry — reuses the
        -- spell's own 30-day merge tolerance rather than re-deriving an
        -- independent "transaction after cutoff" rule (that independent
        -- version undercounted early renewals — see notebooks/05_label_validation.md)
        (c.membership_expire_date < sp.spell_expire) as renewed_in_spell,
        (c.membership_expire_date = sp.spell_expire
            and (sp.spell_expire + interval '30 days') > (select max_tx_date from data_cutoff)) as label_censored
    from collapsed_by_cutoff c
    join {{ ref('fct_membership_spell') }} sp
        on sp.msno = c.msno
       and c.membership_expire_date between sp.spell_start and sp.spell_expire
)
select
    *,
    -- churn = 1 iff this is the spell's last snapshot AND the 30-day
    -- window has fully elapsed (not censored). Censored snapshots get a
    -- NULL label — never used for training or evaluation.
    case
        when label_censored then null
        when renewed_in_spell then 0
        else 1
    end as churn
from joined
