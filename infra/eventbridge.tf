# Step 7.2, item 6 — EventBridge schedule for the daily batch, running
# api/batch_score.py's ECS task definition (see ecs.tf). The command
# override here passes the actual run date; api/batch_score.py's own
# UPSERT logic (tests/test_idempotency.py) is what makes a duplicate or
# retried trigger safe, not anything on the EventBridge side.

data "aws_iam_policy_document" "eventbridge_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eventbridge_ecs" {
  name               = "${var.project_name}-eventbridge-ecs-role"
  assume_role_policy = data.aws_iam_policy_document.eventbridge_assume_role.json
}

data "aws_iam_policy_document" "eventbridge_run_task" {
  statement {
    actions   = ["ecs:RunTask"]
    resources = [replace(aws_ecs_task_definition.batch.arn, "/:\\d+$/", ":*")]
  }
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_execution.arn, aws_iam_role.ecs_task.arn]
  }
}

resource "aws_iam_role_policy" "eventbridge_run_task" {
  name   = "${var.project_name}-eventbridge-run-task"
  role   = aws_iam_role.eventbridge_ecs.id
  policy = data.aws_iam_policy_document.eventbridge_run_task.json
}

resource "aws_cloudwatch_event_rule" "daily_batch" {
  name                = "${var.project_name}-daily-batch"
  description         = "Triggers the daily churn-scoring batch task."
  schedule_expression = var.batch_schedule_cron
}

resource "aws_cloudwatch_event_target" "daily_batch" {
  rule     = aws_cloudwatch_event_rule.daily_batch.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge_ecs.arn

  # No command override here, deliberately: EventBridge's input
  # transformer can substitute fields from the triggering event, but has
  # no built-in function to format "today" as YYYY-MM-DD, so there's
  # nothing useful to inject. api/batch_score.py defaults --date to
  # date.today() (UTC) when omitted specifically so this scheduled
  # trigger can invoke the task definition's own command as-is.
  ecs_target {
    task_definition_arn = aws_ecs_task_definition.batch.arn
    task_count          = 1
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = [aws_subnet.public_a.id, aws_subnet.public_b.id]
      security_groups  = [aws_security_group.ecs.id]
      assign_public_ip = true
    }
  }
}
