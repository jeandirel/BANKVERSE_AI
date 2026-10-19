resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_cloudwatch_log_group" "svc" {
  #checkov:skip=CKV_AWS_338:Retention is a variable (365 days recommended for pilot/prod)
  for_each          = toset(["api", "web", "worker"])
  name              = "/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.logs.arn
}

# --- IAM ------------------------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = concat([for s in aws_secretsmanager_secret.app : s.arn], [for s in aws_secretsmanager_secret.db_url : s.arn])
      },
      { Effect = "Allow", Action = ["kms:Decrypt"], Resource = [aws_kms_key.data.arn] }
    ]
  })
}

# Runtime role of the API: tenant documents (read/write under orgs/*), model artifacts (read), optional Bedrock.
resource "aws_iam_role" "api_task" {
  name               = "${local.name}-api-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "api_task" {
  role = aws_iam_role.api_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid      = "Documents"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${aws_s3_bucket.this["documents"].arn}/orgs/*"]
      },
      { Sid = "DocumentsList", Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.this["documents"].arn] },
      { Sid = "Artifacts", Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${aws_s3_bucket.this["artifacts"].arn}/*"] },
      { Sid = "Kms", Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = [aws_kms_key.data.arn] }
      ], var.llm_provider == "bedrock" ? [{
        Sid      = "Bedrock"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = ["arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_model_id}"]
    }] : [])
  })
}

# Worker role: pipelines and training (lake read/write, artifacts write). Runs only as on-demand tasks.
resource "aws_iam_role" "worker_task" {
  name               = "${local.name}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "worker_task" {
  role = aws_iam_role.worker_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.this["lake"].arn, "${aws_s3_bucket.this["lake"].arn}/*", aws_s3_bucket.this["artifacts"].arn, "${aws_s3_bucket.this["artifacts"].arn}/*"]
      },
      { Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = [aws_kms_key.data.arn] }
    ]
  })
}

# --- Task definitions -----------------------------------------------------------------------------
locals {
  common_env = [
    { name = "BANKVERSE_ENV", value = var.environment == "prod" ? "pilot" : var.environment },
    { name = "BANKVERSE_COOKIE_SECURE", value = "true" },
    { name = "BANKVERSE_ALLOWED_ORIGINS", value = "https://${var.domain_name}" },
    { name = "BANKVERSE_STORAGE_BACKEND", value = "s3" },
    { name = "BANKVERSE_S3_BUCKET_DOCUMENTS", value = aws_s3_bucket.this["documents"].bucket },
    { name = "BANKVERSE_S3_BUCKET_LAKE", value = aws_s3_bucket.this["lake"].bucket },
    { name = "BANKVERSE_S3_BUCKET_ARTIFACTS", value = aws_s3_bucket.this["artifacts"].bucket },
    { name = "BANKVERSE_KMS_KEY_ID", value = aws_kms_key.data.arn },
    { name = "BANKVERSE_AWS_REGION", value = var.region },
    { name = "BANKVERSE_ARTIFACTS_DIR", value = "/tmp/artifacts" },
    { name = "BANKVERSE_DATA_DIR", value = "/tmp/data" },
    { name = "BANKVERSE_LLM_PROVIDER", value = var.llm_provider },
    { name = "BANKVERSE_BEDROCK_MODEL_ID", value = var.bedrock_model_id },
    { name = "BANKVERSE_PUBLIC_DEMO_ENABLED", value = var.environment == "prod" ? "false" : "true" },
  ]
  common_secrets = [
    { name = "BANKVERSE_DATABASE_URL", valueFrom = aws_secretsmanager_secret.db_url["database_url"].arn },
    { name = "BANKVERSE_APP_DATABASE_URL", valueFrom = aws_secretsmanager_secret.db_url["app_database_url"].arn },
    { name = "BANKVERSE_JWT_SECRET", valueFrom = aws_secretsmanager_secret.app["jwt_secret"].arn },
    { name = "BANKVERSE_API_KEY_PEPPER", valueFrom = aws_secretsmanager_secret.app["password_pepper"].arn },
    { name = "BANKVERSE_DATA_ENCRYPTION_KEY", valueFrom = aws_secretsmanager_secret.app["field_encryption_key"].arn },
    { name = "BANKVERSE_METRICS_TOKEN", valueFrom = aws_secretsmanager_secret.app["metrics_token"].arn },
  ]
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name                   = "api"
    image                  = var.api_image
    essential              = true
    readonlyRootFilesystem = false
    portMappings           = [{ containerPort = 8000, protocol = "tcp" }]
    environment            = local.common_env
    secrets                = local.common_secrets
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    logConfiguration = {
      logDriver = "awslogs"
      options   = { awslogs-group = aws_cloudwatch_log_group.svc["api"].name, awslogs-region = var.region, awslogs-stream-prefix = "api" }
    }
  }])
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.execution.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name                   = "web"
    image                  = var.web_image
    essential              = true
    readonlyRootFilesystem = false
    portMappings           = [{ containerPort = 3000, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = { awslogs-group = aws_cloudwatch_log_group.svc["web"].name, awslogs-region = var.region, awslogs-stream-prefix = "web" }
    }
  }])
}

# On-demand worker (migrations, pipelines, training, batch scoring): `aws ecs run-task` with a command override.
resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 4096
  memory                   = 16384
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn
  ephemeral_storage {
    size_in_gib = 100
  }
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name        = "worker"
    image       = var.worker_image
    essential   = true
    command     = ["db-upgrade"]
    environment = local.common_env
    secrets     = local.common_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options   = { awslogs-group = aws_cloudwatch_log_group.svc["worker"].name, awslogs-region = var.region, awslogs-stream-prefix = "worker" }
    }
  }])
}

# --- Services -------------------------------------------------------------------------------------
resource "aws_service_discovery_private_dns_namespace" "main" {
  name = "${local.name}.internal"
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "api" {
  name = "api"
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
  }
}

resource "aws_ecs_service" "api" {
  name                              = "api"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.api.arn
  desired_count                     = var.api_desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 90
  enable_execute_command            = false
  propagate_tags                    = "SERVICE"
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  service_registries {
    registry_arn = aws_service_discovery_service.api.arn
  }
  lifecycle {
    ignore_changes = [desired_count]
  }
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.web_desired_count
  launch_type     = "FARGATE"
  propagate_tags  = "SERVICE"
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 3000
  }
}

resource "aws_appautoscaling_target" "api" {
  max_capacity       = var.api_max_count
  min_capacity       = var.api_desired_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${local.name}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
