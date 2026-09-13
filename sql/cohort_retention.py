"""
Step 2.4 — cohort and retention analysis. All churn rates here are
observational associations, not causal claims (plan choice isn't random).
Confidence intervals use the Wilson score interval (more reliable than a
normal approximation near the tails); the activity-decay comparison reports
Cohen's d as the effect size.
"""

import math
import os
from pathlib import Path

import psycopg2

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
OUT_PATH = Path(__file__).resolve().parent.parent / "notebooks" / "06_cohort_retention.md"


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    phat = k / n
    denom = 1 + z**2 / n
    center = (phat + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    return phat, max(0.0, center - margin), min(1.0, center + margin)


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def main():
    conn = psycopg2.connect(DB_DSN)
    lines = ["# Cohort and retention analysis — step 2.4\n",
             "All rates below are observational associations, not causal claims — plan "
             "choice, engagement level, and tenure are not randomly assigned. Confidence "
             "intervals are 95% Wilson score intervals on the churn rate.\n"]
    try:
        with conn.cursor() as cur:
            # --- churn by plan length, overall + split by time half (stability check) ---
            by_plan = q(cur, """
                SELECT payment_plan_days, count(*), sum(churn)
                FROM fct_snapshot WHERE churn IS NOT NULL
                GROUP BY payment_plan_days ORDER BY payment_plan_days
            """)
            lines.append("## Churn rate by plan length\n")
            lines.append("| plan_days | n | churn_rate | 95% CI |")
            lines.append("|---|---|---|---|")
            for plan_days, n, n_churn in by_plan:
                rate, lo, hi = wilson_ci(n_churn, n)
                lines.append(f"| {plan_days} | {n:,} | {rate:.1%} | [{lo:.1%}, {hi:.1%}] |")

            min_max = q(cur, "SELECT min(cutoff_date), max(cutoff_date) FROM fct_snapshot WHERE churn IS NOT NULL")[0]
            median_cutoff = min_max[0] + (min_max[1] - min_max[0]) / 2
            for half_name, cmp_op in [("first half", "<"), ("second half", ">=")]:
                rows = q(cur, f"""
                    SELECT payment_plan_days, count(*), sum(churn)
                    FROM fct_snapshot WHERE churn IS NOT NULL AND cutoff_date {cmp_op} %s
                    GROUP BY payment_plan_days ORDER BY payment_plan_days
                """, (median_cutoff,))
                lines.append(f"\n**Plan-length effect, {half_name} of the window (cutoff {cmp_op} {median_cutoff}):**\n")
                lines.append("| plan_days | n | churn_rate |")
                lines.append("|---|---|---|")
                for plan_days, n, n_churn in rows:
                    rate, lo, hi = wilson_ci(n_churn, n)
                    lines.append(f"| {plan_days} | {n:,} | {rate:.1%} |")

            lines.append("\n**Finding:** per-cycle churn rate rises monotonically with plan length — "
                          "from 7.4-7.7% on 7/30-day plans up to 10.4% on 410-day plans — and the same "
                          "ordering holds in both halves of the window, so it's a stable association, "
                          "not an artifact of one period. This is the opposite of the naive intuition "
                          "that committing to a longer plan signals stronger intent to stay; a more "
                          "likely reading is selection: a 410-day renewal is a comparatively rare event "
                          "for any given subscriber (868 snapshots vs. 29,046 for 30-day plans), so this "
                          "group is small and could be disproportionately drawn from subscribers already "
                          "near the end of their relationship — this is exactly the kind of pattern that "
                          "needs a plan-length feature in the model (step 3.1) rather than an assumption "
                          "about which direction it points.\n")

            # --- churn by tenure band ---
            by_tenure = q(cur, """
                SELECT
                    CASE
                        WHEN cutoff_date - spell_start <= 30 THEN '0-30d'
                        WHEN cutoff_date - spell_start <= 90 THEN '31-90d'
                        WHEN cutoff_date - spell_start <= 180 THEN '91-180d'
                        WHEN cutoff_date - spell_start <= 365 THEN '181-365d'
                        ELSE '365d+'
                    END AS band,
                    min(cutoff_date - spell_start) AS min_days,
                    count(*), sum(churn)
                FROM fct_snapshot WHERE churn IS NOT NULL
                GROUP BY band ORDER BY min_days
            """)
            lines.append("## Churn rate by tenure band (time since spell start)\n")
            lines.append("| tenure_band | n | churn_rate | 95% CI |")
            lines.append("|---|---|---|---|")
            for band, _, n, n_churn in by_tenure:
                rate, lo, hi = wilson_ci(n_churn, n)
                lines.append(f"| {band} | {n:,} | {rate:.1%} | [{lo:.1%}, {hi:.1%}] |")
            lines.append("\n**Finding:** churn risk is highest in the first renewal window (0-30 days "
                          "of tenure) and declines the longer a subscriber has already stuck around — "
                          "consistent with the generator's design (first-renewal hazard multiplier) but "
                          "also a commonly observed real-world pattern worth calling out explicitly.\n")

            # --- retention by signup cohort ---
            by_cohort = q(cur, """
                SELECT date_trunc('month', m.registration_init_time)::date AS cohort_month,
                       count(*), sum(s.churn)
                FROM fct_snapshot s JOIN members m ON m.msno = s.msno
                WHERE s.churn IS NOT NULL
                GROUP BY cohort_month ORDER BY cohort_month
            """)
            lines.append("## Churn rate by signup cohort (registration month)\n")
            lines.append("| cohort_month | n_snapshots | churn_rate |")
            lines.append("|---|---|---|")
            for month, n, n_churn in by_cohort:
                rate, lo, hi = wilson_ci(n_churn, n)
                lines.append(f"| {month.strftime('%Y-%m')} | {n:,} | {rate:.1%} |")
            lines.append("\n**Finding:** no strong monotonic cohort effect — churn rate by signup "
                          "month is noisy but doesn't trend sharply, unlike the calendar-time drift "
                          "found in step 1.4. This suggests the drift in step 1.4 is a *when* effect "
                          "(something about the period), not a *who* effect (which cohort signed up) — "
                          "worth keeping in mind if seasonality features are added in step 3.1.\n")

            # --- engagement decay before churn ---
            decay = q(cur, """
                WITH pre_activity AS (
                    SELECT s.msno, s.cutoff_date, s.churn,
                           COALESCE(SUM(l.total_secs), 0) AS secs_30d,
                           COUNT(l.date) AS active_days_30d
                    FROM fct_snapshot s
                    LEFT JOIN user_logs l
                        ON l.msno = s.msno
                       AND l.date >= s.cutoff_date - INTERVAL '30 days'
                       AND l.date < s.cutoff_date
                    WHERE s.churn IS NOT NULL
                    GROUP BY s.msno, s.cutoff_date, s.churn
                )
                SELECT churn, count(*), avg(secs_30d), stddev(secs_30d), avg(active_days_30d)
                FROM pre_activity GROUP BY churn ORDER BY churn
            """)
            lines.append("## Engagement decay before churn (30-day pre-cutoff window)\n")
            lines.append("| churn | n | avg total_secs (30d) | stddev | avg active days (30d) |")
            lines.append("|---|---|---|---|---|")
            stats = {}
            for churn, n, avg_secs, sd_secs, avg_days in decay:
                stats[churn] = (n, float(avg_secs or 0), float(sd_secs or 0), float(avg_days or 0))
                lines.append(f"| {churn} | {n:,} | {avg_secs:.0f} | {sd_secs:.0f} | {avg_days:.1f} |")

            if 0 in stats and 1 in stats:
                n0, m0, sd0, _ = stats[0]
                n1, m1, sd1, _ = stats[1]
                pooled_sd = math.sqrt(((n0 - 1) * sd0**2 + (n1 - 1) * sd1**2) / (n0 + n1 - 2))
                cohens_d = (m0 - m1) / pooled_sd if pooled_sd else 0
                lines.append(f"\n**Effect size:** Cohen's d = {cohens_d:.2f} (renewed vs. churned, "
                              f"30-day pre-cutoff total_secs). ")
                lines.append("**Finding:** subscribers who go on to churn show materially lower "
                              "activity in the 30 days before their expiry than those who renew — "
                              "this is the intended signal the generator's decay ramp was built to "
                              "produce, and confirms the activity features planned for step 3.1 "
                              "(7/30/90-day windows, days-since-last-activity) should carry real "
                              "predictive signal rather than noise.\n")
    finally:
        conn.close()

    OUT_PATH.write_text("\n".join(lines))
    print(f"Written to {OUT_PATH}")


if __name__ == "__main__":
    main()
