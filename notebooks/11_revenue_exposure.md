# Step 6.1 — revenue exposure metric

`exposure = calibrated_churn_probability x expected_next_renewal_value`,
computed in `dbt/models/marts/mart_risk_exposure.sql` from
`fct_prediction` joined against `model_predictions` (calibrated scores
from `models/score_predictions.py`). Full exclusion reasoning is in that
model's header comment and `notebooks/02_data_quality_findings.md` (finding 5
and its addendum).

## Coverage (42,813.0 total fct_prediction rows)

| | rows |
|---|---|
| excluded: non-positive `actual_amount_paid` (zero or the -1 cancel sentinel) | 5,428.0 |
| excluded: irregular plan length | 0.0 |
| **has a defined exposure value** | 37,385.0 |

**Reconciliation** (guide requirement — every dashboard total must tie to
a SQL query): summing `mart_risk_exposure.revenue_exposure_30d` gives
**$353,277.95**; recomputing the same quantity
independently from raw columns (`calibrated_churn_probability x
actual_amount_paid / payment_plan_days x 30`, same exclusions applied
inline rather than via the precomputed flags) gives **$353,278.19** — the
$0.24 difference across 37,385.0 rows is per-row
rounding to cents before summing, not a discrepancy in the metric itself.

## Probability-ranked vs. exposure-ranked review queues differ

Review population: each subscriber's most recent snapshot only
(n=4,685) — today's at-risk subscribers, not every historical
cutoff. Comparing the top-5% (k=234) under each ranking:

| ranking | mean expected 30-day renewal value in that top-5% |
|---|---|
| by probability alone | $87.68 (subscribers unique to this ranking) |
| by exposure | $122.00 (subscribers unique to this ranking) |
| population average | $96.43 |

**Overlap between the two top-5% queues: 29/234 (12%).**
The two rankings send the retention team after substantially different
people. Ranking by probability alone pulls in cheap, high-risk subscribers
($87.68 average value, below the $96.43
population mean); ranking by exposure instead surfaces higher-value
subscribers ($122.00 average, well above the population mean)
even when their individual churn probability is lower. This is the
guide's expected result, and the reason both orderings are worth
presenting rather than only the one a probability-only model would
suggest.

**This is revenue *at risk*, not revenue that would be *saved*.** No
retention intervention has been run against this population, so there is
no evidence here about what an intervention would recover.
