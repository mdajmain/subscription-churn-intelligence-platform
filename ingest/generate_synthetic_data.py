"""
Generates a synthetic subscription dataset that mirrors the KKBox churn
challenge schema (members / transactions / user_logs) without downloading
anything. See README.md "Data source" for why this substitution exists.

Deliberately injects the eight data-quality issues BUILD-GUIDE.md step 1.3
asks you to investigate:
  1. Duplicate transactions (exact + near-duplicate)
  2. `bd` (age) outliers: negatives and implausibly large values
  3. `is_cancel` cancel-and-resubscribe noise (a cancel is not a churn)
  4. Same-day multiple transactions
  5. Zero-value transactions (trials/promotions)
  6. Missing activity logs for some users with transactions
  7. Mild churn-rate drift across the window (seasonal bump)
  8. Point-in-time leakage risk left for the feature-engineering step to guard against

Writes members.csv, transactions.csv, user_logs.csv into ../data, plus a
gitignored ground_truth_membership.csv that plays the role of KKBox's
train_v2.csv: the true simulated expiry/churn state, used ONLY to validate
rebuilt labels in step 2.3, never as a model input.
"""

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

SIM_START = pd.Timestamp("2015-01-01")
REG_END = pd.Timestamp("2016-06-01")
SIM_END = pd.Timestamp("2017-03-31")

PLAN_LENGTHS = [7, 30, 90, 180, 410]
PLAN_WEIGHTS = [0.05, 0.55, 0.15, 0.10, 0.15]
PLAN_PRICE = {7: 25, 30: 99, 90: 280, 180: 520, 410: 1788}

ENGAGEMENT_LEVELS = ["high", "medium", "low", "dormant"]
ENGAGEMENT_WEIGHTS = [0.20, 0.40, 0.30, 0.10]
BASE_HAZARD = {"high": 0.03, "medium": 0.08, "low": 0.18, "dormant": 0.35}
BASE_PLAYS = {"high": 25, "medium": 12, "low": 4, "dormant": 1}

MAX_SPELL_EVENTS = 30


def make_msno(i: int) -> str:
    return hashlib.md5(f"user-{i}".encode()).hexdigest()


def gen_members(n, rng):
    ids = [make_msno(i) for i in range(n)]
    bd = rng.integers(18, 66, size=n).astype(int)
    outlier_mask = rng.random(n) < 0.10
    negative_mask = outlier_mask & (rng.random(n) < 0.5)
    huge_mask = outlier_mask & ~negative_mask
    bd[negative_mask] = -rng.integers(1, 6, size=negative_mask.sum())
    bd[huge_mask] = rng.integers(120, 500, size=huge_mask.sum())

    gender = rng.choice(["male", "female", ""], size=n, p=[0.42, 0.43, 0.15])
    city = rng.integers(1, 22, size=n)
    registered_via = rng.choice([3, 4, 7, 9, 13, 16], size=n, p=[0.25, 0.2, 0.15, 0.2, 0.15, 0.05])
    reg_offset_days = rng.integers(0, (REG_END - SIM_START).days, size=n)
    registration_init_time = SIM_START + pd.to_timedelta(reg_offset_days, unit="D")

    return pd.DataFrame({
        "msno": ids,
        "city": city,
        "bd": bd,
        "gender": gender,
        "registered_via": registered_via,
        "registration_init_time": registration_init_time,
    })


