import os
from pathlib import Path

import psycopg2

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
SQL_PATH = Path(__file__).resolve().parent / "build_spells.sql"


def main():
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_PATH.read_text())
            cur.execute("SELECT count(*) FROM fct_membership_spell")
            (n,) = cur.fetchone()
            cur.execute("SELECT count(DISTINCT msno) FROM fct_membership_spell")
            (n_users,) = cur.fetchone()
        conn.commit()
        print(f"fct_membership_spell: {n:,} spells across {n_users:,} users")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
