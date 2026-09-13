"""
Step 5.2 — compare three configurations on the identical time-based test set:

  A — engineered features -> XGBoost (the week 3 model, refit here with its
      already-tuned params so it scores the exact same 39,454-row label set
      as B/C, rather than reusing models/train.py's own read of fct_features).
  B — raw 90-day activity sequence (models/tensors/*.npz) -> GRU encoder -> head.
  C — GRU encoder output concatenated with the same tabular features as A,
      trained jointly (encoder is not pretrained separately).

This is the only place in the project the data is genuinely sequential
(BUILD-GUIDE.md week 5) -- everywhere else, windowed aggregates already
summarize it. The comparison itself is the deliverable: on a tabular churn
problem, gradient-boosted trees on hand-engineered aggregates usually win,
and a clearly-stated negative result for B/C is a legitimate outcome, not
a failed experiment.
"""

import json
import os
from pathlib import Path

# xgboost and torch each bundle their own OpenMP runtime on macOS; loading
# both in one process segfaults (duplicate libomp) unless these are set
# before either is imported. OMP_NUM_THREADS=1 is what actually prevents
# the segfault here; KMP_DUPLICATE_LIB_OK is the commonly-cited companion.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd
import psycopg2
import torch
import torch.nn as nn
import xgboost as xgb
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
TENSOR_DIR = Path(__file__).resolve().parent / "tensors"
MODELS_DIR = Path(__file__).resolve().parent
OUT_DIR = Path(__file__).resolve().parent.parent / "notebooks"
SEED = 42
torch.manual_seed(SEED)
np.random.seed(SEED)

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


def load_tensor_split(name):
    d = np.load(TENSOR_DIR / f"{name}.npz", allow_pickle=True)
    keys = pd.DataFrame({"msno": d["msno"], "cutoff_date": pd.to_datetime(d["cutoff_date"])})
    return d["X"], d["y"], keys


def load_tabular_aligned(keys):
    """Load fct_prediction rows for exactly the (msno, cutoff_date) pairs in
    `keys`, in that same order, so tensor rows and tabular rows line up.

    fct_prediction, not the Week 3 fct_features table it replaced -- dbt
    has no fct_features model, so that name resolves only on databases
    predating the Week 4 migration. The assert below is what actually
    guards this: if the two ever disagreed on grain, the join would drop or
    duplicate rows against the tensor split and fail loudly."""
    conn = psycopg2.connect(DB_DSN)
    df = pd.read_sql(f"""
        SELECT msno, cutoff_date, {", ".join(ALL_FEATURES)}
        FROM fct_prediction WHERE churn IS NOT NULL
    """, conn, parse_dates=["cutoff_date"])
    conn.close()
    df["gender"] = df["gender"].fillna("missing")
    for b in BOOLEAN:
        df[b] = df[b].astype(int)
    aligned = keys.merge(df, on=["msno", "cutoff_date"], how="left")
    assert len(aligned) == len(keys), "tabular join dropped or duplicated rows vs. the tensor split"
    return aligned[ALL_FEATURES]


def normalize_sequence(X_train, *others):
    """Per-channel standardization fit on train only, applied to all splits.
    log1p first since play counts / total_secs are heavily right-skewed."""
    X_train_log = np.log1p(X_train)
    mean = X_train_log.mean(axis=(0, 1), keepdims=True)
    std = X_train_log.std(axis=(0, 1), keepdims=True) + 1e-6
    out = [((np.log1p(X_train) - mean) / std).astype(np.float32)]
    for X in others:
        out.append(((np.log1p(X) - mean) / std).astype(np.float32))
    return out


def evaluate(name, y_true, y_score):
    pr_auc = average_precision_score(y_true, y_score)
    roc_auc = roc_auc_score(y_true, y_score)
    order = np.argsort(-y_score)
    n_top = max(1, int(len(y_true) * 0.05))
    top = order[:n_top]
    precision5 = np.asarray(y_true)[top].mean()
    recall5 = np.asarray(y_true)[top].sum() / np.asarray(y_true).sum()
    brier = brier_score_loss(y_true, y_score)
    return {"config": name, "pr_auc": pr_auc, "roc_auc": roc_auc,
            "precision_at_5pct": precision5, "recall_at_5pct": recall5, "brier": brier}


class GRUEncoder(nn.Module):
    def __init__(self, n_channels=7, hidden=32):
        super().__init__()
        self.gru = nn.GRU(input_size=n_channels, hidden_size=hidden, batch_first=True)

    def forward(self, x):
        _, h = self.gru(x)
        return h[-1]  # (batch, hidden)


