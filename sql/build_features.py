import os
import time
from pathlib import Path

import psycopg2

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
SQL_PATH = Path(__file__).resolve().parent / "build_features.sql"


def main():
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn.cursor() as cur:
            t0 = time.time()
            cur.execute(SQL_PATH.read_text())
            elapsed = time.time() - t0
            cur.execute("SELECT count(*) FROM fct_features")
            (n,) = cur.fetchone()
            cur.execute("SELECT count(*) FROM fct_features WHERE churn IS NOT NULL")
            (n_labeled,) = cur.fetchone()
        conn.commit()
        print(f"fct_features: {n:,} rows ({n_labeled:,} labeled) built in {elapsed:.1f}s")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
