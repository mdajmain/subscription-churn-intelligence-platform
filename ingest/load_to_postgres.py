"""
Loads the synthetic CSVs into Postgres with COPY (not a pandas.to_sql loop
per row) — per BUILD-GUIDE.md step 1.1, COPY is ~two orders of magnitude
faster and is what you'd use in production.
"""

import os
from pathlib import Path

import psycopg2

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

TABLES = {
    "members.csv": "members",
    "transactions.csv": "transactions",
    "user_logs.csv": "user_logs",
    "ground_truth_membership.csv": "ground_truth_membership",
}


def main():
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute((SQL_DIR / "schema.sql").read_text())
        conn.commit()
        print("Schema created.")

        with conn.cursor() as cur:
            for csv_name, table in TABLES.items():
                path = DATA_DIR / csv_name
                with open(path, "r") as f:
                    cur.copy_expert(f"COPY {table} FROM STDIN WITH CSV HEADER", f)
                cur.execute(f"SELECT count(*) FROM {table}")
                (count,) = cur.fetchone()
                print(f"  {table:<28} {count:,} rows")
        conn.commit()
        print("Load complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
