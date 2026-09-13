"""
Step 2.1 "done when": spot-check spells by hand against 10 users' raw
transaction histories. Picks users with more than one transaction (a single
row is trivially correct) and prints raw transactions next to reconstructed
spells for manual review.
"""

import os

import psycopg2

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")


def main():
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT msno FROM transactions
                GROUP BY msno HAVING count(*) > 3
                ORDER BY md5(msno || 'spotcheck-seed') LIMIT 10
            """)
            users = [r[0] for r in cur.fetchall()]

            for msno in users:
                print(f"\n=== {msno} ===")
                cur.execute("""
                    SELECT transaction_date, membership_expire_date, is_cancel, is_auto_renew, payment_plan_days
                    FROM transactions WHERE msno = %s ORDER BY transaction_date
                """, (msno,))
                print("raw transactions (transaction_date, expire, is_cancel, auto_renew, plan_days):")
                for row in cur.fetchall():
                    print("   ", row)

                cur.execute("""
                    SELECT spell_id, spell_start, spell_expire, n_transactions, had_cancel_event
                    FROM fct_membership_spell WHERE msno = %s ORDER BY spell_id
                """, (msno,))
                print("reconstructed spells (spell_id, start, expire, n_tx, had_cancel):")
                for row in cur.fetchall():
                    print("   ", row)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
