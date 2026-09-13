"""
Step 7.1 test: /health, /metrics, /predict/features against the FastAPI
app directly (no running server, no live database needed for
/predict/features -- only /health and /metrics touch Postgres, and
/health is designed to report "unhealthy" rather than crash if the
database is unreachable).
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.scoring import get_artifact

client = TestClient(app)


def test_health_returns_200_or_503_with_a_body():
    r = client.get("/health")
    assert r.status_code in (200, 503)
    body = r.json() if r.status_code == 200 else r.json()["detail"]
    assert "database" in body
    assert "model_artifact" in body


def test_metrics_reports_model_version():
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "model_version" in body
    assert "test_pr_auc" in body


def test_predict_from_features_scores_a_row():
    artifact = get_artifact()
    # a plausible "healthy, engaged, auto-renew on" subscriber
    row = {f: 0 for f in artifact["feature_names"]}
    row.update({
        "bd": 30, "payment_plan_days": 30, "plan_list_price": 149, "actual_amount_paid": 149,
        "tenure_since_registration_days": 400, "is_auto_renew": 1, "bd_is_valid": True,
        "city": 1, "gender": "male", "registered_via": 7, "payment_method_id": 41,
        "total_secs_7d": 50000, "total_secs_30d": 200000, "total_secs_90d": 600000,
        "active_days_7d": 6, "active_days_30d": 25, "active_days_90d": 80,
        "days_since_last_activity": 1,
    })
    r = client.post("/predict/features", json={"features": row})
    assert r.status_code == 200
    body = r.json()
    assert 0.0 <= body["calibrated_churn_probability"] <= 1.0
    assert len(body["top_contributing_features"]) == 3


def test_predict_from_features_missing_feature_returns_422():
    r = client.post("/predict/features", json={"features": {"bd": 30}})
    assert r.status_code == 422
