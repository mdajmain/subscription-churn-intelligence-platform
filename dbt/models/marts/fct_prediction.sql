{{ config(indexes=[{'columns': ['msno', 'cutoff_date']}, {'columns': ['cutoff_date']}]) }}

-- Step 3.1 — feature + prediction table. Every feature is computed from
-- rows strictly BEFORE cutoff_date. tests/assert_no_feature_leakage.sql
-- checks this holds; tests/test_no_leakage.py (outside dbt) additionally
-- checks the test would fail if the cutoff were relaxed.
--
-- Note vs. the pre-dbt version (sql/build_features.sql): prior-history
-- counts here read from fct_transaction (deduped) rather than raw
-- transactions. The original ad-hoc script queried raw transactions for
-- these specific counts while using the deduped view everywhere else — an
-- inconsistency the dbt refactor's explicit model layering made obvious
-- and worth fixing rather than carrying forward.

with base as (
    select
        s.msno,
        s.cutoff_date,
        s.spell_id,
        s.spell_start,
        s.churn,
        s.label_censored,
        c.city,
        c.bd,
        c.bd_is_valid,
        c.gender,
        c.registered_via,
        (s.cutoff_date - c.registration_init_time) as tenure_since_registration_days,
        s.payment_plan_days,
        s.plan_list_price,
        s.actual_amount_paid,
        (s.plan_list_price - s.actual_amount_paid) as discount,
        s.is_auto_renew,
        s.payment_method_id,
        (select count(*) from {{ ref('fct_transaction') }} t
          where t.msno = s.msno and t.transaction_date < s.cutoff_date and t.is_cancel = 0) as prior_renewal_count,
        (select count(*) from {{ ref('fct_transaction') }} t
          where t.msno = s.msno and t.transaction_date < s.cutoff_date and t.is_cancel = 1) as prior_cancellation_count,
        (select count(distinct t.payment_method_id) from {{ ref('fct_transaction') }} t
          where t.msno = s.msno and t.transaction_date < s.cutoff_date) as distinct_payment_methods_used,
        (select count(*) from (
            select payment_plan_days, lag(payment_plan_days) over (order by transaction_date) as prev_days
            from {{ ref('fct_transaction') }} t2 where t2.msno = s.msno and t2.transaction_date < s.cutoff_date
         ) x where x.prev_days is not null and x.payment_plan_days != x.prev_days) as prior_plan_changes,
        coalesce((select sum(l.total_secs) from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date and l.date >= s.cutoff_date - 7), 0) as total_secs_7d,
        coalesce((select sum(l.total_secs) from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date and l.date >= s.cutoff_date - 30), 0) as total_secs_30d,
        coalesce((select sum(l.total_secs) from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date and l.date >= s.cutoff_date - 90), 0) as total_secs_90d,
        coalesce((select sum(l.num_unq) from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date and l.date >= s.cutoff_date - 30), 0) as num_unq_30d,
        coalesce((select count(*) from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date and l.date >= s.cutoff_date - 7), 0) as active_days_7d,
        coalesce((select count(*) from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date and l.date >= s.cutoff_date - 30), 0) as active_days_30d,
        coalesce((select count(*) from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date and l.date >= s.cutoff_date - 90), 0) as active_days_90d,
        (select max(l.date) from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date) as last_activity_date,
        (select sum(l.num_985 + l.num_100)::float from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date and l.date >= s.cutoff_date - 30) as plays_98_5plus_30d,
        (select sum(l.num_25)::float from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date and l.date >= s.cutoff_date - 30) as plays_below25_30d,
        (select sum(l.num_25 + l.num_50 + l.num_75 + l.num_985 + l.num_100)::float from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date and l.date >= s.cutoff_date - 30) as total_plays_30d,
        not exists (select 1 from {{ ref('fct_activity_daily') }} l where l.msno = s.msno and l.date < s.cutoff_date) as missing_activity_flag
    from {{ ref('fct_snapshot') }} s
    join {{ ref('dim_customer') }} c on c.msno = s.msno
)
select
    *,
    (cutoff_date - last_activity_date) as days_since_last_activity,
    case when total_secs_30d > 0 then (total_secs_7d / 7.0) / (total_secs_30d / 30.0) else null end as activity_ratio_7d_to_30d_avg,
    case when total_plays_30d > 0 then plays_98_5plus_30d / total_plays_30d else null end as share_plays_98_5plus_30d,
    case when total_plays_30d > 0 then plays_below25_30d / total_plays_30d else null end as share_plays_below25_30d
from base
