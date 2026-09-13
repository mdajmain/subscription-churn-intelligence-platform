"""
Step 7.2/7.3 — the daily batch scoring job (what EventBridge's schedule
would invoke on the ECS task in a real deploy: `python3 -m api.batch_score
--date 2017-02-15`, see infra/README.md).

Unlike models/score_predictions.py (a full retrain+recalibrate+rescore
run over every fct_prediction row, meant to be run occasionally to
refresh the model itself), this script does not train anything -- it
loads the already-trained artifact and scores only the snapshots for one
cutoff_date, then UPSERTs them into model_predictions. That upsert (not
truncate-and-reload) is what step 7.3's idempotency check is about:
replaying the same date must leave the table in the same state, not
duplicate it. tests/test_idempotency.py runs this script twice for the
same date and asserts the row count and values don't change.
"""

import argparse
import os
from datetime import date

import psycopg2
import psycopg2.extras

from api.scoring import get_artifact, score_rows


DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")


def run(cutoff_date: str, dsn: str = DB_DSN) -> int:
    """Scores every fct_prediction row with this cutoff_date and upserts
    into model_predictions. Returns the number of rows written."""
    artifact = get_artifact()
    feature_cols = ", ".join(artifact["feature_names"])

    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"select msno, cutoff_date, {feature_cols} from fct_prediction where cutoff_date = %s",
            (cutoff_date,),
        )
        rows = cur.fetchall()
        if not rows:
            return 0

        feature_rows = [{k: r[k] for k in artifact["feature_names"]} for r in rows]
        results = score_rows(feature_rows)

        write_cur = conn.cursor()
        write_cur.execute("""
            CREATE TABLE IF NOT EXISTS model_predictions (
                msno TEXT NOT NULL,
                cutoff_date DATE NOT NULL,
                calibrated_churn_probability DOUBLE PRECISION NOT NULL,
                model_version TEXT NOT NULL,
                top_contributing_features TEXT,
                PRIMARY KEY (msno, cutoff_date)
            )
        """)
        for r, result in zip(rows, results):
            write_cur.execute("""
                INSERT INTO model_predictions (msno, cutoff_date, calibrated_churn_probability, model_version, top_contributing_features)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (msno, cutoff_date) DO UPDATE SET
                    calibrated_churn_probability = EXCLUDED.calibrated_churn_probability,
                    model_version = EXCLUDED.model_version,
                    top_contributing_features = EXCLUDED.top_contributing_features
            """, (
                r["msno"], r["cutoff_date"], result["calibrated_churn_probability"],
                result["model_version"], ", ".join(result["top_contributing_features"]),
            ))
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Daily batch scoring: score one cutoff_date's snapshots and upsert.")
    parser.add_argument(
        "--date", default=None,
        help="cutoff_date to score, YYYY-MM-DD. Defaults to today (UTC) -- "
             "EventBridge's scheduled trigger (infra/eventbridge.tf) doesn't pass "
             "--date at all, relying on this default, since EventBridge's input "
             "transformer has no built-in way to format 'today' as YYYY-MM-DD.",
    )
    args = parser.parse_args()
    run_date = args.date or date.today().isoformat()
    date.fromisoformat(run_date)  # validates format early, before touching the DB
    n = run(run_date)
    print(f"Scored and upserted {n} row(s) for cutoff_date={run_date}")


if __name__ == "__main__":
    main()
