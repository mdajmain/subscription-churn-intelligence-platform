"""
Step 7.1 — FastAPI serving layer: /predict, /metrics, /health.

Scores live, from the artifact saved by models/score_predictions.py --
does not just look up a precomputed row in `model_predictions`, since a
serving API that could only replay yesterday's batch wouldn't be much of
a serving API. /predict optionally takes an msno (looks its most recent
fct_prediction features up in Postgres) or a raw feature payload directly,
so it can be tested without a live database.
"""

import os
from datetime import date, datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from api.scoring import get_artifact, score_rows

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")

app = FastAPI(title="Churn Intelligence API", version="1.0")


def get_conn():
    return psycopg2.connect(DB_DSN)


class PredictByMsnoRequest(BaseModel):
    msno: str
    cutoff_date: Optional[date] = Field(
        default=None,
        description="If omitted, uses that subscriber's most recent fct_prediction snapshot.",
    )


class PredictByFeaturesRequest(BaseModel):
    features: dict = Field(description="Must contain every column models/train.py's ALL_FEATURES lists.")


class PredictResponse(BaseModel):
    msno: Optional[str] = None
    cutoff_date: Optional[str] = None
    calibrated_churn_probability: float
    top_contributing_features: list[str]
    model_version: str


@app.get("/health")
def health():
    db_ok = True
    db_error = None
    try:
        conn = get_conn()
        conn.close()
    except Exception as e:
        db_ok = False
        db_error = str(e)

    model_ok = True
    model_error = None
    try:
        get_artifact()
    except Exception as e:
        model_ok = False
        model_error = str(e)

    healthy = db_ok and model_ok
    body = {
        "status": "ok" if healthy else "unhealthy",
        "database": "ok" if db_ok else f"error: {db_error}",
        "model_artifact": "ok" if model_ok else f"error: {model_error}",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if not healthy:
        raise HTTPException(status_code=503, detail=body)
    return body


@app.get("/metrics")
def metrics():
    artifact = get_artifact()
    body = {
        "model_version": artifact["model_version"],
        "test_pr_auc": artifact["test_pr_auc"],
        "test_brier": artifact["test_brier"],
    }
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            select count(*) as n_scored,
                   max(cutoff_date) as latest_cutoff_date,
                   avg(calibrated_churn_probability) as avg_predicted_churn_probability
            from model_predictions
        """)
        body.update(cur.fetchone())
        conn.close()
    except Exception as e:
        body["database_metrics_error"] = str(e)
    body["checked_at"] = datetime.now(timezone.utc).isoformat()
    return body


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictByMsnoRequest):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        artifact = get_artifact()
        feature_cols = ", ".join(artifact["feature_names"])
        if req.cutoff_date:
            cur.execute(
                f"select msno, cutoff_date, {feature_cols} from fct_prediction "
                f"where msno = %s and cutoff_date = %s",
                (req.msno, req.cutoff_date),
            )
        else:
            cur.execute(
                f"select msno, cutoff_date, {feature_cols} from fct_prediction "
                f"where msno = %s order by cutoff_date desc limit 1",
                (req.msno,),
            )
        row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail=f"No fct_prediction snapshot found for msno={req.msno!r}")

    feature_row = {k: row[k] for k in artifact["feature_names"]}
    result = score_rows([feature_row])[0]
    return PredictResponse(
        msno=row["msno"], cutoff_date=str(row["cutoff_date"]),
        **result,
    )


@app.post("/predict/features", response_model=PredictResponse)
def predict_from_features(req: PredictByFeaturesRequest):
    """Score an arbitrary feature payload directly, bypassing the database --
    used by tests/test_api.py so the endpoint doesn't need a live Postgres."""
    try:
        result = score_rows([req.features])[0]
    except KeyError as e:
        raise HTTPException(status_code=422, detail=f"Missing required feature: {e}")
    return PredictResponse(**result)
