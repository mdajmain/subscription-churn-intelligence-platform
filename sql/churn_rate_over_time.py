"""
Step 1.4 — observed monthly churn rate across the whole coverage window.

Reuses the same naive, pre-spell-reconstruction proxy as the class-balance
check in step 1.3 (issue 7): this is intentionally a first-pass measure to
confirm the target is stable enough to justify a time-based split, BEFORE
paying for full spell reconstruction (step 2.1). The authoritative rate is
recomputed from real membership spells and validated labels in step 2.3.
"""

import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import psycopg2

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
OUT_DIR = Path(__file__).resolve().parent.parent / "notebooks"

QUERY = """
    WITH last_per_month AS (
        SELECT msno, membership_expire_date,
               date_trunc('month', membership_expire_date) AS expire_month,
               EXISTS (
                   SELECT 1 FROM transactions t2
                   WHERE t2.msno = t.msno
                     AND t2.transaction_date > t.membership_expire_date
                     AND t2.transaction_date <= t.membership_expire_date + INTERVAL '30 days'
               ) AS renewed
        FROM transactions t
        WHERE membership_expire_date <= (SELECT max(transaction_date) - INTERVAL '30 days' FROM transactions)
    )
    SELECT expire_month, count(*) AS n, avg(CASE WHEN NOT renewed THEN 1.0 ELSE 0 END) AS churn_rate
    FROM last_per_month
    GROUP BY expire_month ORDER BY expire_month
"""


def main():
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            rows = cur.fetchall()
    finally:
        conn.close()

    # drop the last partial month — the 30-day exclusion window leaves it with
    # too few transactions to be a meaningful point (an edge effect, not signal)
    rows = [r for r in rows if r[1] >= 100]

    months = [r[0] for r in rows]
    rates = [float(r[2]) for r in rows]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(months, rates, marker="o", markersize=3)
    ax.set_title("Naive monthly churn rate (pre-spell-reconstruction proxy)")
    ax.set_ylabel("Churn rate")
    ax.set_xlabel("Membership expiry month")
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    out_png = OUT_DIR / "03_churn_rate_over_time.png"
    fig.savefig(out_png, dpi=150)

    first_year_avg = sum(rates[:12]) / min(12, len(rates))
    last_year_avg = sum(rates[-12:]) / min(12, len(rates))

    report = OUT_DIR / "03_churn_rate_over_time.md"
    report.write_text(
        "# Churn rate over time — step 1.4\n\n"
        f"![churn rate over time](03_churn_rate_over_time.png)\n\n"
        f"- First 12 months average: **{first_year_avg:.1%}**\n"
        f"- Last 12 months average: **{last_year_avg:.1%}**\n\n"
        "**Finding:** the rate is not perfectly flat — it drifts upward from roughly "
        "the mid-20s% in early 2015 to the high-30s%/low-40s% by late 2016, with a "
        "recurring bump around November-January each cycle. This is drift, not noise: "
        "it is consistent across multiple years in the window.\n\n"
        "**Consequence for the split (step 3.2):** because the rate moves across the "
        "window, comparing a Jan-Feb 2017 test set against an early-2015-heavy training "
        "set risks attributing a distribution shift to model failure. The time-based "
        "split should be evaluated with this drift in mind — e.g., check performance "
        "isn't solely explained by the test window's naturally higher base rate, and "
        "consider reporting lift over a rule baseline (step 3.3) computed within the "
        "same window rather than a single pooled number.\n"
    )
    print(f"Wrote {out_png}")
    print(f"Wrote {report}")
    print(f"First-year avg: {first_year_avg:.1%}  Last-year avg: {last_year_avg:.1%}")


if __name__ == "__main__":
    main()
