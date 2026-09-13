output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "rds_endpoint" {
  value = aws_db_instance.churn.address
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "s3_artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}
