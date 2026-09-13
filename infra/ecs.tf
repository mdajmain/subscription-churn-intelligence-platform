# Step 7.2, item 5 — ECS Fargate, one task, running the api/ image built
# from the repo root Dockerfile. The same image also serves the daily
# batch job (see eventbridge.tf) via a command override on a second task
# definition — one image, two task definitions, rather than building and
# maintaining two images for what's the same dependency set.

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.project_name}-api"
  retention_in_days = 14 # student-budget cost control, not a data-retention decision
}

resource "aws_cloudwatch_log_group" "batch" {
  name              = "/ecs/${var.project_name}-batch"
  retention_in_days = 14
}

resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"
}

resource "aws_security_group" "ecs" {
  name        = "${var.project_name}-ecs-sg"
  description = "Inbound: API port from anywhere (this is the public-facing service). Outbound: all, for ECR/S3/RDS."
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "API"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- IAM ---

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${var.project_name}-ecs-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  name               = "${var.project_name}-ecs-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

data "aws_iam_policy_document" "ecs_task_s3" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
  }
}

resource "aws_iam_role_policy" "ecs_task_s3" {
  name   = "${var.project_name}-ecs-task-s3-access"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task_s3.json
}

# --- API service (the "one task" the guide asks for) ---

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project_name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name         = "api"
    image        = "${aws_ecr_repository.api.repository_url}:${var.container_image_tag}"
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "CHURN_DB_DSN", value = "host=${aws_db_instance.churn.address} port=5432 dbname=churn user=churn password=${var.db_password}" }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
}

resource "aws_ecs_service" "api" {
  name            = "${var.project_name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = [aws_subnet.public_a.id, aws_subnet.public_b.id]
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }
}

# --- Daily batch task (same image, command override; triggered by EventBridge — see eventbridge.tf) ---

resource "aws_ecs_task_definition" "batch" {
  family                   = "${var.project_name}-batch"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name  = "batch"
    image = "${aws_ecr_repository.api.repository_url}:${var.container_image_tag}"
    # Overrides the image's default CMD (uvicorn). No --date: defaults to
    # today (UTC) — see api/batch_score.py's comment. EventBridge's
    # trigger (eventbridge.tf) runs this task definition unmodified; a
    # manual backfill for a specific date passes containerOverrides.command
    # at `aws ecs run-task` time instead of changing this definition.
    command = ["python3", "-m", "api.batch_score"]
    environment = [
      { name = "CHURN_DB_DSN", value = "host=${aws_db_instance.churn.address} port=5432 dbname=churn user=churn password=${var.db_password}" }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "batch"
      }
    }
  }])
}
