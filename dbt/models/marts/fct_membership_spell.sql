{{ config(indexes=[{'columns': ['msno', 'spell_expire']}]) }}

-- Step 2.1 — reconstruct membership spells. A spell merges consecutive
-- transactions with a gap of at most 30 days between one transaction's
-- expiry and the next one's date — the same window the churn label uses,
-- so a spell boundary and a churn event are defined consistently rather
-- than by two independently-written rules (see notebooks/05_label_validation.md
-- for the bug this consistency requirement prevented).
--
-- spell_expire is the membership_expire_date of the chronologically LAST
-- transaction in the spell (not MAX(membership_expire_date)) — MAX would
-- silently ignore a cancel that truly ends a spell below a higher value
-- seen earlier in the same spell.

with ordered as (
    select
        *,
        lag(membership_expire_date) over (partition by msno order by transaction_date) as prev_expire
    from {{ ref('fct_transaction') }}
),
flagged as (
    select
        *,
        case
            when prev_expire is null then 1
            when transaction_date > prev_expire + interval '30 days' then 1
            else 0
        end as is_new_spell
    from ordered
),
grouped as (
    select
        *,
        sum(is_new_spell) over (partition by msno order by transaction_date rows unbounded preceding) as spell_seq
    from flagged
)
select
    msno,
    spell_seq as spell_id,
    min(transaction_date) as spell_start,
    (array_agg(membership_expire_date order by transaction_date desc))[1] as spell_expire,
    count(*) as n_transactions,
    bool_or(is_cancel = 1) as had_cancel_event,
    (array_agg(is_auto_renew order by transaction_date desc))[1] as is_auto_renew_at_end,
    (array_agg(payment_plan_days order by transaction_date desc))[1] as payment_plan_days_at_end
from grouped
group by msno, spell_seq
