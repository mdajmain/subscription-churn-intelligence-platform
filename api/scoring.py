"""
Loads the model artifact saved by models/score_predictions.py (fitted
preprocessor + calibrated XGBoost) and scores a single feature row. Shared
by the FastAPI /predict endpoint (api/main.py) and the daily batch job
(api/batch_score.py) so both use the exact same scoring path -- an API
that scored differently from the batch job would be its own class of bug.
"""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "models" / "artifacts" / "churn_model.joblib"

_artifact = None


def get_artifact():
    global _artifact
    if _artifact is None:
        if not ARTIFACT_PATH.exists():
            raise FileNotFoundError(
                f"No model artifact at {ARTIFACT_PATH}. Run `python3 models/score_predictions.py` first "
                "(it trains, calibrates, and saves the artifact this API and the batch job both load)."
            )
        _artifact = joblib.load(ARTIFACT_PATH)
    return _artifact


def clean_row(row: dict, boolean_features: list[str]) -> dict:
    row = dict(row)
    row.setdefault("gender", None)
    if row.get("gender") in (None, ""):
        row["gender"] = "missing"
    for b in boolean_features:
        if b in row and row[b] is not None:
            row[b] = int(row[b])
    return row


def score_rows(rows: list[dict]) -> list[dict]:
    """rows: list of feature dicts (must contain every key in artifact['feature_names']).
    Returns one dict per row: calibrated_churn_probability + top_contributing_features."""
    artifact = get_artifact()
    feature_names = artifact["feature_names"]
    boolean = artifact["boolean"]

    cleaned = [clean_row(r, boolean) for r in rows]
    df = pd.DataFrame(cleaned)[feature_names]

    pre = artifact["preprocessor"]
    calibrated = artifact["calibrated_model"]
    raw_model = artifact["raw_model"]

    X = pre.transform(df)
    X_dense = X.toarray() if hasattr(X, "toarray") else X
    probs = calibrated.predict_proba(X_dense)[:, 1]

    import xgboost as xgb
    contribs = raw_model.get_booster().predict(xgb.DMatrix(X_dense), pred_contribs=True)[:, :-1]
    out_feature_names = pre.get_feature_names_out()
    top_idx = np.argsort(-np.abs(contribs), axis=1)[:, :3]

    results = []
    for i in range(len(cleaned)):
        top = [f"{out_feature_names[j]}:{contribs[i, j]:+.3f}" for j in top_idx[i]]
        results.append({
            "calibrated_churn_probability": float(probs[i]),
            "top_contributing_features": top,
            "model_version": artifact["model_version"],
        })
    return results
