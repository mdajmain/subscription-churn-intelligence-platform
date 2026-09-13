import os
from pathlib import Path

import psycopg2

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
SQL_PATH = Path(__file__).resolve().parent / "build_snapshots.sql"


def main():
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_PATH.read_text())
            cur.execute("SELECT count(*) FROM fct_snapshot")
            (n,) = cur.fetchone()
            cur.execute("SELECT count(*) FROM fct_snapshot WHERE churn IS NULL")
            (n_censored,) = cur.fetchone()
            cur.execute("SELECT avg(churn::float) FROM fct_snapshot WHERE churn IS NOT NULL")
            (rate,) = cur.fetchone()
            cur.execute("""
                SELECT count(*) FROM (
                    SELECT msno, cutoff_date FROM fct_snapshot GROUP BY msno, cutoff_date HAVING count(*) > 1
                ) d
            """)
            (dupes,) = cur.fetchone()
        conn.commit()
        print(f"fct_snapshot: {n:,} rows")
        print(f"  censored (no label): {n_censored:,}")
        print(f"  churn rate (labeled rows): {rate:.1%}")
        print(f"  (msno, cutoff_date) duplicate groups: {dupes:,}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
