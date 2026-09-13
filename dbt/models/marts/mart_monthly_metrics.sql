-- Business-facing monthly summary: active subscribers, new subscriptions
-- (spells starting that month), observed churn, renewal rate.
--
-- n_active_subscribers (added step 6.2) is a genuine point-in-time count
-- -- distinct msno with a spell covering the last day of the month -- not
-- to be confused with n_snapshots (a count of renewal-cycle *events* that
-- month, which double-counts a subscriber who renews more than once in
-- the same month and misses one who neither renews nor churns that
-- month). The dashboard spec (dashboards/tableau_spec.md) uses
-- n_active_subscribers for the headline "active subscribers" tile.
with months as (
    select generate_series(
        date_trunc('month', (select min(spell_start) from {{ ref('fct_membership_spell') }})),
        date_trunc('month', (select max(cutoff_date) from {{ ref('fct_snapshot') }})),
        interval '1 month'
    )::date as month
),
active as (
    select
        m.month,
        count(distinct sp.msno) as n_active_subscribers
    from months m
    join {{ ref('fct_membership_spell') }} sp
        on sp.spell_start <= (m.month + interval '1 month' - interval '1 day')::date
       and sp.spell_expire >= m.month
    group by m.month
),
monthly_snapshot as (
    select
        date_trunc('month', cutoff_date)::date as month,
        count(*) as n_snapshots,
        count(*) filter (where churn is not null) as n_labeled,
        sum(churn) filter (where churn is not null) as n_churn,
        avg(churn::float) filter (where churn is not null) as churn_rate,
        count(*) filter (where churn = 0) as n_renewed
    from {{ ref('fct_snapshot') }}
    group by 1
),
new_subs as (
    select
        date_trunc('month', spell_start)::date as month,
        count(*) as n_new_spells
    from {{ ref('fct_membership_spell') }}
    group by 1
)
select
    months.month,
    coalesce(active.n_active_subscribers, 0) as n_active_subscribers,
    ms.n_snapshots,
    ms.n_labeled,
    ms.n_churn,
    ms.churn_rate,
    ms.n_renewed,
    coalesce(ns.n_new_spells, 0) as n_new_spells,
    case when ms.n_labeled > 0 then ms.n_renewed::float / ms.n_labeled else null end as renewal_rate
from months
left join active on active.month = months.month
left join monthly_snapshot ms on ms.month = months.month
left join new_subs ns on ns.month = months.month
order by months.month
