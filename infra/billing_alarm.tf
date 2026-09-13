# Step 7.2, item 1 — billing alarm, provisioned before any resource that
# could actually cost money. This is what removes the standard "left a
# resource running" student-AWS horror story as a failure mode, at zero
# cost of its own. Deliberately has no dependency on anything else in this
# stack, so `terraform apply -target=aws_cloudwatch_metric_alarm.billing`
# can provision it alone, first, exactly as the guide's ordering asks.
#
# Billing metrics only publish to us-east-1 regardless of aws_region, so
# this alarm's provider is pinned there.

provider "aws" {
  alias  = "billing"
  region = "us-east-1"
}

resource "aws_sns_topic" "billing_alerts" {
  provider = aws.billing
  name     = "${var.project_name}-billing-alerts"
}

resource "aws_sns_topic_subscription" "billing_alerts_email" {
  provider  = aws.billing
  topic_arn = aws_sns_topic.billing_alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_notification_email
  # AWS emails a confirmation link to alarm_notification_email after apply —
  # the subscription stays PendingConfirmation, and no alarm notification
  # is delivered, until that link is clicked.
}

resource "aws_cloudwatch_metric_alarm" "billing" {
  provider            = aws.billing
  alarm_name          = "${var.project_name}-billing-over-${var.billing_alarm_threshold_usd}usd"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EstimatedCharges"
  namespace           = "AWS/Billing"
  period              = 21600 # 6 hours — the finest granularity billing metrics support
  statistic           = "Maximum"
  threshold           = var.billing_alarm_threshold_usd
  dimensions = {
    Currency = "USD"
  }
  alarm_description  = "Total AWS estimated charges exceeded $${var.billing_alarm_threshold_usd}. Provisioned first, before any billable resource, per BUILD-GUIDE.md step 7.2."
  alarm_actions      = [aws_sns_topic.billing_alerts.arn]
  treat_missing_data = "missing"
}
