{{ config(indexes=[{'columns': ['msno', 'cutoff_date']}, {'columns': ['cutoff_date']}]) }}

-- Step 6.1 — revenue exposure metric:
--   exposure = calibrated_churn_probability x expected_next_renewal_value
--
-- calibrated_churn_probability comes from models/score_predictions.py
-- (offline batch scoring, joined in via the `ml.model_predictions` source
-- below) -- dbt has no ML step, so this is the one mart in the project
-- that depends on an externally-loaded table rather than only on other
-- dbt models.
--
-- expected_next_renewal_value normalizes actual_amount_paid to a common
-- 30-day period so plans of different lengths are comparable. Three
-- exclusions:
--   1. actual_amount_paid = 0 -- trials/promotions carry no renewal value
--      signal (5,417 of 42,813 fct_prediction rows), decided and counted
--      in step 1.3 (notebooks/02_data_quality_findings.md, finding 5).
--   2. actual_amount_paid < 0 -- found while building this metric, not in
--      the original eight step-1.3 issues: 11 fct_prediction rows (35 in
--      raw transactions) carry actual_amount_paid = -1, always paired
--      with is_cancel = 1. Reads as a sentinel value for "no payment on a
--      cancellation" rather than a real amount. Excluded rather than
--      clamped to zero, for the same reason as issue 1: a cancel row's
--      payment field was never a renewal-value estimate in the first
--      place, sentinel or not.
--   3. payment_plan_days not in the five accepted plan lengths -- would be
--      an "irregular" plan the normalization can't be trusted for; the
--      accepted_values test on fct_transaction means this should never
--      actually fire, but the exclusion is expressed explicitly rather
--      than assumed.
-- All three produce a NULL expected_next_renewal_value and NULL exposure,
-- never a zero -- zero would misread as "this subscriber has no revenue
-- at risk" when the truth is "value is unknown for this row."
--
-- Labeled clearly as revenue AT RISK: this is calibrated_probability x
-- value, not a causal estimate of revenue that would be saved by an
-- intervention. No intervention has been run against this population.

with base as (
    select
        p.*,
        mp.calibrated_churn_probability,
        mp.model_version,
        mp.top_contributing_features
    from {{ ref('fct_prediction') }} p
    left join {{ source('ml', 'model_predictions') }} mp
        on mp.msno = p.msno and mp.cutoff_date = p.cutoff_date
)
select
    *,
    (actual_amount_paid <= 0) as excluded_nonpositive_value,
    (payment_plan_days not in (7, 30, 90, 180, 410)) as excluded_irregular_plan,
    case
        when actual_amount_paid <= 0 then null
        when payment_plan_days not in (7, 30, 90, 180, 410) then null
        else round((actual_amount_paid::numeric / payment_plan_days) * 30, 2)
    end as expected_next_renewal_value_30d,
    case
        when actual_amount_paid <= 0 then null
        when payment_plan_days not in (7, 30, 90, 180, 410) then null
        when calibrated_churn_probability is null then null
        else round(calibrated_churn_probability::numeric
                    * (actual_amount_paid::numeric / payment_plan_days) * 30, 2)
    end as revenue_exposure_30d
from base