def simulate_user(msno, reg_date, engagement, rng):
    """Replays one user's subscription life. Returns (transactions, logs, ground_truth)."""
    transactions = []
    logs = []
    ground_truth = []

    def add_tx(row):
        # A transaction can never be recorded after the data-collection cutoff,
        # even though membership_expire_date (its consequence) can run past it —
        # e.g. an annual plan's cancel event dated near cur_expire, which for a
        # 410-day plan can land over a year after SIM_END. Without this clip,
        # transaction_date silently leaks past the window the rest of the
        # pipeline assumes data stops at, corrupting month-over-month analysis.
        if row["transaction_date"] <= SIM_END:
            transactions.append(row)

    plan_length = rng.choice(PLAN_LENGTHS, p=PLAN_WEIGHTS)
    auto_renew = int(rng.random() < 0.55)
    payment_method = int(rng.integers(3, 42))

    cur_start = reg_date
    cur_expire = reg_date + pd.Timedelta(days=int(plan_length))
    renewal_count = 0
    churned = False

    for _ in range(MAX_SPELL_EVENTS):
        if cur_start > SIM_END:
            break

        price = PLAN_PRICE[plan_length]
        is_trial = rng.random() < 0.05
        actual_paid = 0 if is_trial else max(0, round(price * (1 - rng.uniform(0, 0.15))))
        tx_date = cur_start + pd.Timedelta(days=int(rng.integers(-1, 2)))

        add_tx({
            "msno": msno, "payment_method_id": payment_method,
            "payment_plan_days": plan_length, "plan_list_price": price,
            "actual_amount_paid": actual_paid, "is_auto_renew": auto_renew,
            "transaction_date": tx_date, "membership_expire_date": cur_expire,
            "is_cancel": 0,
        })

        # same-day duplicate/retry transaction noise
        if rng.random() < 0.03:
            add_tx({
                "msno": msno, "payment_method_id": int(rng.integers(3, 42)),
                "payment_plan_days": plan_length, "plan_list_price": price,
                "actual_amount_paid": actual_paid, "is_auto_renew": auto_renew,
                "transaction_date": tx_date, "membership_expire_date": cur_expire,
                "is_cancel": 0,
            })

        # generate activity logs for this spell, with decay ramp if it will end in churn
        hazard = BASE_HAZARD[engagement] * (1.5 if renewal_count == 0 else 1.0)
        hazard *= 0.4 if auto_renew else 1.0
        hazard *= 1.15 if cur_expire.month in (12, 1) else 1.0
        hazard = min(hazard, 0.9)
        will_churn_this_spell = rng.random() < hazard

        spell_end_for_logs = min(cur_expire, SIM_END)
        n_days = (spell_end_for_logs - cur_start).days
        if n_days > 0:
            day_offsets = np.arange(n_days)
            base_prob = {"high": 0.75, "medium": 0.5, "low": 0.25, "dormant": 0.08}[engagement]
            prob = np.full(n_days, base_prob)
            if will_churn_this_spell:
                decay_window = min(45, n_days)
                ramp = np.linspace(1.0, 0.25, decay_window)
                prob[-decay_window:] *= ramp
            active_mask = rng.random(n_days) < prob
            active_offsets = day_offsets[active_mask]
            n_active = len(active_offsets)
            if n_active > 0:
                total_plays = rng.poisson(BASE_PLAYS[engagement], size=n_active) + 1
                weights = rng.dirichlet([2, 1.5, 1, 1, 3], size=n_active)
                counts = np.round(total_plays[:, None] * weights).astype(int)
                total_secs = (total_plays * rng.uniform(150, 260, size=n_active)).round(1)
                uniq_frac = rng.uniform(0.4, 0.95, size=n_active)
                num_unq = np.maximum(1, np.round(total_plays * uniq_frac)).astype(int)
                dates = cur_start + pd.to_timedelta(active_offsets, unit="D")
                for j in range(n_active):
                    logs.append({
                        "msno": msno, "date": dates[j],
                        "num_25": int(counts[j, 0]), "num_50": int(counts[j, 1]),
                        "num_75": int(counts[j, 2]), "num_985": int(counts[j, 3]),
                        "num_100": int(counts[j, 4]), "num_unq": int(num_unq[j]),
                        "total_secs": float(total_secs[j]),
                    })

        if will_churn_this_spell:
            # sometimes an explicit cancel row precedes the silent lapse
            if rng.random() < 0.4:
                add_tx({
                    "msno": msno, "payment_method_id": payment_method,
                    "payment_plan_days": plan_length, "plan_list_price": price,
                    "actual_amount_paid": 0, "is_auto_renew": 0,
                    "transaction_date": cur_expire - pd.Timedelta(days=int(rng.integers(0, 4))),
                    "membership_expire_date": cur_expire, "is_cancel": 1,
                })
            ground_truth.append({
                "msno": msno, "spell_start": cur_start, "spell_expire": cur_expire,
                "true_churn": 1,
            })

            # reactivation after a gap: a genuinely new spell, not a renewal
            if rng.random() < 0.15 and cur_expire + pd.Timedelta(days=300) < SIM_END:
                gap = int(rng.integers(30, 300))
                cur_start = cur_expire + pd.Timedelta(days=gap)
                cur_expire = cur_start + pd.Timedelta(days=int(plan_length))
                renewal_count = 0
                continue
            churned = True
            break
        else:
            # cancel-and-immediately-resubscribe noise: is_cancel=1 that is NOT churn
            if rng.random() < 0.08:
                cancel_date = cur_expire - pd.Timedelta(days=int(rng.integers(0, 3)))
                add_tx({
                    "msno": msno, "payment_method_id": payment_method,
                    "payment_plan_days": plan_length, "plan_list_price": price,
                    "actual_amount_paid": 0, "is_auto_renew": auto_renew,
                    "transaction_date": cancel_date, "membership_expire_date": cur_expire,
                    "is_cancel": 1,
                })

            ground_truth.append({
                "msno": msno, "spell_start": cur_start, "spell_expire": cur_expire,
                "true_churn": 0,
            })

            # occasional plan change on renewal
            if rng.random() < 0.10:
                plan_length = rng.choice(PLAN_LENGTHS, p=PLAN_WEIGHTS)
            if rng.random() < 0.05:
                auto_renew = 1 - auto_renew
            if rng.random() < 0.05:
                payment_method = int(rng.integers(3, 42))

            renewal_count += 1
            cur_start = cur_expire
            cur_expire = cur_start + pd.Timedelta(days=int(plan_length))

    return transactions, logs, ground_truth


