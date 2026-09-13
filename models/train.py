"""
Steps 3.2-3.4: time-based split, three models trained in order (rule
baseline -> logistic regression -> XGBoost), honest evaluation on an
untouched test set.

Split boundaries follow BUILD-GUIDE.md's table exactly: train through
Nov 2016, validation = Dec 2016, test = Jan-Feb 2017. No gap is inserted
BETWEEN the splits' cutoff ranges — the guide's "leave the full 30-day
label window clear" is read here as: reserve calendar time AFTER the test
window so test labels aren't censored (already enforced by fct_snapshot's
label_censored logic), not as a gap between split cutoffs. A train
snapshot's label looking forward into the validation calendar period isn't
feature leakage — features are already strictly pre-cutoff — it's just how
a 30-day-forward label is defined for any cutoff near a split boundary.
"""

import json
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import optuna
import pandas as pd
import psycopg2
import xgboost as xgb
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss,
                              precision_score, recall_score, roc_auc_score)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

optuna.logging.set_verbosity(optuna.logging.WARNING)

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
OUT_DIR = Path(__file__).resolve().parent.parent / "notebooks"
MODELS_DIR = Path(__file__).resolve().parent
SEED = 42

NUMERIC = [
    "bd", "payment_plan_days", "plan_list_price", "actual_amount_paid", "discount",
    "tenure_since_registration_days", "prior_renewal_count", "prior_cancellation_count",
    "distinct_payment_methods_used", "prior_plan_changes",
    "total_secs_7d", "total_secs_30d", "total_secs_90d", "num_unq_30d",
    "active_days_7d", "active_days_30d", "active_days_90d", "days_since_last_activity",
    "activity_ratio_7d_to_30d_avg", "share_plays_98_5plus_30d", "share_plays_below25_30d",
]
BOOLEAN = ["bd_is_valid", "is_auto_renew", "missing_activity_flag"]
CATEGORICAL = ["city", "gender", "registered_via", "payment_method_id"]
ALL_FEATURES = NUMERIC + BOOLEAN + CATEGORICAL


def load_data():
    conn = psycopg2.connect(DB_DSN)
    # fct_prediction, not the Week 3 fct_features table it replaced: dbt has
    # no fct_features model, so that name resolves only on databases predating
    # the Week 4 migration. Same grain and same 31 columns; `churn IS NOT NULL`
    # continues to mean "label resolved, not right-censored".
    df = pd.read_sql(f"""
        SELECT msno, cutoff_date, churn, {", ".join(ALL_FEATURES)}
        FROM fct_prediction WHERE churn IS NOT NULL
    """, conn)
    conn.close()
    df["cutoff_date"] = pd.to_datetime(df["cutoff_date"])
    df["gender"] = df["gender"].fillna("missing")
    for b in BOOLEAN:
        df[b] = df[b].astype(int)
    return df


def split(df):
    train = df[df.cutoff_date <= pd.Timestamp("2016-11-30")]
    val = df[(df.cutoff_date >= pd.Timestamp("2016-12-01")) & (df.cutoff_date <= pd.Timestamp("2016-12-31"))]
    test = df[(df.cutoff_date >= pd.Timestamp("2017-01-01")) & (df.cutoff_date <= pd.Timestamp("2017-02-28"))]
    return train, val, test


def build_preprocessor():
    return ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUMERIC),
        ("bool", "passthrough", BOOLEAN),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
    ])


def recall_precision_at_top_k(y_true, y_score, k_frac=0.05):
    n_top = max(1, int(len(y_true) * k_frac))
    order = np.argsort(-y_score)
    top_idx = order[:n_top]
    y_top = np.asarray(y_true)[top_idx]
    precision = y_top.mean()
    recall = y_top.sum() / np.asarray(y_true).sum()
    return precision, recall


def evaluate(name, y_true, y_score):
    pr_auc = average_precision_score(y_true, y_score)
    roc_auc = roc_auc_score(y_true, y_score)
    precision5, recall5 = recall_precision_at_top_k(y_true, y_score, 0.05)
    brier = brier_score_loss(y_true, y_score)
    return {
        "model": name, "pr_auc": pr_auc, "roc_auc": roc_auc,
        "precision_at_5pct": precision5, "recall_at_5pct": recall5, "brier": brier,
    }


