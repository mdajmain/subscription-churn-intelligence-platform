"""
Step 7.4 — scaling benchmark. Reruns the ingest -> load -> dbt build
pipeline at 3%, 10%, and 25% of the full 6,000-user dataset (180, 600,
1,500 users), timing each phase, against the same local Postgres this
whole project uses (no AWS deploy — see infra/README.md for why).

Honesty about what this is and isn't: these are LOCAL wall-clock
measurements, used as a proxy for how the pipeline scales, not measured
cloud costs (nothing here has run on Fargate). The cost column is a
labeled ESTIMATE, computed from AWS's published Fargate on-demand pricing
(us-east-1, 0.25 vCPU / 0.5 GB — the size infra/ecs.tf's batch task
definition requests) applied to the measured local duration. Generated or
resampled rows here are for load testing only and are never presented as
additional evidence of model accuracy (BUILD-GUIDE.md's own warning for
this step).

Regenerates the full 6,000-user dataset and rebuilds everything at the
end, restoring exactly the state this script found the database in —
sample runs are throwaway, not a replacement for the real dataset.
"""

import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "notebooks" / "12_scaling_benchmark.md"

# AWS Fargate on-demand pricing, us-east-1, as of this build (see
# infra/README.md — not fetched live, since nothing here calls AWS).
FARGATE_VCPU_HOUR_USD = 0.04048
FARGATE_GB_HOUR_USD = 0.004445
TASK_VCPU = 0.25  # matches infra/ecs.tf's batch task definition (cpu=256)
TASK_MEMORY_GB = 0.5  # matches infra/ecs.tf's batch task definition (memory=512)

SAMPLE_FRACTIONS = [0.03, 0.10, 0.25]
FULL_N_USERS = 6000


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd or ROOT, check=True, capture_output=True, text=True)


def estimated_fargate_cost(duration_seconds: float) -> float:
    hours = duration_seconds / 3600
    return hours * (TASK_VCPU * FARGATE_VCPU_HOUR_USD + TASK_MEMORY_GB * FARGATE_GB_HOUR_USD)


def one_pass(n_users: int, label: str):
    t0 = time.perf_counter()
    run(["python3", "ingest/generate_synthetic_data.py", "--n-users", str(n_users), "--seed", "42"])
    t_gen = time.perf_counter()

    run(["python3", "-c", (
        "import psycopg2,os;"
        "conn=psycopg2.connect(os.environ.get('CHURN_DB_DSN','host=localhost port=5432 dbname=churn user=churn password=churn'));"
        "conn.autocommit=True;cur=conn.cursor();"
        "cur.execute(open('sql/schema.sql').read())"
    )])
    run(["python3", "ingest/load_to_postgres.py"])
    t_load = time.perf_counter()

    run(["dbt", "build", "--profiles-dir", "."], cwd=ROOT / "dbt")
    t_dbt = time.perf_counter()

    return {
        "label": label, "n_users": n_users,
        "generate_s": t_gen - t0, "load_s": t_load - t_gen, "dbt_build_s": t_dbt - t_load,
        "total_s": t_dbt - t0,
    }


def main():
    results = []
    for frac in SAMPLE_FRACTIONS:
        n_users = round(FULL_N_USERS * frac)
        print(f"Benchmarking {frac:.0%} sample ({n_users} users)...")
        results.append(one_pass(n_users, f"{frac:.0%}"))
        print(f"  total {results[-1]['total_s']:.1f}s")

    print("Restoring the full 6,000-user dataset (regenerate + reload + rebuild + rescore)...")
    one_pass(FULL_N_USERS, "100% (restore)")
    run(["python3", "models/score_predictions.py"])
    print("Restored.")

    md = ["# Step 7.4 — scaling benchmark\n",
          "Local wall-clock measurements of the ingest -> load -> dbt build pipeline "
          "at three sample fractions of the full 6,000-user dataset. **Cost is an "
          "estimate** (AWS Fargate on-demand pricing, us-east-1, applied to the "
          "measured local duration at the batch task's configured size — "
          f"{TASK_VCPU} vCPU / {TASK_MEMORY_GB} GB, matching `infra/ecs.tf`) — nothing "
          "here ran on Fargate; see `infra/README.md` for why this project builds "
          "AWS infrastructure without deploying it. Generated/resampled rows are for "
          "load testing only, not additional evidence of model accuracy.\n",
          "| sample | n_users | generate (s) | load (s) | dbt build (s) | total (s) | est. Fargate cost |",
          "|---|---|---|---|---|---|---|"]
    for r in results:
        cost = estimated_fargate_cost(r["total_s"])
        md.append(f"| {r['label']} | {r['n_users']:,} | {r['generate_s']:.2f} | {r['load_s']:.2f} | "
                   f"{r['dbt_build_s']:.2f} | {r['total_s']:.2f} | ${cost:.6f} |")

    total_first, total_last = results[0]["total_s"], results[-1]["total_s"]
    users_first, users_last = results[0]["n_users"], results[-1]["n_users"]
    scale_factor = users_last / users_first
    time_factor = total_last / total_first if total_first > 0 else float("nan")
    md.append(f"\n**{scale_factor:.1f}x more users ({users_first} -> {users_last}) took "
              f"{time_factor:.1f}x longer end-to-end** ({total_first:.2f}s -> {total_last:.2f}s), "
              f"i.e. {'sub-linear' if time_factor < scale_factor else 'roughly linear' if abs(time_factor - scale_factor) < 0.3*scale_factor else 'super-linear'} "
              "scaling over this range on this hardware. `dbt build` is dominated by its correlated "
              "subqueries in `fct_prediction` (see the Week 4 query-optimization writeup in "
              "`README.md`) — indexed, so this stays roughly linear rather than the ~260x blowup "
              "an unindexed version showed at full scale.\n")
    OUT_PATH.write_text("\n".join(md))
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
