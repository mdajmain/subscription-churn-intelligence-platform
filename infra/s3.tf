# Step 7.2, item 2 — S3 bucket for processed Parquet and model artifacts
# (the trained+calibrated joblib artifact models/score_predictions.py
# produces). A real deploy has the training job upload here and the
# Docker image pull from here at container start, instead of the local
# build-context COPY the Dockerfile uses today (see Dockerfile's comment).

resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.project_name}-artifacts-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire-old-model-versions"
    status = "Enabled"
    filter {
      prefix = "models/"
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

data "aws_caller_identity" "current" {}
