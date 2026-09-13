# Resume bullets

Step 8.5. Pulled from the results ledger built across the project, while
the numbers are still in front of me.

- Built an end-to-end subscription churn prediction platform (Postgres,
  dbt, XGBoost, FastAPI) on 6,000 simulated subscribers with 990K+
  activity records, enforcing a strict point-in-time feature cutoff
  verified by an automated leakage test that provably catches a leaky
  query, not just one that happens to pass.
- Trained and honestly evaluated three churn models on a time-based
  split — a tuned XGBoost model reached 0.65 PR-AUC (5.3x lift over a
  rule baseline at 5% review capacity) against a 6.1% base rate; ran a
  sequence-model experiment (GRU on raw 90-day activity) that lost to
  engineered features, reported as a negative result rather than omitted.
- Migrated the transformation pipeline to 11 dbt models with grain and
  cutoff tests, then found and fixed a missing-index bug that cut a
  13-subquery table build from 10+ minutes to 3.1 seconds (~260x) via
  `EXPLAIN ANALYZE`-driven diagnosis.
- Designed a revenue-exposure ranking (calibrated probability x expected
  renewal value) that surfaces a materially different, higher-value
  review queue than probability alone (12% overlap in the top 5%);
  compared isotonic vs. sigmoid calibration and caught isotonic silently
  degrading ranking quality on a small validation set before shipping it.
- Containerized a FastAPI scoring service (`/predict`, `/metrics`,
  `/health`) with an idempotent daily batch job (proven by an automated
  replay test) and CI (pytest -> dbt build -> Docker build); deployed the
  full stack to AWS with Terraform (VPC, RDS, ECS Fargate, ECR,
  EventBridge, a CloudWatch billing alarm) and verified it live end-to-end
  — a real subscriber scored through the running service, a scheduled
  batch task triggered and confirmed — then tore it down; architected for
  a sub-$20/month student budget by deliberately omitting a NAT gateway.
- Built a bounded, read-only LLM investigation agent (4 tools, database-
  enforced least-privilege access, per-query timeouts, full logged
  traces) that separates data-quality issues from observed changes from
  hypothesized explanations in every report, rather than collapsing them
  into one confident narrative.

**Numbers to keep straight** (so a rounding-off in conversation doesn't
drift from what's actually in the repo):
- PR-AUC 0.651 (original Week 3 run, `notebooks/07_model_results.md`) —
  Week 6's refit under newer library versions got 0.6646 on the same test
  rows; see `MODEL_CARD.md`'s version note for why both are cited rather
  than one being picked silently.
- 5.33x lift (XGBoost, recall@top-5% vs. rule baseline).
- 260x query speedup (38,677ms -> 148ms on the representative subquery
  isolated with `EXPLAIN ANALYZE`; ~260x is also consistent with the
  observed full-table 10+ minute -> 3.1s change).
- 12% overlap between probability-ranked and exposure-ranked top-5% queues.
- 99.64% label-validation agreement against the generator's ground truth.