class SequenceOnlyModel(nn.Module):
    def __init__(self, hidden=32):
        super().__init__()
        self.encoder = GRUEncoder(hidden=hidden)
        self.head = nn.Sequential(nn.Linear(hidden, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, x_seq, x_tab=None):
        return self.head(self.encoder(x_seq)).squeeze(-1)


class JointModel(nn.Module):
    def __init__(self, n_tab_features, hidden=32):
        super().__init__()
        self.encoder = GRUEncoder(hidden=hidden)
        self.head = nn.Sequential(
            nn.Linear(hidden + n_tab_features, 32), nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, x_seq, x_tab):
        enc = self.encoder(x_seq)
        return self.head(torch.cat([enc, x_tab], dim=1)).squeeze(-1)


def train_torch_model(model, X_seq_train, X_tab_train, y_train, X_seq_val, X_tab_val, y_val,
                       epochs=30, batch_size=256, lr=1e-3, patience=5):
    device = torch.device("cpu")
    model.to(device)
    pos_weight = torch.tensor([(y_train == 0).sum() / max((y_train == 1).sum(), 1)], dtype=torch.float32)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    opt = torch.optim.Adam(model.parameters(), lr=lr)

    X_seq_train_t = torch.tensor(X_seq_train)
    X_tab_train_t = torch.tensor(X_tab_train, dtype=torch.float32) if X_tab_train is not None else None
    y_train_t = torch.tensor(y_train, dtype=torch.float32)
    X_seq_val_t = torch.tensor(X_seq_val)
    X_tab_val_t = torch.tensor(X_tab_val, dtype=torch.float32) if X_tab_val is not None else None

    n = len(y_train)
    best_val_prauc, best_state, no_improve = -1, None, 0

    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(n)
        for i in range(0, n, batch_size):
            idx = perm[i:i + batch_size]
            xb_seq = X_seq_train_t[idx]
            xb_tab = X_tab_train_t[idx] if X_tab_train_t is not None else None
            yb = y_train_t[idx]
            opt.zero_grad()
            out = model(xb_seq, xb_tab)
            loss = loss_fn(out, yb)
            loss.backward()
            opt.step()

        model.eval()
        with torch.no_grad():
            val_logits = model(X_seq_val_t, X_tab_val_t).numpy()
        val_score = 1 / (1 + np.exp(-val_logits))
        val_prauc = average_precision_score(y_val, val_score)
        if val_prauc > best_val_prauc:
            best_val_prauc, best_state, no_improve = val_prauc, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            no_improve += 1
        if no_improve >= patience:
            break

    model.load_state_dict(best_state)
    return model, best_val_prauc


def predict_torch(model, X_seq, X_tab):
    model.eval()
    with torch.no_grad():
        logits = model(torch.tensor(X_seq), torch.tensor(X_tab, dtype=torch.float32) if X_tab is not None else None).numpy()
    return 1 / (1 + np.exp(-logits))


def main():
    print("Loading tensors...")
    X_seq_train, y_train, keys_train = load_tensor_split("train")
    X_seq_val, y_val, keys_val = load_tensor_split("val")
    X_seq_test, y_test, keys_test = load_tensor_split("test")

    print("Loading tabular features aligned to the same rows...")
    tab_train_raw = load_tabular_aligned(keys_train)
    tab_val_raw = load_tabular_aligned(keys_val)
    tab_test_raw = load_tabular_aligned(keys_test)

    pre = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUMERIC),
        ("bool", "passthrough", BOOLEAN),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
    ])
    X_tab_train = pre.fit_transform(tab_train_raw)
    X_tab_val = pre.transform(tab_val_raw)
    X_tab_test = pre.transform(tab_test_raw)
    if hasattr(X_tab_train, "toarray"):
        X_tab_train, X_tab_val, X_tab_test = X_tab_train.toarray(), X_tab_val.toarray(), X_tab_test.toarray()
    n_tab_features = X_tab_train.shape[1]
    print(f"tabular feature width after encoding: {n_tab_features}")

    X_seq_train_n, X_seq_val_n, X_seq_test_n = normalize_sequence(X_seq_train, X_seq_val, X_seq_test)

    results = []

    # --- Config A: engineered features -> XGBoost (reuse week-3 tuned params) ---
    print("\n[A] XGBoost on engineered features...")
    with open(MODELS_DIR / "xgb_best_params.json") as f:
        best_params = json.load(f)
    xgb_model = xgb.XGBClassifier(**best_params)
    xgb_model.fit(X_tab_train, y_train)
    score_a = xgb_model.predict_proba(X_tab_test)[:, 1]
    results.append(evaluate("A: tabular -> XGBoost", y_test, score_a))
    print(f"  PR-AUC={results[-1]['pr_auc']:.4f}")

    # --- Config B: raw sequence -> GRU ---
    print("\n[B] GRU on raw 90-day sequence...")
    model_b = SequenceOnlyModel(hidden=32)
    model_b, val_prauc_b = train_torch_model(
        model_b, X_seq_train_n, None, y_train, X_seq_val_n, None, y_val,
    )
    score_b = predict_torch(model_b, X_seq_test_n, None)
    results.append(evaluate("B: sequence -> GRU", y_test, score_b))
    print(f"  best val PR-AUC={val_prauc_b:.4f}  test PR-AUC={results[-1]['pr_auc']:.4f}")

    # --- Config C: GRU + tabular, trained jointly ---
    print("\n[C] GRU + tabular, trained jointly...")
    model_c = JointModel(n_tab_features=n_tab_features, hidden=32)
    model_c, val_prauc_c = train_torch_model(
        model_c, X_seq_train_n, X_tab_train, y_train, X_seq_val_n, X_tab_val, y_val,
    )
    score_c = predict_torch(model_c, X_seq_test_n, X_tab_test)
    results.append(evaluate("C: sequence + tabular (joint)", y_test, score_c))
    print(f"  best val PR-AUC={val_prauc_c:.4f}  test PR-AUC={results[-1]['pr_auc']:.4f}")

    results_df = pd.DataFrame(results)
    print("\n", results_df.to_string(index=False))

    winner = results_df.loc[results_df.pr_auc.idxmax(), "config"]
    a_prauc, b_prauc, c_prauc = results[0]["pr_auc"], results[1]["pr_auc"], results[2]["pr_auc"]

    md = ["# Step 5.2 — sequence model comparison\n",
          "Same time-based test set as `models/train.py` (Jan-Feb 2017, "
          f"n={len(y_test):,}, base rate {y_test.mean():.1%}). All three "
          "configurations see the exact same 39,454 labeled snapshots split "
          "the same way; B and C never see engineered features beyond what "
          "the GRU learns from the raw 90x7 tensor.\n",
          "| Config | PR-AUC | ROC-AUC | Precision@5% | Recall@5% | Brier |",
          "|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r['config']} | {r['pr_auc']:.4f} | {r['roc_auc']:.4f} | "
                   f"{r['precision_at_5pct']:.3f} | {r['recall_at_5pct']:.3f} | {r['brier']:.4f} |")

    interpretation = f"""
## Interpretation

**Winner on PR-AUC: {winner}** (A={a_prauc:.4f}, B={b_prauc:.4f}, C={c_prauc:.4f}).
"""
    if a_prauc >= b_prauc and a_prauc >= c_prauc:
        interpretation += (
            "The engineered-feature XGBoost model was not beaten by either sequence "
            "configuration. This is the expected outcome for a tabular churn problem, "
            "not a failed experiment: the windowed aggregates in `fct_features` "
            "(7/30/90-day totals, active-day counts, activity ratios) already summarize "
            "the same 90 days the GRU sees, and gradient-boosted trees are typically "
            "better than a small recurrent encoder at exploiting a moderate number of "
            "informative, already-well-engineered numeric features on a dataset this "
            "size (~34k training rows). The result suggests the signal in this data "
            "lives in activity *level* and *recency* (which the aggregates capture "
            "directly) rather than in fine-grained daily pattern (bursty vs. steady, "
            "gradual decline vs. cliff) that only a sequence model could see. "
        )
        if c_prauc > b_prauc:
            interpretation += (
                "Config C outperforming Config B (concatenating tabular features "
                "helps the joint model) further supports this: once the model has "
                "access to the aggregates, the marginal value of the raw sequence "
                "on top of them is small. "
            )
        interpretation += (
            "Operationally: the added complexity of training and serving a GRU "
            "(a second training pipeline, a sequence-tensor build step, GPU/CPU "
            "inference on raw daily logs instead of a single feature row) is not "
            "worth it here — Config A stays the production model."
        )
    else:
        interpretation += (
            f"{winner} outperformed the tabular XGBoost baseline, suggesting the raw "
            "daily sequence carries information about churn (shape of decline, "
            "burstiness) that the windowed aggregates discard. "
        )
        if winner.startswith("C"):
            interpretation += (
                "Config C beating both A and B suggests the sequence and tabular "
                "views are complementary rather than redundant. "
            )
        interpretation += (
            f"The PR-AUC gap ({max(a_prauc, b_prauc, c_prauc) - a_prauc:+.4f} over "
            "Config A) would need to be weighed against the added serving complexity "
            "of a sequence model before recommending it operationally."
        )

    md.append(interpretation)
    (OUT_DIR / "09_sequence_model_results.md").write_text("\n".join(md))
    results_df.to_csv(OUT_DIR / "09_sequence_model_results.csv", index=False)
    print(f"\nWrote {OUT_DIR / '09_sequence_model_results.md'}")


if __name__ == "__main__":
    main()
