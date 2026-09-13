{{ config(indexes=[{'columns': ['cohort_month', 'months_since_signup']}]) }}

-- Step 6.2 — cohort retention heatmap: for each signup cohort (month of
-- registration_init_time), % of that cohort still active N months later.
-- This is the classic retention-curve matrix (cohort_month rows x
-- months_since_signup columns, cell = % active), distinct from
-- mart_cohort_retention (step 2.4's simpler cohort x overall-churn-rate
-- table) and from mart_retention_curve (plan length x tenure band).
--
-- Capped at 17 months since signup: the registration window in this data
-- is 2015-01 through 2016-06 (see README "Honest limitations" — no
-- test-period subscriber can have under ~7 months tenure), and 17 months
-- covers every cohort's overlap with the data-collection window
-- (max(transaction_date)) without extending into columns that would be
-- populated for early cohorts only.
with cohorts as (
    select msno, date_trunc('month', registration_init_time)::date as cohort_month
    from {{ ref('dim_customer') }}
),
offsets as (
    select generate_series(0, 17) as months_since_signup
),
grid as (
    select c.msno, c.cohort_month, o.months_since_signup,
           (c.cohort_month + (o.months_since_signup || ' months')::interval)::date as target_month
    from cohorts c
    cross join offsets o
),
active_flag as (
    select
        g.cohort_month,
        g.months_since_signup,
        g.msno,
        exists (
            select 1 from {{ ref('fct_membership_spell') }} sp
            where sp.msno = g.msno
              and sp.spell_start <= (g.target_month + interval '1 month' - interval '1 day')::date
              and sp.spell_expire >= g.target_month
        ) as is_active
    from grid g
    -- only evaluate cells within the observed data window, so a cohort
    -- doesn't show 0% active for months that simply haven't happened yet
    where g.target_month <= (select max(transaction_date) from {{ ref('fct_transaction') }})
)
select
    cohort_month,
    months_since_signup,
    count(*) as cohort_size,
    sum(is_active::int) as n_active,
    round(avg(is_active::int)::numeric, 4) as pct_active
from active_flag
group by 1, 2
order by 1, 2
