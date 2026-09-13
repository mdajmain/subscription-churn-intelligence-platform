# AWS infrastructure (Terraform) — built, not deployed

**Status: written and syntax-validated (`terraform validate`) against the
provider schema, never `plan`ned or `apply`ed against a real AWS account.**
That's a deliberate decision, not an oversight — the user's live AWS
credentials are available in this environment (account
`471879937478`), and provisioning real infrastructure (even inside free
tier) is the kind of action that gets explicit confirmation first rather
than happening automatically. This directory is what happens when that
go-ahead is given; nothing here has touched the account yet.

## What's here

| File | Step | Provisions |
|---|---|---|
| `billing_alarm.tf` | 7.2.1 | SNS topic (email) + CloudWatch billing alarm at `$20`. Deliberately no dependency on anything else — apply this alone, first. |
| `s3.tf` | 7.2.2 | Versioned, private S3 bucket for processed Parquet + the model artifact (`models/artifacts/churn_model.joblib`). |
| `networking.tf` | 7.2.3 | VPC, public subnets (2 AZs, no NAT gateway), RDS Postgres `db.t3.micro`, locked-down security group (Postgres only, only from the ECS security group). |
| `ecr.tf` | 7.2.4 | ECR repo for the API image (`Dockerfile` at repo root), with a 10-image lifecycle policy. |
| `ecs.tf` | 7.2.5 | ECS cluster, one Fargate service running the API (`aws_ecs_service.api`, `desired_count = 1`), plus a second task definition for the daily batch (same image, command override — see `api/batch_score.py`). |
| `eventbridge.tf` | 7.2.6 | EventBridge schedule triggering the batch task definition daily (default 02:00 UTC). |

## Deploy order, when the go-ahead is given

Follow the guide's order — the billing alarm genuinely goes first, not
just first in this list:

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # fill in alarm_notification_email
export TF_VAR_db_password="<choose a real secret, never commit it>"
terraform init

# 1. Billing alarm alone, first.
terraform apply -target=aws_sns_topic.billing_alerts \
                 -target=aws_sns_topic_subscription.billing_alerts_email \
                 -target=aws_cloudwatch_metric_alarm.billing
# -> confirm the SNS subscription email that AWS sends, or the alarm can't notify.
# -> also enable "Receive Billing Alerts" in Billing Preferences in the
#    AWS console once, by hand -- Terraform/CloudFormation cannot turn
#    this account setting on, and the alarm silently never fires without it.

# 2. Everything else.
terraform apply

# 3. Build and push the image (or let CI's `deploy` job do this --
#    see .github/workflows/ci.yml, currently a gated no-op).
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ecr_repository_url from output>
docker build -t churn-platform-api:latest ..
docker tag churn-platform-api:latest <ecr_repository_url>:latest
docker push <ecr_repository_url>:latest

# 4. Load data + run dbt against the new RDS instance, same as local:
CHURN_DB_DSN="host=<rds_endpoint output> port=5432 dbname=churn user=churn password=$TF_VAR_db_password" \
  python3 ../ingest/load_to_postgres.py
cd ../dbt && CHURN_DB_DSN="..." dbt build --target dev   # or add an `aws` target pointed at RDS

# 5. Force a new ECS deployment to pick up the freshly-pushed image:
aws ecs update-service --cluster <ecs_cluster_name output> --service churn-platform-api --force-new-deployment
```

**Done when** (guide's own bar): `/predict` responds from the running ECS
service, and the EventBridge-scheduled batch task has run at least once
(check `/ecs/churn-platform-batch` in CloudWatch Logs).

## Cost notes, stated rather than hidden

- **No NAT gateway** (~$32/month) — the single largest deliberate cost
  decision here. RDS sits in a public subnet with a locked-down security
  group instead of a private subnet behind a NAT; ECS tasks get a public
  IP directly. Neither pattern gives the workload an egress path a NAT
  gateway wouldn't have provided anyway, for this design.
- `db.t3.micro`, `allocated_storage = 20` (GB, gp3), `skip_final_snapshot
  = true`, `backup_retention_period = 0` — a disposable student-budget
  database, not a production one. Stated as a tradeoff, not implied to be
  production-grade.
- ECS tasks are the smallest Fargate size (`cpu=256`, `memory=512`).
- CloudWatch log retention capped at 14 days on both log groups.
- ECR keeps only the 10 most recent images.

## Teardown

`terraform destroy` removes everything here. RDS has
`skip_final_snapshot = true` and `deletion_protection = false`
specifically so a `destroy` doesn't get stuck or leave an orphaned
snapshot billing quietly in the background — another instance of stating
the cost tradeoff instead of hiding it.
