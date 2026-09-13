"""
Step 1.3 — investigate the eight known data-quality issues. Each function
runs the diagnostic query and returns numbers; main() stitches numbers into
a written finding + handling decision and saves the whole thing as a report.

This is deliberately SQL-first (per BUILD-GUIDE.md 1.1: "SQL is what data
analyst interviews test") — the Python here is just a runner and formatter.
"""

import os
from pathlib import Path

import psycopg2

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
OUT_PATH = Path(__file__).resolve().parent.parent / "notebooks" / "02_data_quality_findings.md"


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def issue_1_duplicates(cur):
    exact = q(cur, "SELECT count(*) - count(DISTINCT (msno, payment_method_id, payment_plan_days, "
                    "plan_list_price, actual_amount_paid, is_auto_renew, transaction_date, "
                    "membership_expire_date, is_cancel)) FROM transactions")[0][0]
    near = q(cur, """
        SELECT count(*) FROM (
            SELECT msno, transaction_date, count(*) AS n
            FROM transactions
            GROUP BY msno, transaction_date
            HAVING count(*) > 1
        ) t
    """)[0][0]
    return exact, near


def issue_2_bd(cur):
    negative = q(cur, "SELECT count(*) FROM members WHERE bd < 0")[0][0]
    huge = q(cur, "SELECT count(*) FROM members WHERE bd > 110")[0][0]
    plausible = q(cur, "SELECT count(*) FROM members WHERE bd BETWEEN 10 AND 90")[0][0]
    return negative, huge, plausible


def issue_3_is_cancel(cur):
    total_cancels = q(cur, "SELECT count(*) FROM transactions WHERE is_cancel = 1")[0][0]
    resubscribe_within_7d = q(cur, """
        SELECT count(*) FROM transactions c
        WHERE c.is_cancel = 1 AND EXISTS (
            SELECT 1 FROM transactions t2
            WHERE t2.msno = c.msno AND t2.is_cancel = 0
              AND t2.transaction_date > c.transaction_date
              AND t2.transaction_date <= c.transaction_date + INTERVAL '7 days'
        )
    """)[0][0]
    return total_cancels, resubscribe_within_7d


def issue_4_same_day(cur):
    return q(cur, """
        SELECT count(*) FROM (
            SELECT msno, transaction_date FROM transactions
            GROUP BY msno, transaction_date HAVING count(*) > 1
        ) t
    """)[0][0]


def issue_5_zero_value(cur):
    zero = q(cur, "SELECT count(*) FROM transactions WHERE actual_amount_paid = 0")[0][0]
    zero_not_cancel = q(cur, "SELECT count(*) FROM transactions WHERE actual_amount_paid = 0 AND is_cancel = 0")[0][0]
    return zero, zero_not_cancel


def issue_6_missing_logs(cur):
    return q(cur, """
        SELECT count(DISTINCT t.msno) FROM transactions t
        LEFT JOIN user_logs l ON l.msno = t.msno
        WHERE l.msno IS NULL
    """)[0][0]


def issue_7_class_balance(cur):
    # Naive, pre-spell-reconstruction proxy: for each transaction row, did a LATER
    # transaction for the same user occur within 30 days of this row's expiry?
    # This is intentionally approximate — the rigorous version is built in step 2.1-2.3.
    #
    # Reference date for "can we have observed a renewal yet" must be the data
    # COLLECTION cutoff (max transaction_date — when transactions stopped being
    # recorded), not max membership_expire_date. Expiry dates from long plans
    # (410 days) run a year past the last recorded transaction, so using expiry
    # as the reference right-censors every recent month to a false 100% churn.
    # (Caught this by eyeballing the output — recent months read as 100% churn,
    # which is the tell for censoring rather than a real trend.)
    return q(cur, """
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
        SELECT expire_month, count(*) AS n, avg(CASE WHEN NOT renewed THEN 1.0 ELSE 0 END) AS naive_churn_rate
        FROM last_per_month
        GROUP BY expire_month ORDER BY expire_month
    """)


