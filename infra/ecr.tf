# Step 7.2, item 4 — ECR repository. `docker build` (see repo root
# Dockerfile) and push happen in CI (.github/workflows/ci.yml's `deploy`
# job, currently a gated no-op — see that file's comment) or manually via:
#   aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <repo_url>
#   docker tag churn-platform-api:latest <repo_url>:latest
#   docker push <repo_url>:latest

resource "aws_ecr_repository" "api" {
  name                 = "${var.project_name}-api"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the 10 most recent images — student-budget storage cost control."
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
