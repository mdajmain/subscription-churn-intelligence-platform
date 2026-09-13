"""
Step 7.3 — idempotency check: replay the daily batch for a date already
processed and confirm model_predictions is unchanged, not doubled.
Requires a live database (skipped if unreachable, same convention as
tests/test_no_leakage.py).
"""

import os

import psycopg2
import pytest

from api.batch_score import run

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")


def _db_available():
    try:
        psycopg2.connect(DB_DSN).close()
        return True
    except Exception:
        return False


def _pick_a_cutoff_date():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("select cutoff_date from fct_prediction group by cutoff_date order by count(*) desc limit 1")
    row = cur.fetchone()
    conn.close()
    return str(row[0])


@pytest.mark.skipif(not _db_available(), reason="requires a live Postgres (docker compose up -d)")
def test_replaying_a_batch_does_not_duplicate_rows():
    cutoff_date = _pick_a_cutoff_date()

    n_first = run(cutoff_date)
    assert n_first > 0

    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("select count(*) from model_predictions where cutoff_date = %s", (cutoff_date,))
    count_after_first = cur.fetchone()[0]
    cur.execute("""
        select msno, calibrated_churn_probability from model_predictions
        where cutoff_date = %s order by msno
    """, (cutoff_date,))
    values_after_first = cur.fetchall()
    conn.close()

    n_second = run(cutoff_date)
    assert n_second == n_first, "batch should score the same number of rows on replay"

    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("select count(*) from model_predictions where cutoff_date = %s", (cutoff_date,))
    count_after_second = cur.fetchone()[0]
    cur.execute("""
        select msno, calibrated_churn_probability from model_predictions
        where cutoff_date = %s order by msno
    """, (cutoff_date,))
    values_after_second = cur.fetchall()
    conn.close()

    assert count_after_second == count_after_first, (
        f"replay duplicated rows: {count_after_first} -> {count_after_second}"
    )
    assert values_after_second == values_after_first, (
        "replay produced different scores for the same date -- scoring should be deterministic"
    )
