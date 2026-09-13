-- Step 6.2 — retention curve by plan length, for the Subscription
-- Overview dashboard. Same finding as notebooks/06_cohort_retention.md
-- (churn rises monotonically with plan length), materialized here as a
-- queryable mart broken out by tenure band too, since the dashboard spec
-- (dashboards/tableau_spec.md) plots one line per plan length across
-- tenure bands. Non-censored snapshots only -- a censored row's true
-- outcome isn't known yet, so it can't contribute to an observed
-- retention rate.
with tenure_bands as (
    select
        p.payment_plan_days,
        case
            when p.tenure_since_registration_days <= 30 then '0-30d'
            when p.tenure_since_registration_days <= 90 then '31-90d'
            when p.tenure_since_registration_days <= 180 then '91-180d'
            when p.tenure_since_registration_days <= 365 then '181-365d'
            else '365d+'
        end as tenure_band,
        case
            when p.tenure_since_registration_days <= 30 then 1
            when p.tenure_since_registration_days <= 90 then 2
            when p.tenure_since_registration_days <= 180 then 3
            when p.tenure_since_registration_days <= 365 then 4
            else 5
        end as tenure_band_sort,
        p.churn
    from {{ ref('fct_prediction') }} p
    where p.churn is not null
)
select
    payment_plan_days,
    tenure_band,
    tenure_band_sort,
    count(*) as n,
    avg(churn::float) as churn_rate,
    1 - avg(churn::float) as retention_rate
from tenure_bands
group by 1, 2, 3
order by 1, 3