def main():
    print("Loading fct_prediction...")
    df = load_data()
    train, val, test = split(df)
    print(f"train={len(train):,} (churn={train.churn.mean():.1%})  "
          f"val={len(val):,} (churn={val.churn.mean():.1%})  "
          f"test={len(test):,} (churn={test.churn.mean():.1%})")

    X_train, y_train = train[ALL_FEATURES], train["churn"]
    X_val, y_val = val[ALL_FEATURES], val["churn"]
    X_test, y_test = test[ALL_FEATURES], test["churn"]

    results = []

    # --- 1. Rule baseline: is_auto_renew == 0 -> predicted churn ---
    rule_score_test = 1 - X_test["is_auto_renew"].values  # 1 if auto_renew off
    precision5, recall5 = recall_precision_at_top_k(y_test, rule_score_test, 0.05)
    rule_precision = precision_score(y_test, rule_score_test)
    rule_recall = recall_score(y_test, rule_score_test)
    results.append({
        "model": "rule_baseline (auto_renew=0)", "pr_auc": None, "roc_auc": None,
        "precision_at_5pct": precision5, "recall_at_5pct": recall5, "brier": None,
        "precision_full_rule": rule_precision, "recall_full_rule": rule_recall,
    })
    print(f"Rule baseline: precision={rule_precision:.3f} recall={rule_recall:.3f}")

    # --- 2. Logistic regression ---
    pre = build_preprocessor()
    logreg = Pipeline([("pre", pre), ("clf", LogisticRegression(max_iter=2000, C=1.0))])
    logreg.fit(X_train, y_train)
    logreg_score = logreg.predict_proba(X_test)[:, 1]
    results.append(evaluate("logistic_regression", y_test, logreg_score))
    print(f"Logistic regression: PR-AUC={results[-1]['pr_auc']:.4f}")

    # --- 3. XGBoost, Optuna-tuned against validation ---
    pre_xgb = build_preprocessor()
    X_train_t = pre_xgb.fit_transform(X_train)
    X_val_t = pre_xgb.transform(X_val)
    X_test_t = pre_xgb.transform(X_test)
    pos_weight_base = (y_train == 0).sum() / (y_train == 1).sum()

    def objective(trial):
        params = {
            "max_depth": trial.suggest_int("max_depth", 3, 8),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "n_estimators": trial.suggest_int("n_estimators", 100, 500),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-3, 10, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10, log=True),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
            "scale_pos_weight": trial.suggest_float("scale_pos_weight", 1.0, pos_weight_base),
            "random_state": SEED, "eval_metric": "aucpr",
        }
        model = xgb.XGBClassifier(**params)
        model.fit(X_train_t, y_train)
        val_score = model.predict_proba(X_val_t)[:, 1]
        return average_precision_score(y_val, val_score)

    print("Tuning XGBoost with Optuna (30 trials, optimizing val PR-AUC)...")
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=30, show_progress_bar=False)
    best_params = study.best_params
    best_params.update({"random_state": SEED, "eval_metric": "aucpr"})
    print(f"Best val PR-AUC: {study.best_value:.4f}  params: {best_params}")

    xgb_model = xgb.XGBClassifier(**best_params)
    xgb_model.fit(X_train_t, y_train)
    xgb_score = xgb_model.predict_proba(X_test_t)[:, 1]
    results.append(evaluate("xgboost (optuna-tuned)", y_test, xgb_score))
    print(f"XGBoost: PR-AUC={results[-1]['pr_auc']:.4f}")

    with open(MODELS_DIR / "xgb_best_params.json", "w") as f:
        json.dump(best_params, f, indent=2)

    # --- lift over rule baseline ---
    lift_logreg = results[1]["recall_at_5pct"] / recall5 if recall5 else float("inf")
    lift_xgb = results[2]["recall_at_5pct"] / recall5 if recall5 else float("inf")

    # --- calibration curve for the best model (xgb) ---
    bins = np.linspace(0, 1, 11)
    bin_ids = np.digitize(xgb_score, bins) - 1
    cal_x, cal_y, cal_n = [], [], []
    for b in range(10):
        mask = bin_ids == b
        if mask.sum() > 0:
            cal_x.append(xgb_score[mask].mean())
            cal_y.append(y_test.values[mask].mean())
            cal_n.append(mask.sum())

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], "k--", alpha=0.4, label="perfect calibration")
    ax.plot(cal_x, cal_y, marker="o", label="XGBoost")
    ax.set_xlabel("Predicted probability")
    ax.set_ylabel("Observed churn rate")
    ax.set_title("Calibration curve (test set)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "08_calibration_curve.png", dpi=150)

    # --- segment breakdown for xgb on test ---
    test_eval = test.copy()
    test_eval["score"] = xgb_score
    seg_rows = []
    for plan_days, grp in test_eval.groupby("payment_plan_days"):
        if len(grp) >= 20:
            pr_auc = average_precision_score(grp.churn, grp.score) if grp.churn.nunique() > 1 else float("nan")
            seg_rows.append(("plan_days", plan_days, len(grp), grp.churn.mean(), pr_auc))
    for band_name, cond in [
        ("0-90d", test_eval.tenure_since_registration_days <= 90),
        ("91-365d", (test_eval.tenure_since_registration_days > 90) & (test_eval.tenure_since_registration_days <= 365)),
        ("365d+", test_eval.tenure_since_registration_days > 365),
    ]:
        grp = test_eval[cond]
        if len(grp) >= 20:
            pr_auc = average_precision_score(grp.churn, grp.score) if grp.churn.nunique() > 1 else float("nan")
            seg_rows.append(("tenure_band", band_name, len(grp), grp.churn.mean(), pr_auc))

    # --- write report ---
    lines = ["# Model results — steps 3.2-3.4\n"]
    lines.append(f"Train: {len(train):,} rows (cutoff <= 2016-11-30), churn rate {train.churn.mean():.1%}")
    lines.append(f"Val: {len(val):,} rows (2016-12), churn rate {val.churn.mean():.1%}")
    lines.append(f"Test: {len(test):,} rows (2017-01/02), churn rate {test.churn.mean():.1%}\n")

    lines.append("## Results table (test set, reported once)\n")
    lines.append("| model | PR-AUC | ROC-AUC | precision@5% | recall@5% | Brier |")
    lines.append("|---|---|---|---|---|---|")
    lines.append(f"| rule baseline (full rule, not top-5%) | - | - | {rule_precision:.3f}* | {rule_recall:.3f}* | - |")
    for r in results:
        if r["model"].startswith("rule"):
            continue
        lines.append(f"| {r['model']} | {r['pr_auc']:.4f} | {r['roc_auc']:.4f} | "
                      f"{r['precision_at_5pct']:.3f} | {r['recall_at_5pct']:.3f} | {r['brier']:.4f} |")
    lines.append("\n*Rule baseline is a hard binary flag (`is_auto_renew=0`), not a ranked score — its row "
                  "reports precision/recall of the rule itself, not a top-5% cut. `rule@5%` below is the "
                  "comparable ranking-based number used for lift.\n")

    lines.append(f"- Rule baseline @ top-5% by score: precision={precision5:.3f}, recall={recall5:.3f}")
    lines.append(f"- **Lift over rule baseline (recall@5%): logistic regression {lift_logreg:.2f}x, XGBoost {lift_xgb:.2f}x**\n")

    lines.append("## Calibration\n")
    lines.append("![calibration curve](08_calibration_curve.png)\n")
    lines.append(f"XGBoost Brier score: {results[2]['brier']:.4f}. See plot for the full curve — points near "
                  "the diagonal indicate predicted probabilities can be trusted as probabilities, which "
                  "matters directly for the revenue exposure calculation (step 6.1) that multiplies "
                  "probability by expected renewal value.\n")

    lines.append("## Segment breakdown (XGBoost, test set)\n")
    lines.append("| segment_type | segment | n | churn_rate | PR-AUC |")
    lines.append("|---|---|---|---|---|")
    for seg_type, seg, n, rate, pr_auc in seg_rows:
        pr_str = f"{pr_auc:.3f}" if pr_auc == pr_auc else "n/a (single class)"
        lines.append(f"| {seg_type} | {seg} | {n:,} | {rate:.1%} | {pr_str} |")

    lines.append(f"\n## Best XGBoost hyperparameters (Optuna, {30} trials, optimized against validation PR-AUC)\n")
    lines.append("```json")
    lines.append(json.dumps(best_params, indent=2))
    lines.append("```\n")

    (OUT_DIR / "07_model_results.md").write_text("\n".join(lines))
    print(f"\nWritten to {OUT_DIR / '07_model_results.md'}")
    print(f"Lift over rule baseline: logreg {lift_logreg:.2f}x, xgb {lift_xgb:.2f}x")


if __name__ == "__main__":
    main()
