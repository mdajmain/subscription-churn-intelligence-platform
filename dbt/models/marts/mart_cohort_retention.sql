-- Step 2.4 cohort/retention finding, materialized as a queryable mart
-- rather than a one-off notebook query — this is what the Churn Risk
-- dashboard's cohort heatmap (step 6.2) would read from.
select
    date_trunc('month', c.registration_init_time)::date as cohort_month,
    count(*) as n_snapshots,
    sum(s.churn) filter (where s.churn is not null) as n_churn,
    avg(s.churn::float) filter (where s.churn is not null) as churn_rate
from {{ ref('fct_snapshot') }} s
join {{ ref('dim_customer') }} c on c.msno = s.msno
group by 1
order by 1
