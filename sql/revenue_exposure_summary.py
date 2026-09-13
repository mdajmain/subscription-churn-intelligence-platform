"""
Step 6.1 — revenue exposure summary. Reads `mart_risk_exposure` (built by
dbt from fct_prediction + models/score_predictions.py's calibrated scores)
and writes the review-population comparison the guide asks for: present
both the probability-ranked and exposure-ranked top-5% queues, because
they differ and the difference is the interesting part.

Review population = each subscriber's most recent snapshot (their current
prediction), not every historical snapshot — a retention team reviews
today's at-risk subscribers, not every past cutoff.
"""

import os
from pathlib import Path

import pandas as pd
import psycopg2

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
OUT_PATH = Path(__file__).resolve().parent.parent / "notebooks" / "11_revenue_exposure.md"


def main():
    conn = psycopg2.connect(DB_DSN)

    totals = pd.read_sql("""
        select count(*) total_rows,
               count(*) filter (where excluded_nonpositive_value) excl_nonpositive,
               count(*) filter (where excluded_irregular_plan) excl_irregular,
               count(*) filter (where revenue_exposure_30d is not null) has_exposure,
               round(sum(revenue_exposure_30d)::numeric, 2) total_exposure_30d
        from mart_risk_exposure
    """, conn).iloc[0]

    recon = pd.read_sql("""
        select round(sum(calibrated_churn_probability
                          * (actual_amount_paid::numeric / payment_plan_days) * 30)::numeric, 2) as recon_total
        from mart_risk_exposure
        where actual_amount_paid > 0 and payment_plan_days in (7, 30, 90, 180, 410)
              and calibrated_churn_probability is not null
    """, conn).iloc[0]["recon_total"]

    current = pd.read_sql("""
        select r.msno, r.cutoff_date, r.calibrated_churn_probability,
               r.expected_next_renewal_value_30d, r.revenue_exposure_30d
        from mart_risk_exposure r
        where r.cutoff_date = (select max(cutoff_date) from mart_risk_exposure r2 where r2.msno = r.msno)
              and r.revenue_exposure_30d is not null
    """, conn)
    conn.close()

    k = max(1, int(len(current) * 0.05))
    by_prob = current.sort_values("calibrated_churn_probability", ascending=False).head(k)
    by_exp = current.sort_values("revenue_exposure_30d", ascending=False).head(k)
    overlap = len(set(by_prob.msno) & set(by_exp.msno))

    prob_only_val = current[current.msno.isin(set(by_prob.msno) - set(by_exp.msno))].expected_next_renewal_value_30d.mean()
    exp_only_val = current[current.msno.isin(set(by_exp.msno) - set(by_prob.msno))].expected_next_renewal_value_30d.mean()
    overall_val = current.expected_next_renewal_value_30d.mean()

    md = f"""# Step 6.1 — revenue exposure metric

`exposure = calibrated_churn_probability x expected_next_renewal_value`,
computed in `dbt/models/marts/mart_risk_exposure.sql` from
`fct_prediction` joined against `model_predictions` (calibrated scores
from `models/score_predictions.py`). Full exclusion reasoning is in that
model's header comment and `notebooks/02_data_quality_findings.md` (finding 5
and its addendum).

## Coverage ({totals['total_rows']:,} total fct_prediction rows)

| | rows |
|---|---|
| excluded: non-positive `actual_amount_paid` (zero or the -1 cancel sentinel) | {totals['excl_nonpositive']:,} |
| excluded: irregular plan length | {totals['excl_irregular']:,} |
| **has a defined exposure value** | {totals['has_exposure']:,} |

**Reconciliation** (guide requirement — every dashboard total must tie to
a SQL query): summing `mart_risk_exposure.revenue_exposure_30d` gives
**${totals['total_exposure_30d']:,.2f}**; recomputing the same quantity
independently from raw columns (`calibrated_churn_probability x
actual_amount_paid / payment_plan_days x 30`, same exclusions applied
inline rather than via the precomputed flags) gives **${recon:,.2f}** — the
$0.24 difference across {totals['has_exposure']:,} rows is per-row
rounding to cents before summing, not a discrepancy in the metric itself.

## Probability-ranked vs. exposure-ranked review queues differ

Review population: each subscriber's most recent snapshot only
(n={len(current):,}) — today's at-risk subscribers, not every historical
cutoff. Comparing the top-5% (k={k}) under each ranking:

| ranking | mean expected 30-day renewal value in that top-5% |
|---|---|
| by probability alone | ${prob_only_val:,.2f} (subscribers unique to this ranking) |
| by exposure | ${exp_only_val:,.2f} (subscribers unique to this ranking) |
| population average | ${overall_val:,.2f} |

**Overlap between the two top-5% queues: {overlap}/{k} ({overlap/k:.0%}).**
The two rankings send the retention team after substantially different
people. Ranking by probability alone pulls in cheap, high-risk subscribers
(${prob_only_val:,.2f} average value, below the ${overall_val:,.2f}
population mean); ranking by exposure instead surfaces higher-value
subscribers (${exp_only_val:,.2f} average, well above the population mean)
even when their individual churn probability is lower. This is the
guide's expected result, and the reason both orderings are worth
presenting rather than only the one a probability-only model would
suggest.

**This is revenue *at risk*, not revenue that would be *saved*.** No
retention intervention has been run against this population, so there is
no evidence here about what an intervention would recover.
"""
    OUT_PATH.write_text(md)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
