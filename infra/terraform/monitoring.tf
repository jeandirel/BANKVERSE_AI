resource "aws_sns_topic" "alarms" {
  name              = "${local.name}-alarms"
  kms_master_key_id = aws_kms_key.data.arn
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  alarms = {
    alb_5xx = {
      namespace  = "AWS/ApplicationELB", metric = "HTTPCode_Target_5XX_Count", stat = "Sum", threshold = 20, period = 300
      dimensions = { LoadBalancer = aws_lb.main.arn_suffix }, description = "API/web 5xx responses"
    }
    alb_latency = {
      namespace  = "AWS/ApplicationELB", metric = "TargetResponseTime", stat = "p95", threshold = 2, period = 300
      dimensions = { LoadBalancer = aws_lb.main.arn_suffix }, description = "p95 latency above 2 s"
    }
    api_cpu = {
      namespace  = "AWS/ECS", metric = "CPUUtilization", stat = "Average", threshold = 85, period = 300
      dimensions = { ClusterName = aws_ecs_cluster.main.name, ServiceName = aws_ecs_service.api.name }, description = "API CPU"
    }
    rds_cpu = {
      namespace  = "AWS/RDS", metric = "CPUUtilization", stat = "Average", threshold = 80, period = 300
      dimensions = { DBInstanceIdentifier = aws_db_instance.main.identifier }, description = "RDS CPU"
    }
    rds_storage = {
      namespace  = "AWS/RDS", metric = "FreeStorageSpace", stat = "Minimum", threshold = 5368709120, period = 300, lower = true
      dimensions = { DBInstanceIdentifier = aws_db_instance.main.identifier }, description = "RDS free storage below 5 GB"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each            = local.alarms
  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = contains(["p95"], each.value.stat) ? null : each.value.stat
  extended_statistic  = contains(["p95"], each.value.stat) ? each.value.stat : null
  period              = each.value.period
  evaluation_periods  = 2
  threshold           = each.value.threshold
  comparison_operator = try(each.value.lower, false) ? "LessThanThreshold" : "GreaterThanThreshold"
  dimensions          = each.value.dimensions
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      { type = "metric", x = 0, y = 0, width = 12, height = 6, properties = { title = "ALB requests & 5xx", region = var.region, stat = "Sum", period = 300,
      metrics = [["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.main.arn_suffix], [".", "HTTPCode_Target_5XX_Count", ".", "."]] } },
      { type = "metric", x = 12, y = 0, width = 12, height = 6, properties = { title = "Latency p95", region = var.region, stat = "p95", period = 300,
      metrics = [["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix]] } },
      { type = "metric", x = 0, y = 6, width = 12, height = 6, properties = { title = "ECS CPU / memory (api)", region = var.region, stat = "Average", period = 300,
      metrics = [["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", "api"], [".", "MemoryUtilization", ".", ".", ".", "."]] } },
      { type = "metric", x = 12, y = 6, width = 12, height = 6, properties = { title = "RDS", region = var.region, stat = "Average", period = 300,
      metrics = [["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.identifier], [".", "DatabaseConnections", ".", "."]] } },
    ]
  })
}

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [join("$", ["user:Project", var.project])]
  }

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value == 100 ? "FORECASTED" : "ACTUAL"
      subscriber_email_addresses = var.budget_alert_emails
    }
  }
}