def inject_duplicates(tx_df, rng):
    exact = tx_df.sample(frac=0.015, random_state=int(rng.integers(0, 1_000_000)))
    near = tx_df.sample(frac=0.01, random_state=int(rng.integers(0, 1_000_000))).copy()
    near["actual_amount_paid"] = near["actual_amount_paid"] + rng.choice([-1, 1], size=len(near))
    return pd.concat([tx_df, exact, near], ignore_index=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-users", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--missing-logs-frac", type=float, default=0.10)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    out_dir = Path(__file__).resolve().parent.parent / "data"
    out_dir.mkdir(exist_ok=True)

    print(f"Simulating {args.n_users} users (seed={args.seed})...")
    members = gen_members(args.n_users, rng)
    engagements = rng.choice(ENGAGEMENT_LEVELS, size=args.n_users, p=ENGAGEMENT_WEIGHTS)
    missing_logs_users = set(
        members["msno"].sample(frac=args.missing_logs_frac, random_state=args.seed)
    )

    all_tx, all_logs, all_gt = [], [], []
    for i, (msno, reg_date) in enumerate(zip(members["msno"], members["registration_init_time"])):
        tx, logs, gt = simulate_user(msno, reg_date, engagements[i], rng)
        all_tx.extend(tx)
        all_gt.extend(gt)
        if msno not in missing_logs_users:
            all_logs.extend(logs)
        if (i + 1) % 1000 == 0:
            print(f"  {i + 1}/{args.n_users} users simulated")

    tx_df = pd.DataFrame(all_tx)
    tx_df = inject_duplicates(tx_df, rng)
    logs_df = pd.DataFrame(all_logs)
    gt_df = pd.DataFrame(all_gt)

    members.to_csv(out_dir / "members.csv", index=False)
    tx_df.to_csv(out_dir / "transactions.csv", index=False)
    logs_df.to_csv(out_dir / "user_logs.csv", index=False)
    gt_df.to_csv(out_dir / "ground_truth_membership.csv", index=False)

    print("\nDone. Row counts:")
    print(f"  members.csv                 {len(members):,}")
    print(f"  transactions.csv            {len(tx_df):,}")
    print(f"  user_logs.csv               {len(logs_df):,}")
    print(f"  ground_truth_membership.csv {len(gt_df):,}  (validation only, never a model input)")
    print(f"  users with zero log rows    {len(missing_logs_users):,}")
    print(f"\nWritten to {out_dir}")


if __name__ == "__main__":
    main()