def main():
    conn = psycopg2.connect(DB_DSN)
    lines = ["# Data quality findings — step 1.3\n"]
    try:
        with conn.cursor() as cur:
            exact, near = issue_1_duplicates(cur)
            lines.append("## 1. Duplicate transactions")
            lines.append(f"- Exact duplicate rows (all columns identical): **{exact:,}**")
            lines.append(f"- (msno, transaction_date) groups with >1 row (includes near-dupes and legitimate same-day multi-tx): **{near:,}**")
            lines.append("- **Decision:** dedup key is the full row (all columns) — drop exact duplicates. "
                          "Rows that share (msno, transaction_date) but differ in amount/method are NOT "
                          "deduplicated; they are kept and disambiguated by transaction semantics (issue 4).\n")

            negative, huge, plausible = issue_2_bd(cur)
            lines.append("## 2. `bd` (age) outliers")
            lines.append(f"- Negative values: **{negative:,}**")
            lines.append(f"- Values > 110: **{huge:,}**")
            lines.append(f"- Plausible range [10, 90]: **{plausible:,}** of {plausible+negative+huge:,}")
            lines.append("- **Decision:** treat `bd` outside [10, 90] as missing (set to NULL at the feature layer, "
                          "carry a separate `bd_is_valid` flag). Do not drop the rows — age is not the join key "
                          "and the rest of the member record is still usable.\n")

            total_cancels, resub = issue_3_is_cancel(cur)
            rate = resub / total_cancels if total_cancels else 0
            lines.append("## 3. `is_cancel` semantics")
            lines.append(f"- Total `is_cancel=1` rows: **{total_cancels:,}**")
            lines.append(f"- Of those, followed by a new (non-cancel) transaction within 7 days: **{resub:,}** ({rate:.1%})")
            lines.append("- **Decision:** `is_cancel` is never used as the churn target. A cancel followed by a "
                          "prompt resubscription is a plan change, not a lapse. The churn label (step 2.3) is "
                          "defined purely on absence of a later membership-extending transaction.\n")

            same_day = issue_4_same_day(cur)
            lines.append("## 4. Same-day transactions")
            lines.append(f"- (msno, transaction_date) pairs with more than one transaction row: **{same_day:,}**")
            lines.append("- **Decision:** when multiple rows share a transaction_date, the row with the latest "
                          "`membership_expire_date` governs the effective expiry (mirrors 'last write wins' in "
                          "the spell-reconstruction window function in step 2.1); a same-day `is_cancel=1` row is "
                          "superseded by a same-day non-cancel row.\n")

            zero, zero_not_cancel = issue_5_zero_value(cur)
            lines.append("## 5. Zero-value transactions")
            lines.append(f"- `actual_amount_paid = 0` rows: **{zero:,}**")
            lines.append(f"- Of those, not flagged as a cancel: **{zero_not_cancel:,}** (trials/promotions)")
            lines.append("- **Decision:** zero-value non-cancel transactions are kept as legitimate membership "
                          "events (they still extend `membership_expire_date`); they are excluded from the "
                          "revenue-exposure calculation in step 6.1 since they carry no renewal value signal.\n")

            missing_logs = issue_6_missing_logs(cur)
            lines.append("## 6. Missing activity logs")
            lines.append(f"- Users present in `transactions` with zero rows in `user_logs`: **{missing_logs:,}**")
            lines.append("- **Decision:** absence of a log row is ambiguous (no activity vs. nothing recorded). "
                          "A `has_activity_data` flag is carried at the feature layer; missing activity is never "
                          "silently filled with zero.\n")

            balance_rows = issue_7_class_balance(cur)
            lines.append("## 7. Class balance over time (naive, pre-spell-reconstruction proxy)")
            lines.append("| expire_month | n_transactions | naive_churn_rate |")
            lines.append("|---|---|---|")
            for month, n, rate in balance_rows:
                lines.append(f"| {month.strftime('%Y-%m')} | {n:,} | {rate:.1%} |")
            lines.append("\n- **Note on censoring:** the reference date for \"has enough time passed to observe a "
                          "renewal\" must be `max(transaction_date)` — when the data stops being collected — not "
                          "`max(membership_expire_date)`. Long plans (410 days) push expiry a year past the last "
                          "recorded transaction; using expiry as the reference right-censors every recent month to "
                          "a false 100% churn. First run of this query showed exactly that (100% from 2017-04 "
                          "onward) before the reference date was fixed.")
            lines.append("- **Decision:** this is a rough pre-spell-reconstruction proxy (multiple transaction "
                          "rows per user per month, not one row per membership spell) — used only to sanity-check "
                          "that the rate doesn't move drastically month to month before committing to a time-based "
                          "split. The authoritative rate comes from step 1.4 / 2.3 after spells and labels are built.\n")

            lines.append("## 8. Feature availability at prediction time")
            lines.append("Assessed by design, not by query — for each candidate feature in step 3.1, confirm no "
                          "input row can be dated at or after the snapshot cutoff:\n")
            lines.append("| Candidate feature | Source table | Available at cutoff? |")
            lines.append("|---|---|---|")
            lines.append("| Tenure, prior renewal/cancel counts | transactions | Yes — computed from rows strictly before cutoff |")
            lines.append("| Current plan/price/auto-renew | transactions | Yes — the row that established the current spell, dated before cutoff |")
            lines.append("| 7/30/90-day activity aggregates | user_logs | Yes, if windowed strictly to `date < cutoff` |")
            lines.append("| Days since last activity | user_logs | Yes, same constraint |")
            lines.append("| `bd`, gender, city | members | Yes — static, not time-varying |")
            lines.append("- **Decision:** every feature query in step 3.1 filters on `< cutoff_date`, never `<=`, "
                          "and this is enforced by an automated test rather than left to convention.\n")
    finally:
        conn.close()

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text("\n".join(lines))
    print(f"Written to {OUT_PATH}")


if __name__ == "__main__":
    main()
