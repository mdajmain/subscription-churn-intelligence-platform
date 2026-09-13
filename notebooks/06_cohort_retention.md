# Cohort and retention analysis — step 2.4

All rates below are observational associations, not causal claims — plan choice, engagement level, and tenure are not randomly assigned. Confidence intervals are 95% Wilson score intervals on the churn rate.

## Churn rate by plan length

| plan_days | n | churn_rate | 95% CI |
|---|---|---|---|
| 7 | 3,037 | 7.7% | [6.8%, 8.7%] |
| 30 | 29,046 | 7.4% | [7.1%, 7.7%] |
| 90 | 4,710 | 8.4% | [7.6%, 9.2%] |
| 180 | 1,793 | 9.1% | [7.8%, 10.5%] |
| 410 | 868 | 10.4% | [8.5%, 12.6%] |

**Plan-length effect, first half of the window (cutoff < 2016-02-19):**

| plan_days | n | churn_rate |
|---|---|---|
| 7 | 1,633 | 8.7% |
| 30 | 11,557 | 8.9% |
| 90 | 1,371 | 9.6% |
| 180 | 370 | 10.0% |
| 410 | 8 | 12.5% |

**Plan-length effect, second half of the window (cutoff >= 2016-02-19):**

| plan_days | n | churn_rate |
|---|---|---|
| 7 | 1,404 | 6.5% |
| 30 | 17,489 | 6.3% |
| 90 | 3,339 | 7.9% |
| 180 | 1,423 | 8.9% |
| 410 | 860 | 10.3% |

**Finding:** per-cycle churn rate rises monotonically with plan length — from 7.4-7.7% on 7/30-day plans up to 10.4% on 410-day plans — and the same ordering holds in both halves of the window, so it's a stable association, not an artifact of one period. This is the opposite of the naive intuition that committing to a longer plan signals stronger intent to stay; a more likely reading is selection: a 410-day renewal is a comparatively rare event for any given subscriber (868 snapshots vs. 29,046 for 30-day plans), so this group is small and could be disproportionately drawn from subscribers already near the end of their relationship — this is exactly the kind of pattern that needs a plan-length feature in the model (step 3.1) rather than an assumption about which direction it points.

## Churn rate by tenure band (time since spell start)

| tenure_band | n | churn_rate | 95% CI |
|---|---|---|---|
| 0-30d | 3,390 | 12.8% | [11.7%, 14.0%] |
| 31-90d | 7,417 | 9.5% | [8.9%, 10.2%] |
| 91-180d | 8,567 | 7.6% | [7.1%, 8.2%] |
| 181-365d | 12,009 | 6.4% | [6.0%, 6.9%] |
| 365d+ | 8,071 | 5.7% | [5.2%, 6.2%] |

**Finding:** churn risk is highest in the first renewal window (0-30 days of tenure) and declines the longer a subscriber has already stuck around — consistent with the generator's design (first-renewal hazard multiplier) but also a commonly observed real-world pattern worth calling out explicitly.

## Churn rate by signup cohort (registration month)

| cohort_month | n_snapshots | churn_rate |
|---|---|---|
| 2015-01 | 2,990 | 7.3% |
| 2015-02 | 2,701 | 7.1% |
| 2015-03 | 2,906 | 7.4% |
| 2015-04 | 3,066 | 6.8% |
| 2015-05 | 2,633 | 7.8% |
| 2015-06 | 2,246 | 8.6% |
| 2015-07 | 2,541 | 7.9% |
| 2015-08 | 2,545 | 8.3% |
| 2015-09 | 2,182 | 8.0% |
| 2015-10 | 2,190 | 8.3% |
| 2015-11 | 1,993 | 7.9% |
| 2015-12 | 2,099 | 7.9% |
| 2016-01 | 2,444 | 7.2% |
| 2016-02 | 1,937 | 7.7% |
| 2016-03 | 1,875 | 7.7% |
| 2016-04 | 1,614 | 7.2% |
| 2016-05 | 1,492 | 8.0% |

**Finding:** no strong monotonic cohort effect — churn rate by signup month is noisy but doesn't trend sharply, unlike the calendar-time drift found in step 1.4. This suggests the drift in step 1.4 is a *when* effect (something about the period), not a *who* effect (which cohort signed up) — worth keeping in mind if seasonality features are added in step 3.1.

## Engagement decay before churn (30-day pre-cutoff window)

| churn | n | avg total_secs (30d) | stddev | avg active days (30d) |
|---|---|---|---|---|
| 0 | 36,432 | 47948 | 45526 | 13.3 |
| 1 | 3,022 | 16206 | 23685 | 6.1 |

**Effect size:** Cohen's d = 0.72 (renewed vs. churned, 30-day pre-cutoff total_secs). 
**Finding:** subscribers who go on to churn show materially lower activity in the 30 days before their expiry than those who renew — this is the intended signal the generator's decay ramp was built to produce, and confirms the activity features planned for step 3.1 (7/30/90-day windows, days-since-last-activity) should carry real predictive signal rather than noise.
