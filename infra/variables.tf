variable "aws_region" {
  description = "AWS region for every resource in this stack."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefix for all resource names/tags."
  type        = string
  default     = "churn-platform"
}

variable "billing_alarm_threshold_usd" {
  description = "Step 7.2: billing alarm threshold, provisioned FIRST, before anything else — this is a student-budget project and the alarm removes the 'forgot a resource was running' failure mode at zero cost."
  type        = number
  default     = 20
}

variable "alarm_notification_email" {
  description = "Email address the billing alarm and other alarms notify. Must be confirmed via the SNS subscription email after apply."
  type        = string
}

variable "db_password" {
  description = "RDS master password. Pass via TF_VAR_db_password or a .tfvars file that is gitignored — never commit it."
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "Guide-specified instance size — the smallest that exists, deliberately."
  type        = string
  default     = "db.t3.micro"
}

variable "container_image_tag" {
  description = "Tag of the image in ECR to run on ECS. Set by CI after a successful build; defaults to 'latest' for a first manual apply."
  type        = string
  default     = "latest"
}

variable "batch_schedule_cron" {
  description = "EventBridge schedule for the daily batch scoring task (api/batch_score.py). Default: 02:00 UTC daily."
  type        = string
  default     = "cron(0 2 * * ? *)"
}
