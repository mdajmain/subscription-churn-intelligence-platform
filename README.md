# Subscription Churn Intelligence Platform

**▶ [Try the live churn-risk calculator](https://subscription-churn-intelligence-platform-c4hxjbhnr9jzqe4t7f4vd.streamlit.app/)** — enter a subscriber's
plan and usage, get their calibrated 30-day churn probability plus the three factors that drove
it. Runs the same trained model artifact and the same scoring path (`api/scoring.py`) as the
FastAPI service and the nightly batch job, so it cannot silently disagree with them.

## Business question

Which subscribers are likely not to renew, how much subscription revenue is exposed, and which segments should the retention team review first?

## Data source — deviation from the original build guide

This project follows `BUILD-GUIDE.md`'s PACE plan with one deliberate change to step 0.4: instead of
downloading the ~30GB KKBox Kaggle dataset, `ingest/generate_synthetic_data.py` **simulates** a
subscription business with the same table schema (`members`, `transactions`, `user_logs`) and
deliberately injects the same eight data-quality issues the guide's Analyze phase asks you to find:

1. Duplicate transactions (exact and near-duplicate)
2. `bd` (age) outliers — negatives and implausibly large values
3. `is_cancel` cancel-and-resubscribe noise (cancel is not churn)
4. Same-day multiple transactions (plan changes)
5. Zero-value transactions (trials/promotions)
6. Missing activity logs for some users with transactions
7. Churn rate drift across the simulated time window
8. Features that would leak future information if computed carelessly

Because there is no published `train_v2.csv` answer key to validate rebuilt labels against, the
generator keeps a hidden `ground_truth_membership.csv` (the true simulated expiry/churn state,
gitignored, never used as a feature or training input) that stands in for that role in step 2.3:
we rebuild labels from raw transactions and check them against the ground truth we know we
simulated, exactly as the guide checks against `train_v2.csv`.

This substitution is the honest limitation to state up front: findings describe a simulated
population's mechanics, not real KKBox subscribers. The engineering and analytical methods
(spell reconstruction, cutoff discipline, snapshotting, time-based splits) are unchanged and are
the actual point of the project.

## Exclusion list

- **LoRA / fine-tuning an LLM** — no NLP task in this project; would not answer the business question.
- **React front end** — Tableau/dashboards cover the presentation layer needed here.
- **Terraform** — single-environment student project; manual AWS steps are documented and reproducible by hand.
- **Locust / load testing framework** — the scaling question is answered by the 3%/10%/25% benchmark, not simulated concurrent users.
- **Step Functions** — one scheduled batch job doesn't need a state machine; EventBridge + ECS Fargate is sufficient.
- **Kubernetes** — one ECS Fargate task; no orchestration complexity to justify it.
- **Neural networks on tabular features** — gradient-boosted trees are the appropriate tool for this data shape; a sequence model is tried only on the one genuinely sequential artifact (Week 5).
- **Downloading the real KKBox dataset** — see "Data source" above; synthetic data with injected data-quality issues is used instead.

## Project structure

```
churn-platform/
  ingest/       # synthetic data generation, load into Postgres
  sql/          # exploration queries -> later dbt/
  notebooks/
  features/
  models/
  api/
  tests/
  agent/        # read-only LLM investigation agent
  dbt/          # the transformation pipeline of record
  infra/        # Terraform for the AWS deployment
  streamlit_demo/  # the public churn-risk calculator
  data/         # gitignored — synthetic CSVs + ground truth
```

## Environment

```
docker compose up -d
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python ingest/generate_synthetic_data.py
python ingest/load_to_postgres.py
python sql/build_spells.py
python sql/build_snapshots.py
python sql/build_features.py
python -m pytest tests/
python models/train.py
```

## Label definition

A subscriber churns at a given membership expiry (`cutoff_date`) if no later
transaction for that subscriber carries a `membership_expire_date` beyond
that cutoff, within 30 days of it. This is computed from `fct_membership_spell`
directly (a snapshot is a churn iff it is the last transaction in its spell
and the 30-day window has fully elapsed against the data's collection
cutoff) rather than re-derived independently from raw transactions — an
early version that re-derived it independently required the next
transaction to be dated strictly *after* the cutoff, which misclassified
early renewals (common in this data) as churn. See
`notebooks/05_label_validation.md` for the full writeup, including why the
spell-based definition is the correct one.

`is_cancel=1` is never used as the churn signal. A cancellation followed by
a prompt resubscription is a plan change, not a lapse (`notebooks/02_data_quality_findings.md`, issue 3).

Rebuilt labels agree with the generator's ground truth (the synthetic-data
stand-in for KKBox's `train_v2.csv`) on **99.64%** of comparable
subscriber-cycles. The remaining disagreements all trace to one mechanism —
a cancel-noise transaction sorting chronologically after the renewal it was
paired with, due to independent date jitter — which is the synthetic
equivalent of the guide's own expected real-data disagreement category
("same-day cancel-and-resubscribe").

## Point-in-time cutoff rule

**No feature may read data dated at or after its prediction cutoff.** Every
feature in `fct_features` (`sql/build_features.sql`) is computed with a
strict `transaction_date < cutoff_date` / `date < cutoff_date` filter.
`tests/test_no_leakage.py` checks this two ways: every stored feature value
is independently recomputed from raw tables and must match exactly, and —
to prove the test isn't vacuous — the same recomputation using a
deliberately leaky `<=` filter is checked to actually disagree with the
stored values for real rows.

## Split strategy

| Split | Cutoff range | Rows | Churn rate |
|---|---|---|---|
| Train | through 2016-11-30 | 33,820 | 8.1% |
| Validation | 2016-12 | 1,552 | 7.4% |
| Test | 2017-01 to 2017-02 | 2,842 | 6.1% |

All preprocessing (imputation, scaling, one-hot encoding) is fit on train
only. Snapshots whose 30-day label window hadn't fully elapsed as of the
data's collection cutoff are excluded entirely (`churn IS NULL`) rather than
guessed at — this affects the most recent ~11% of all snapshots.

## Results (test set, reported once)

| model | PR-AUC | ROC-AUC | precision@5% | recall@5% | Brier |
|---|---|---|---|---|---|
| rule baseline (`is_auto_renew=0`) | - | - | 0.111 | 0.741 | - |
| logistic regression | 0.531 | 0.894 | 0.570 | 0.466 | 0.039 |
| XGBoost (Optuna-tuned) | 0.651 | 0.932 | 0.676 | 0.552 | 0.035 |

**Lift over rule baseline (recall @ top-5% capacity): logistic regression
4.50x, XGBoost 5.33x.** Full results, segment breakdown, and hyperparameters:
`notebooks/07_model_results.md`. Calibration curve: `notebooks/08_calibration_curve.png`
(reasonably close to the diagonal — slightly under-confident in the
mid-range, which is worth an isotonic/Platt calibration pass before trusting
the revenue-exposure multiplication in step 6.1 at face value).

## Three retention findings (`notebooks/06_cohort_retention.md`)

1. **Tenure risk:** churn is highest in the first 30 days after a spell
   starts (12.8%, 95% CI [11.7%, 14.0%]) and declines steadily with tenure
   (5.7% past a year) — new subscribers are the highest-risk group, not
   long-tenured ones.
2. **Plan length:** churn rises monotonically with plan length, from
   7.4-7.7% on 7/30-day plans to 10.4% on 410-day plans, holding in both
   halves of the time window. Counter to the naive intuition that a longer
   commitment signals stronger intent to stay — read here as a likely
   selection effect (410-day renewals are rare per subscriber) rather than
   a causal claim.
3. **Pre-churn engagement decay:** subscribers who churn show materially
   lower activity in the 30 days before their expiry than those who renew
   (Cohen's d = 0.72) — confirms the activity-window features carry real
   signal, not noise.

All three are observational associations, not causal claims — plan choice,
tenure, and engagement are not randomly assigned.

## dbt and query optimization (Week 4)

Transformations migrated to dbt (`dbt/`): 11 models across staging
(`stg_members`, `stg_transactions`, `stg_user_logs`) and marts
(`dim_customer`, `fct_transaction`, `fct_activity_daily`,
`fct_membership_spell`, `fct_snapshot`, `fct_prediction`,
`mart_monthly_metrics`, `mart_cohort_retention`), with `unique`/`not_null`
on every grain key, `accepted_values` on plan length and payment method, a
`relationships` test from `fct_snapshot`/`fct_prediction` to `dim_customer`,
and a custom cutoff test (`assert_no_feature_leakage`). `dbt build` runs
green from a clean database in 4.2 seconds (`sql/*.py` scripts from
Weeks 2-3 remain in the repo for their exploratory writeups but are
superseded by `dbt/` as the pipeline of record).

**Query optimization, found live rather than staged:** the first `dbt
build` attempt on `fct_prediction` (its ~13 correlated subqueries per row
against `fct_transaction` and `fct_activity_daily`) hung for over 10
minutes and had to be killed — the dbt models didn't carry the indexes the
original hand-written `sql/build_features.py` script had. Isolated with
`EXPLAIN ANALYZE` on one representative subquery (prior renewal count,
42,813 outer rows):

| | plan | execution time |
|---|---|---|
| without index | `Seq Scan on fct_transaction` per outer row, 46,370 rows filtered each time | 38,677 ms |
| with `(msno, transaction_date)` index | `Index Scan` per outer row | 148 ms |

A ~260x speedup on this one subquery — consistent with the observed >10
minute full-table hang, since `fct_prediction` repeats this pattern roughly
13 times per row across two large tables. Fixed by adding dbt-native
`indexes` config to `fct_transaction`, `fct_activity_daily`,
`fct_membership_spell`, `fct_snapshot`, and `fct_prediction`; `fct_prediction`
now builds in 3.1 seconds. Verified the dbt-rebuilt `fct_snapshot` and
`fct_membership_spell` are logically identical to the pre-dbt versions by
re-running the label validation against them (still 99.64% agreement) and
the full pytest leakage suite (still passing) — a regression check, not
just a rebuild.

## Sequence model experiment (Week 5)

The only genuinely sequential data in this project is the 90 days of daily
activity before each prediction cutoff — everywhere else, `fct_features`
already crushes it into windowed aggregates. Built a `90 x 7` tensor per
snapshot (`models/build_sequence_tensor.py`: 90 days, six play-count
buckets + `total_secs`, zero-filled for inactive days) and compared three
configurations on the identical time-based test set (`models/train_sequence.py`):

| Config | PR-AUC | ROC-AUC | Precision@5% | Recall@5% | Brier |
|---|---|---|---|---|---|
| A: engineered features -> XGBoost | 0.6523 | 0.9320 | 0.669 | 0.546 | 0.0351 |
| B: raw sequence -> GRU | 0.5429 | 0.8901 | 0.556 | 0.454 | 0.1053 |
| C: GRU + tabular, trained jointly | 0.6242 | 0.9245 | 0.662 | 0.540 | 0.0966 |

**Config A wins.** Neither sequence configuration beat the week-3 XGBoost
model. This is a clean negative result, not a failed experiment: the
windowed aggregates already summarize the same 90 days the GRU sees, and a
small recurrent encoder trained on ~34k rows doesn't have an edge over
gradient-boosted trees on well-engineered numeric features. It suggests the
signal in this data lives in activity *level* and *recency* — which the
aggregates capture directly — rather than in fine-grained daily pattern
(bursty vs. steady, gradual decline vs. cliff) that only a sequence model
would see; Config C beating Config B (tabular features helping the joint
model) supports the same reading. Operationally, the added complexity of a
second training pipeline and sequence-tensor build step isn't worth it
here — Config A stays the production model. Full writeup:
`notebooks/09_sequence_model_results.md`.

## Revenue exposure (Week 6.1)

`exposure = calibrated_churn_probability x expected_next_renewal_value` —
ranking by exposure instead of raw probability sends the retention team
after value at risk, not just risk. Two pieces of new work here, not just
a formula:

**Calibration, checked rather than assumed.** Week 3 flagged the model as
"reasonably but not perfectly calibrated." Isotonic calibration (the more
flexible option) was tried first and *measurably hurt ranking* — PR-AUC
0.6646 -> 0.6239 on the test set — because its piecewise-constant fit
overfits a validation set this size (1,552 rows). Sigmoid (Platt) scaling
improved Brier (0.0342 -> 0.0337) while leaving PR-AUC unchanged (0.6646),
so that's what scores `mart_risk_exposure`. Full comparison:
`notebooks/10_calibration_check.md`; scoring job: `models/score_predictions.py`.

**Exclusions.** `actual_amount_paid <= 0` (zero-value trials/promotions,
plus 11 rows carrying a `-1` sentinel on cancellations found while building
this metric — see `notebooks/02_data_quality_findings.md`, finding 5) and
irregular plan lengths are excluded from exposure entirely (NULL, never
zero — a zero would misread as "no revenue at risk"). 37,385 of 42,813
`fct_prediction` rows get a defined exposure value.

**The two rankings genuinely differ.** Comparing each subscriber's current
snapshot, top-5% by probability alone vs. top-5% by exposure: only 12%
overlap. The probability-only queue skews toward cheap, high-risk
subscribers ($87.68 average 30-day value, below the $96.43 population
mean); the exposure queue surfaces higher-value subscribers ($121.99
average) even at somewhat lower individual risk. Full writeup and the SQL
reconciliation (dashboard totals must tie to a query, not just look
right): `notebooks/11_revenue_exposure.md`.

**Labeled as revenue *at risk*, never *saved*** — no intervention has been
run against this population, so there's no evidence yet about what an
intervention would recover.

## Dashboards (Week 6.2)

Tableau is a GUI tool outside what this repo can build end-to-end, so this
step produced the SQL views and a written spec
(`dashboards/tableau_spec.md`) for the two dashboards the guide asks for
(Subscription Overview, Churn Risk) — see that file for the panel-by-panel
spec and reconciliation queries for every headline number.

**Published:** https://public.tableau.com/app/profile/md.ajmain.adil/viz/SubscriptionChurnIntelligence/ChurnRisk
(Tableau Public — built from a static CSV extract per
`TABLEAU_DASHBOARD_WALKTHROUGH.md`; headline numbers reconciled against
direct SQL queries before publishing).

## Serving and deployment (Week 7)

**7.1 API and container — built and running locally.** FastAPI
(`api/main.py`): `/predict` (scores live from the saved model artifact,
either by `msno` — looks up its latest `fct_prediction` snapshot — or a
raw feature payload), `/metrics` (model version, test PR-AUC/Brier,
prediction-volume stats), `/health` (DB + model-artifact check, `503` if
either is down). `Dockerfile` builds a serving-only image (~seconds to
build; `requirements-serve.txt` deliberately excludes dbt/optuna/torch —
training-only dependencies with no business being in a serving
container). `docker compose up -d` brings up Postgres + the API together;
verified end-to-end against the containerized service (`/health`,
`/metrics`, and `/predict` against a real subscriber all confirmed
working through the container, not just via the local `.venv`).

**A daily batch job** (`api/batch_score.py`) is separate from the API —
scores one `cutoff_date`'s snapshots and UPSERTs into `model_predictions`
(vs. `models/score_predictions.py`, which retrains + recalibrates +
rescores everything, meant to be rerun occasionally to refresh the model
itself). `tests/test_idempotency.py` replays the same date twice and
asserts the row count and values are unchanged, not doubled — the guide's
explicit ask for step 7.3.

**7.2 AWS — deployed, verified end-to-end, then torn down.** `infra/`
(Terraform): billing alarm first, S3 for artifacts, RDS `db.t3.micro` in
a public subnet with a locked-down security group (no NAT gateway — the
single largest deliberate cost decision, stated not hidden), ECR, ECS
Fargate (one API task + a second task definition for the batch job),
EventBridge schedule. 34 resources applied for real; the API served a
live `/predict` against a real subscriber from RDS, and the scheduled
batch task ran to a clean exit. Then destroyed and independently
verified gone (`head-bucket` → 404, `describe-repositories` →
`RepositoryNotFoundException`, empty `terraform state list`) — the stack
is a demonstrated capability, not something left billing.

Applying it found five things `terraform validate` cannot catch, all of
which had been sitting in this repo undetected:

| Found only by deploying | Why `validate` missed it |
|---|---|
| IAM user lacked create permissions for EC2/S3/ECR/ECS/EventBridge | `validate` makes no AWS API calls |
| Image built `arm64` on Apple Silicon; Fargate expects `linux/amd64` | Not a Terraform concern |
| `dbt build --target dev` silently builds against **localhost**, not RDS | `dev` hardcodes its host; added a `prod` target |
| `mart_risk_exposure` ↔ `score_predictions.py` circular dependency | Only surfaces against a genuinely empty database |
| `*.tfplan` not gitignored — a saved plan embeds `db_password` in cleartext | Not a schema error |

**7.3 CI** (`.github/workflows/ci.yml`): `pytest` -> `dbt build` against
an isolated `test` schema (the `ci` dbt target added specifically for
this — never used by the app itself) -> Docker image build -> a `deploy`
job that stays a gated no-op (`AWS_DEPLOY_ENABLED` repo variable, unset)
until AWS deployment is actually turned on. The idempotency check runs
inline in CI too, not just in pytest, per the guide calling it out
explicitly as "the kind of thing people claim and rarely verify."

**7.4 Scaling benchmark** (`scripts/scaling_benchmark.py` ->
`notebooks/12_scaling_benchmark.md`): reruns ingest -> load -> `dbt
build` at 3%/10%/25% of the full 6,000-user dataset, measured locally
(labeled as a local proxy with an *estimated* Fargate cost — these timings
were taken on a laptop, not on the Fargate deployment described in 7.2,
and are not restated as measured cloud figures). 8.3x more users (180 -> 1,500) took
1.8x longer end-to-end — sub-linear over this range, consistent with the
Week 4 indexing fix keeping `fct_prediction`'s correlated subqueries from
scaling badly. The script regenerates and restores the full dataset
afterward rather than leaving the database at a sampled-down state.

## Live demo — churn-risk calculator (`streamlit_demo/`)

**[subscription-churn-intelligence-platform…streamlit.app](https://subscription-churn-intelligence-platform-c4hxjbhnr9jzqe4t7f4vd.streamlit.app/)**

A Streamlit front end aimed at someone who will never read this README: enter a
subscriber's plan, price, tenure and usage, get a calibrated 30-day churn probability,
a risk tier, the 30-day revenue at stake, and the three factors that moved the
prediction most. Four presets load a realistic subscriber so there's nothing to fill
in to see it work.

It scores through `api/scoring.py`'s `score_rows()` rather than re-implementing
preprocessing, so the app, the FastAPI service and the batch job cannot drift apart in
what they'd predict for the same subscriber. Derived features (`discount`,
`activity_ratio_7d_to_30d_avg`, the play-share columns) use the same definitions as
`dbt/models/marts/fct_prediction.sql`.

**One modelling subtlety the UI handles explicitly.** "We have usage data and it shows
zero" and "we have no usage data at all" are very different to this model, and
correctly so: rows with `missing_activity_flag` churn at **7.8%** in training — the base
rate — because missing logs mean missing *data*, not a disengaged subscriber. Zero
*recorded* usage scores ~83%; the same inputs with the missing-data flag set score ~9%.
The app asks which case applies instead of inferring one from the other, which would
invert the prediction. An earlier version of the app got this wrong, which is how the
behaviour got measured in the first place.

`streamlit_demo/requirements.txt` is deliberately separate from the root
`requirements.txt` (which carries dbt/optuna/torch, none of which this app imports), and
pins `scikit-learn`/`xgboost` to the versions the artifact was pickled under —
unpickling under different versions risks silently different predictions.

```bash
streamlit run streamlit_demo/streamlit_app.py   # no database required
```

## Investigation agent, model card, architecture (Week 8)

**8.1 Investigation agent** (`agent/`): four read-only tools
(`query_metrics`, `compare_segments`, `check_freshness`,
`get_prediction_summary`) over four Postgres views
(`sql/agent_views.sql`) that a dedicated `agent_readonly` role can SELECT
from and **nothing else** — enforced by Postgres itself
(`sql/agent_grants.sql`), verified by a test that confirms the role is
denied access to a raw table, not just assumed. Every tool call is
argument-validated (a fixed metric/segment-column enum, not free-text
SQL), timeout-bounded, and logged to a structured trace. An LLM (Claude,
via the Anthropic API's tool-use loop, `agent/orchestrator.py`) chooses
among the tools, capped at 6 calls per investigation, and must structure
every answer under three headers — data quality problems, observed
changes, candidate explanations — so a hypothesis is never presented with
the same confidence as a number that came from a tool.

**8.2 Evaluation** (`agent/eval_prompts.py` ->
`notebooks/13_agent_evaluation.md`): 10 fixed prompts, 8 answerable + 2
deliberately unanswerable, scored by hand against an independent SQL
recomputation of every number. **8/8 answerable prompts correct on tool
selection, arithmetic, and traceability; 2/2 unanswerable prompts
correctly declined for the right stated reasons.** The evaluation itself
found two real bugs live (not in code review): an ambiguous-SQL-column
bug in the revenue-exposure tool query (now fixed, with regression
tests), and — on an ad-hoc question before the fixed eval set was even
run — the agent silently dropping an unsupported tenure filter and
describing its answer as if the filter had been applied, fixed by an
explicit system-prompt rule against exactly that failure mode. Full
writeup, including what a stricter "never make an unnecessary tool call"
prompt would likely trade away: `notebooks/13_agent_evaluation.md`.

**8.3 Model card:** `MODEL_CARD.md` — intended use, training data/window,
features, performance (overall and by segment, including a version note
on a small PR-AUC discrepancy between the original Week 3 run and a Week
6 refit under drifted library versions), calibration, known failure
modes (weakest on 7-day plans, no sub-7-month tenure in the test period,
calendar drift, sequence information genuinely unused per Week 5), and
an explicit "what this model does not establish" section (no causal
claims, no intervention evidence, no evidence beyond synthetic data or
the observed test window).

**8.4 Architecture:** `ARCHITECTURE.md` — one Mermaid diagram (source ->
storage -> transform -> model -> consumption) plus a table stating
plainly which paths are actually running locally versus built-but-not-
deployed (AWS) versus spec-only (Tableau), so the diagram doesn't imply
more than what's true today.

**8.5 Resume bullets:** `RESUME_BULLETS.md` — six bullets with the
numbers pulled directly from this results ledger, plus a "numbers to
keep straight" section noting where two honestly-different numbers exist
for the same metric (e.g. the PR-AUC version note above) so a bullet
doesn't drift from what the repo actually shows.

## Honest limitations

- **Synthetic data.** See "Data source" above. Findings describe this
  simulated population's mechanics, not real KKBox subscribers — the
  generator's own design choices (hazard function, decay ramp) are partly
  responsible for the patterns found in Analyze, so those findings are best
  read as validation that the pipeline can *detect* known-injected signal,
  not as claims about real subscriber behavior.
- **No causal claims.** All retention findings are observational.
- **No intervention evidence.** The model has never been used to change
  anyone's outcome; "revenue at risk" (step 6.1) is not "revenue saved."
- **Fixed registration window.** All synthetic users registered between
  Jan 2015 and Jun 2016, so by the test period (Jan-Feb 2017) no subscriber
  has less than ~7 months' tenure — the tenure-band segment breakdown in
  `notebooks/07_model_results.md` is naturally missing a "new user" segment
  in the test window as a result.
- **Calibration is decent, not perfect.** See the calibration curve above.
