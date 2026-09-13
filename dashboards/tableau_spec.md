# Tableau dashboard spec — Week 6.2

Tableau Desktop/Public is a GUI application this build can't drive
directly, so this step ships the SQL views and this spec instead of
finished, published dashboards. Everything below reads from `dbt/`-built
tables (`churn` Postgres database) — connect Tableau to Postgres directly
if you have Tableau Desktop with a live connection, or export each table
to CSV/Hyper extract for Tableau Public (see "Tableau Public workflow"
below).

Both dashboards, per the guide:
- **Date filter** and **segment filter** (plan length at minimum; add
  tenure band / registered_via where the panel supports it).
- **A visible refresh timestamp.** Since there's no live connection on
  Tableau Public, this must be a literal value baked into the extract at
  export time, not a `NOW()` calculated field (which would show your
  viewing time, not the data's). Simplest approach: add a text object
  reading `"Data as of " & {the export date}` and update it by hand each
  time you re-export — or add a one-row `metadata` table
  (`select now() as refreshed_at`) to the extract and reference that field.
- **A metric-definitions panel** — a text/tooltip object stating, verbatim,
  the definitions given per-panel below. Don't paraphrase them on the
  dashboard; a metric defined differently in two places is worse than a
  metric defined once.
- **Observed vs. predicted must be visually distinguishable** — e.g. solid
  fill/line for observed (actuals from `fct_snapshot.churn`,
  `mart_monthly_metrics`), a distinct hatch/dashed style or a clearly
  different hue for predicted (`mart_risk_exposure.calibrated_churn_probability`).
  Never render them with the same visual treatment on the same axis.

---

## Dashboard 1 — Subscription Overview

Source tables: `mart_monthly_metrics`, `mart_retention_curve`, `mart_cohort_heatmap`.

| Panel | Source | Definition | Notes |
|---|---|---|---|
| Active subscribers (KPI tile) | `mart_monthly_metrics.n_active_subscribers` | Distinct subscribers with a membership spell covering the last day of the selected month. | **Not** the same as a raw snapshot/event count — see the model's header comment for why those differ. |
| New subscriptions (KPI tile) | `mart_monthly_metrics.n_new_spells` | Count of membership spells (`fct_membership_spell`) starting that month. | A returning subscriber who reactivates after a gap >30 days counts as a new spell, by the same 30-day rule used everywhere else in this project. |
| Observed churn (line/bar) | `mart_monthly_metrics.churn_rate` | Of non-censored snapshots with a cutoff in that month, the fraction where `churn=1`. | Recent months are `NULL` — those snapshots are still censored (their 30-day outcome window hasn't elapsed against the data's collection cutoff). Show as a gap, not zero. |
| Renewal rate (line/bar) | `mart_monthly_metrics.renewal_rate` | `n_renewed / n_labeled` for that month. | Same censoring caveat as churn rate. |
| Retention curve by plan length (line chart, one line per `payment_plan_days`) | `mart_retention_curve` | `retention_rate = 1 - churn_rate` within each (plan length x tenure band) cell, non-censored snapshots only. | x-axis = `tenure_band` ordered by `tenure_band_sort`, not alphabetically. Matches the Week 2 finding: churn rises monotonically with plan length (~3-point spread), most visible in the 0-30d tenure band. |
| Cohort retention heatmap | `mart_cohort_heatmap` | Rows = `cohort_month` (registration month), columns = `months_since_signup` (0-17), cell = `pct_active`. | Cells beyond a cohort's observed window are absent from the table (not zero) — filter the heatmap to non-null cells only, or a young cohort will show a false cliff at its right edge. |

**Reconciliation query** (for the "active subscribers" tile, most recent
complete month in this build, 2018-04):

```sql
-- via the mart:
select n_active_subscribers from mart_monthly_metrics where month = '2018-04-01';  -- returns 122

-- independent recomputation directly from the spell table:
select count(distinct sp.msno)
from fct_membership_spell sp
where sp.spell_start <= '2018-04-30' and sp.spell_expire >= '2018-04-01';  -- must also return 122
```
Both must return the same number for any month you put on the tile — if
they don't, the extract is stale.

---

## Dashboard 2 — Churn Risk

Source table: `mart_risk_exposure` (one row per (msno, cutoff_date);
filter to each subscriber's most recent `cutoff_date` for a "current risk"
view — see the reconciliation query below for the exact filter).

| Panel | Source | Definition | Notes |
|---|---|---|---|
| Predicted risk distribution (histogram) | `calibrated_churn_probability` | Sigmoid-calibrated churn probability (see `notebooks/10_calibration_check.md` for why sigmoid over isotonic). | This is **predicted**, not observed — give it the "predicted" visual treatment defined above. |
| Revenue exposure (KPI tile + distribution) | `revenue_exposure_30d` | `calibrated_churn_probability x expected_next_renewal_value_30d`. NULL for excluded rows (zero/negative payment, irregular plan) — exclude NULLs from the sum/average, don't coerce to 0. | Label this tile "revenue at risk," never "revenue saved" — no intervention has been run. |
| Top-K review queue (table, sortable) | `msno`, `calibrated_churn_probability`, `expected_next_renewal_value_30d`, `revenue_exposure_30d`, `top_contributing_features` | Default sort: `revenue_exposure_30d` descending. | Give the viewer a toggle (parameter or a second sheet) between sorting by probability and by exposure — `notebooks/11_revenue_exposure.md` found only 12% overlap between the two top-5% queues, which is exactly the comparison the guide asks the dashboard to make visible. |
| Segment breakdown | `payment_plan_days`, a calculated tenure-band field on `tenure_since_registration_days` (bins: 0-90d / 91-365d / 365d+, matching `models/train.py`'s segment breakdown in `notebooks/07_model_results.md`), `churn` (observed, non-censored rows), `calibrated_churn_probability` (predicted) | Average observed churn rate and average predicted probability per segment, side by side. | This is the one panel that puts observed and predicted on the same chart by design — the whole point is to let the viewer see where the model over- or under-predicts by segment. Keep the visual distinction (fill vs. outline) even here. |
| Per-subscriber drill-down | filter the whole dashboard to one `msno` (a parameter or dashboard action from the review-queue table) | Shows that subscriber's `calibrated_churn_probability`, `expected_next_renewal_value_30d`, and `top_contributing_features` (a string of up to 3 `"feature:signed_contribution"` pairs from XGBoost's native `pred_contribs` — computed from the raw model, since calibration remaps the overall score without changing which features drove it). | Render the contributions as a small tornado/bar chart if you split `top_contributing_features` into rows first (Tableau can't parse the packed string directly — split on `", "` then `":"` with a calculated field, or preprocess it in the extract). |

**Reconciliation query** (current-risk population and its exposure total):

```sql
select count(*) as n, round(sum(revenue_exposure_30d)::numeric, 2) as total_exposure
from mart_risk_exposure r
where r.cutoff_date = (select max(cutoff_date) from mart_risk_exposure r2 where r2.msno = r.msno)
  and r.revenue_exposure_30d is not null;
```
Full reconciliation writeup, including the independent-recomputation check
and the top-5%-by-probability-vs-exposure comparison:
`notebooks/11_revenue_exposure.md`.

---

## Tableau Public workflow (no live database connection)

Tableau Public can't hold a live Postgres connection, so refresh is a
manual extract workflow, not automatic:

1. Run `docker compose up -d` and, if scores are stale, `python3
   models/score_predictions.py` (recalibrates and rescoring
   `model_predictions`) then `cd dbt && dbt build` (rebuilds every mart,
   `mart_risk_exposure` included, from the fresh scores).
2. Export each source table used above (`mart_monthly_metrics`,
   `mart_retention_curve`, `mart_cohort_heatmap`, `mart_risk_exposure`) to
   CSV, or connect Tableau Desktop live and save a `.hyper` extract.
3. Republish to Tableau Public with the new extract; update the refresh
   timestamp text object to the export date (see above — there is no
   automatic way to do this on Public).
4. State this workflow explicitly in the dashboard's definitions panel or
   a caption, so a viewer doesn't assume the numbers are live.
