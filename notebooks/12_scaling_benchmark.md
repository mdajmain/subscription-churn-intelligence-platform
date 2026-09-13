# Step 7.4 — scaling benchmark

Local wall-clock measurements of the ingest -> load -> dbt build pipeline at three sample fractions of the full 6,000-user dataset. **Cost is an estimate** (AWS Fargate on-demand pricing, us-east-1, applied to the measured local duration at the batch task's configured size — 0.25 vCPU / 0.5 GB, matching `infra/ecs.tf`) — nothing here ran on Fargate; see `infra/README.md` for why this project builds AWS infrastructure without deploying it. Generated/resampled rows are for load testing only, not additional evidence of model accuracy.

| sample | n_users | generate (s) | load (s) | dbt build (s) | total (s) | est. Fargate cost |
|---|---|---|---|---|---|---|
| 3% | 180 | 0.50 | 0.27 | 2.64 | 3.41 | $0.000012 |
| 10% | 600 | 0.77 | 0.30 | 2.45 | 3.52 | $0.000012 |
| 25% | 1,500 | 1.97 | 0.98 | 3.23 | 6.18 | $0.000021 |

**8.3x more users (180 -> 1500) took 1.8x longer end-to-end** (3.41s -> 6.18s), i.e. sub-linear scaling over this range on this hardware. `dbt build` is dominated by its correlated subqueries in `fct_prediction` (see the Week 4 query-optimization writeup in `README.md`) — indexed, so this stays roughly linear rather than the ~260x blowup an unindexed version showed at full scale.
