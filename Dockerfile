FROM python:3.12-slim

WORKDIR /app

COPY requirements-serve.txt .
RUN pip install --no-cache-dir -r requirements-serve.txt

COPY api/ api/
# The trained+calibrated model artifact (models/artifacts/churn_model.joblib) is produced by
# running `python3 models/score_predictions.py` against a real database, not by this build --
# it must exist in the build context before `docker build` runs. In the AWS design (see
# infra/), a real deploy pulls this from the S3 bucket instead of relying on the build
# context, since the training job and the image build run in different pipelines there.
COPY models/artifacts/ models/artifacts/

ENV CHURN_DB_DSN="host=postgres port=5432 dbname=churn user=churn password=churn"

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
