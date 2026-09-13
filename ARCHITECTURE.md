# Architecture

Step 8.4. One diagram, source to consumption — most of a reviewer's time
goes here and in the demo recording, not in cloning the repo.

```mermaid
flowchart LR
    subgraph ingest["Ingest"]
        SRC["Synthetic data generator\ningest/generate_synthetic_data.py\n(KKBox-download substitute)"]
    end

    subgraph storage["Storage"]
        S3["S3: processed Parquet\n+ model artifacts\ninfra/s3.tf — built, not deployed"]
        PG[("Postgres\nraw tables")]
    end

    subgraph transform["Transform"]
        DBT["dbt\n11 models: staging -> marts\ncutoff + grain tests"]
        MARTS[("fct_prediction\nmart_risk_exposure\nmart_monthly_metrics\n+ agent views")]
    end

    subgraph ml["Model"]
        TRAIN["models/train.py\nrule -> logreg -> XGBoost\n(+ train_sequence.py: GRU experiment, lost)"]
        SCORE["models/score_predictions.py\ncalibrate + score\n-> churn_model.joblib"]
        PRED[("model_predictions\ncalibrated probability\n+ feature contributions")]
    end

    subgraph consume["Consumption"]
        API["FastAPI\n/predict /metrics /health\napi/main.py"]
        BATCH["Daily batch\napi/batch_score.py\nEventBridge-scheduled (infra/)"]
        DASH["Tableau\nSubscription Overview\nChurn Risk\n(spec: dashboards/tableau_spec.md)"]
        AGENT["Investigation agent\n4 read-only tools, bounded + logged\nagent/"]
    end

    SRC -->|"local: direct to Postgres\nAWS design: via S3"| S3
    SRC --> PG
    PG --> DBT
    DBT --> MARTS
    MARTS --> TRAIN
    TRAIN -->|"tuned params"| SCORE
    MARTS --> SCORE
    SCORE --> PRED
    SCORE -.->|"artifact"| S3
    PRED --> MARTS
    MARTS --> API
    MARTS --> BATCH
    BATCH --> PRED
    MARTS --> DASH
    MARTS -->|"4 approved views only"| AGENT
```

## Reading this diagram against what's actually running vs. built-not-deployed

| Path | Status |
|---|---|
| Generator -> Postgres -> dbt -> marts | Running locally (`docker compose up -d`, `dbt build`) |
| marts -> `models/train.py` / `score_predictions.py` -> `model_predictions` | Running locally |
| marts -> FastAPI (`/predict`, `/metrics`, `/health`) | Running locally, verified through `docker compose up -d` including the containerized API |
| Daily batch (`api/batch_score.py`) | Running locally (`python3 -m api.batch_score`); **not yet triggered by EventBridge** — that Terraform exists (`infra/eventbridge.tf`) but is unapplied |
| S3 (artifacts) | **Built, not deployed** (`infra/s3.tf`) — the Dockerfile currently `COPY`s the artifact from the local build context instead |
| ECR / ECS Fargate | **Built, not deployed** (`infra/ecr.tf`, `infra/ecs.tf`) |
| Tableau dashboards | **Spec + SQL only** (`dashboards/tableau_spec.md`) — Tableau is a GUI app outside what this repo drives; the dashboards themselves are the user's to build from the spec |
| Investigation agent | Tools built and tested against the real database (`tests/test_agent_tools.py`); the LLM orchestration loop (`agent/orchestrator.py`) exists and is wired to the Anthropic API — live-run status depends on whether an API key with credits was available this session (see `CLAUDE.md`'s Week 8 entry) |

The reason for drawing the full intended architecture rather than only
what's live: `infra/README.md`'s deploy order turns every dotted/unapplied
piece above into a running one without changing this diagram — the
diagram is the target, not a snapshot that goes stale the moment AWS
deployment happens.
