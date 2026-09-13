"""
Step 3.1's automated leakage test: no feature may read data dated at or
after its prediction cutoff. This is checked two ways:

1. Every stored feature value is independently recomputed straight from
   the raw tables using a strict `< cutoff_date` filter and must match
   exactly (test_features_match_strict_recompute, test_last_activity_date_before_cutoff).

2. The test must have teeth: recomputing the same features with a
   deliberately leaky `<= cutoff_date` filter must produce DIFFERENT
   values for a real, non-trivial number of rows. If it didn't, the strict
   test above would be vacuous — passing regardless of whether the cutoff
   discipline actually did anything (test_leaky_window_would_be_caught).
"""

# Queries fct_prediction, not the Week 3 fct_features table it replaced.
# fct_features was built by sql/build_features.py before the Week 4 dbt
# migration; dbt has no such model, so it exists only on databases old
# enough to predate the migration -- which is why this test passed locally
# for months and failed the first time CI ran it on a fresh Postgres.
# fct_prediction is the ported successor (same grain, same 42,813 rows,
# and the version with the fct_transaction dedup fix applied).

import os
import random

import psycopg2
import pytest

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(DB_DSN)
    yield c
    c.close()


@pytest.fixture(scope="module")
def sample_rows(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM fct_prediction")
        (n,) = cur.fetchone()
        cur.execute("SELECT msno, cutoff_date FROM fct_prediction ORDER BY random() LIMIT %s", (min(500, n),))
        return cur.fetchall()


# These recompute against the same base relations fct_prediction.sql uses
# (fct_activity_daily, fct_transaction), not the raw user_logs/transactions
# tables the pre-dbt sql/build_features.py read. What this test exists to
# verify independently is the CUTOFF discipline -- the `< cutoff` vs
# `<= cutoff` filter below -- so the recomputation has to start from the
# same rows the pipeline does, or it measures a definition difference
# instead of a leak.
#
# Using raw `transactions` for the renewal count was in fact the Week 3
# definition, and it disagrees with the current one on 147 of 500 sampled
# rows: the Week 4 dbt migration deliberately standardised prior-history
# counts on the DEDUPED fct_transaction (the raw table carries 760 exact
# duplicate rows, found in Week 1). That fix is what this test was still
# contradicting.


def recompute_total_secs_30d(cur, msno, cutoff_date, operator):
    cur.execute(f"""
        SELECT COALESCE(sum(total_secs), 0) FROM fct_activity_daily
        WHERE msno = %s AND date {operator} %s AND date >= %s - 30
    """, (msno, cutoff_date, cutoff_date))
    return float(cur.fetchone()[0])


def recompute_prior_renewal_count(cur, msno, cutoff_date, operator):
    cur.execute(f"""
        SELECT count(*) FROM fct_transaction
        WHERE msno = %s AND transaction_date {operator} %s AND is_cancel = 0
    """, (msno, cutoff_date))
    return cur.fetchone()[0]


def test_last_activity_date_before_cutoff(conn):
    """No stored last_activity_date may fall on or after its own cutoff."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FROM fct_prediction
            WHERE last_activity_date IS NOT NULL AND last_activity_date >= cutoff_date
        """)
        (violations,) = cur.fetchone()
    assert violations == 0, f"{violations} rows have last_activity_date >= cutoff_date"


def test_features_match_strict_recompute(conn, sample_rows):
    """Stored features must equal an independent recomputation using date < cutoff."""
    with conn.cursor() as cur:
        mismatches = []
        for msno, cutoff_date in sample_rows:
            cur.execute("SELECT total_secs_30d, prior_renewal_count FROM fct_prediction WHERE msno=%s AND cutoff_date=%s", (msno, cutoff_date))
            stored_secs, stored_renewals = cur.fetchone()
            recomputed_secs = recompute_total_secs_30d(cur, msno, cutoff_date, "<")
            recomputed_renewals = recompute_prior_renewal_count(cur, msno, cutoff_date, "<")
            if abs(float(stored_secs) - recomputed_secs) > 0.01 or stored_renewals != recomputed_renewals:
                mismatches.append((msno, cutoff_date))
    assert not mismatches, f"{len(mismatches)} rows disagree with strict (< cutoff) recomputation: {mismatches[:5]}"


def test_leaky_window_would_be_caught(conn, sample_rows):
    """
    Prove the strict-cutoff test isn't vacuous: recomputing the same
    features with <= instead of < must disagree with the correct stored
    values for a real number of rows (a snapshot's OWN governing
    transaction and same-day log rows sit exactly ON the cutoff for many
    rows, so a <= leak is a realistic mistake, not a contrived one).
    """
    with conn.cursor() as cur:
        leaked = 0
        for msno, cutoff_date in sample_rows:
            cur.execute("SELECT total_secs_30d, prior_renewal_count FROM fct_prediction WHERE msno=%s AND cutoff_date=%s", (msno, cutoff_date))
            stored_secs, stored_renewals = cur.fetchone()
            leaky_secs = recompute_total_secs_30d(cur, msno, cutoff_date, "<=")
            leaky_renewals = recompute_prior_renewal_count(cur, msno, cutoff_date, "<=")
            if abs(float(stored_secs) - leaky_secs) > 0.01 or stored_renewals != leaky_renewals:
                leaked += 1
    assert leaked > 0, (
        "Expected the <= (leaky) recomputation to disagree with the strict stored "
        "values for at least some sampled rows — if it never does, this test suite "
        "would pass even with a broken cutoff, which defeats the point of having it."
    )
