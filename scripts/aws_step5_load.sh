#!/usr/bin/env bash
# aws.md step 5 -- load data + build the warehouse against RDS.
# Run from anywhere: cd's to the repo root itself.
set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate

export CHURN_DB_HOST="churn-platform-db.culccemay4uh.us-east-1.rds.amazonaws.com"
export CHURN_DB_USER="churn"
export CHURN_DB_NAME="churn"
export CHURN_DB_PASSWORD
CHURN_DB_PASSWORD="$(grep '^db_password' infra/terraform.tfvars | sed 's/.*= *"\(.*\)"/\1/')"
export CHURN_DB_DSN="host=$CHURN_DB_HOST port=5432 dbname=$CHURN_DB_NAME user=$CHURN_DB_USER password=$CHURN_DB_PASSWORD"

echo "== 1/5: schema =="
python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['CHURN_DB_DSN'])
conn.autocommit = True
conn.cursor().execute(open('sql/schema.sql').read())
print('schema.sql applied')
"

echo "== 2/5: raw data load =="
python3 ingest/load_to_postgres.py

echo "== 3/6: dbt build, pass 1 (target: prod) =="
# On a genuinely fresh database, mart_risk_exposure's 3 not_null tests on
# the `ml.model_predictions` source are EXPECTED to fail here -- that table
# doesn't exist yet, it's written by score_predictions.py below, which
# itself reads fct_prediction (built by this same pass). So this is a real
# circular dependency, resolved by running dbt build twice. Don't let
# set -e kill the script on this specific, anticipated failure; do stop on
# anything else.
set +e
(cd dbt && dbt build --target prod)
DBT1_EXIT=$?
set -e
if [ "$DBT1_EXIT" -ne 0 ]; then
  echo "  (pass 1 exited non-zero -- expected IF the only failures above are"
  echo "   the 3 model_predictions source tests. If fct_prediction or any"
  echo "   other model failed to build, stop and investigate before continuing.)"
fi

echo "== 4/6: score predictions =="
python3 models/score_predictions.py

echo "== 5/6: dbt build, pass 2 (target: prod) -- builds mart_risk_exposure now that model_predictions exists =="
(cd dbt && dbt build --target prod)

echo "== 6/6: agent views + grants =="
python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['CHURN_DB_DSN'])
conn.autocommit = True
conn.cursor().execute(open('sql/agent_views.sql').read())
conn.cursor().execute(open('sql/agent_grants.sql').read())
print('agent_views.sql + agent_grants.sql applied')
"

echo "== ALL DONE == checking row counts =="
python3 -c "
import psycopg2, os
conn = psycopg2.connect(os.environ['CHURN_DB_DSN'])
cur = conn.cursor()
for t in ['members', 'transactions', 'user_logs', 'fct_prediction']:
    cur.execute(f'select count(*) from {t}')
    print(f'{t}: {cur.fetchone()[0]}')
"
