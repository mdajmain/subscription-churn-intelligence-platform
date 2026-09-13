"""
Step 5.1 — build the 90x7 daily-activity tensor for each snapshot.

For every (msno, cutoff_date) in fct_prediction with a known (non-censored)
label, build a 90 x 7 array: 90 days strictly before cutoff_date (same
lower bound already used by fct_prediction's *_90d features), each day
holding six play-count buckets (num_25, num_50, num_75, num_985, num_100,
num_unq) plus total_secs. Day index 0 = cutoff_date - 90, index 89 =
cutoff_date - 1, so the tensor is always in chronological order regardless
of which days a user was actually active. Missing days are zero-filled —
zero plays is the correct value for a day with no activity, not a missing
value.

Aligned with the same time-based split as models/train.py (train through
2016-11-30, val 2016-12, test 2017-01/02) so configuration A/B/C in
models/train_sequence.py are compared on identical rows.
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

DB_DSN = os.environ.get("CHURN_DB_DSN", "host=localhost port=5432 dbname=churn user=churn password=churn")
OUT_DIR = Path(__file__).resolve().parent / "tensors"
WINDOW = 90
N_CHANNELS = 7  # num_25, num_50, num_75, num_985, num_100, num_unq, total_secs
CHANNELS = ["num_25", "num_50", "num_75", "num_985", "num_100", "num_unq", "total_secs"]


def load_snapshots(conn):
    return pd.read_sql("""
        SELECT msno, cutoff_date, churn
        FROM fct_prediction
        WHERE churn IS NOT NULL
        ORDER BY cutoff_date, msno
    """, conn, parse_dates=["cutoff_date"])


def load_activity_join(conn, snapshots):
    """One query joining every snapshot to its own 90-day activity window,
    instead of one query per snapshot (42k+ round trips)."""
    return pd.read_sql(f"""
        SELECT s.msno, s.cutoff_date,
               (l.date - (s.cutoff_date - {WINDOW}))::int AS day_idx,
               l.num_25, l.num_50, l.num_75, l.num_985, l.num_100, l.num_unq, l.total_secs
        FROM fct_prediction s
        JOIN fct_activity_daily l
          ON l.msno = s.msno
         AND l.date >= s.cutoff_date - {WINDOW}
         AND l.date < s.cutoff_date
        WHERE s.churn IS NOT NULL
    """, conn, parse_dates=["cutoff_date"])


def split(df):
    train = df[df.cutoff_date <= pd.Timestamp("2016-11-30")]
    val = df[(df.cutoff_date >= pd.Timestamp("2016-12-01")) & (df.cutoff_date <= pd.Timestamp("2016-12-31"))]
    test = df[(df.cutoff_date >= pd.Timestamp("2017-01-01")) & (df.cutoff_date <= pd.Timestamp("2017-02-28"))]
    return {"train": train, "val": val, "test": test}


def build_tensor(snap_split, activity):
    """snap_split: dataframe of (msno, cutoff_date, churn) for one split.
    activity: the full joined activity dataframe (filtered per split by merge).
    Returns X (n, WINDOW, N_CHANNELS) float32, y (n,) int, keys (n, 2)."""
    snap_split = snap_split.reset_index(drop=True)
    n = len(snap_split)
    X = np.zeros((n, WINDOW, N_CHANNELS), dtype=np.float32)

    key_to_row = {(m, c): i for i, (m, c) in enumerate(zip(snap_split.msno, snap_split.cutoff_date))}
    act = activity.merge(snap_split[["msno", "cutoff_date"]], on=["msno", "cutoff_date"], how="inner")

    row_idx = np.array([key_to_row[(m, c)] for m, c in zip(act.msno, act.cutoff_date)])
    day_idx = act["day_idx"].values.astype(int)
    valid = (day_idx >= 0) & (day_idx < WINDOW)
    row_idx, day_idx, act = row_idx[valid], day_idx[valid], act[valid]

    for ch_i, ch in enumerate(CHANNELS):
        X[row_idx, day_idx, ch_i] = act[ch].values.astype(np.float32)

    y = snap_split["churn"].values.astype(np.int64)
    keys = snap_split[["msno", "cutoff_date"]].copy()
    return X, y, keys


def main():
    OUT_DIR.mkdir(exist_ok=True)
    conn = psycopg2.connect(DB_DSN)
    print("Loading snapshots...")
    snapshots = load_snapshots(conn)
    print(f"{len(snapshots):,} labeled snapshots")

    print("Loading joined 90-day activity window (one query)...")
    activity = load_activity_join(conn, snapshots)
    conn.close()
    print(f"{len(activity):,} snapshot-day activity rows")

    splits = split(snapshots)
    for name, s in splits.items():
        X, y, keys = build_tensor(s, activity)
        churned_pct_covered = (X.sum(axis=(1, 2)) > 0).mean()
        np.savez_compressed(
            OUT_DIR / f"{name}.npz",
            X=X, y=y,
            msno=keys["msno"].values, cutoff_date=keys["cutoff_date"].astype(str).values,
        )
        print(f"{name}: X={X.shape} y={y.shape} churn_rate={y.mean():.1%} "
              f"nonzero_activity={churned_pct_covered:.1%} -> {OUT_DIR / (name + '.npz')}")


if __name__ == "__main__":
    main()
