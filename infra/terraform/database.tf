resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.isolated[*].id
}

resource "aws_db_parameter_group" "pg16" {
  name   = "${local.name}-pg16"
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
  parameter {
    name  = "log_connections"
    value = "1"
  }
}

# Master credentials are generated and stored by RDS in Secrets Manager (never in state outputs or code).
resource "aws_db_instance" "main" {
  #checkov:skip=CKV_AWS_157:Multi-AZ is a per-environment variable (enabled for pilot/prod)
  #checkov:skip=CKV_AWS_293:Deletion protection enabled outside staging (variable)
  identifier                            = local.name
  engine                                = "postgres"
  engine_version                        = "16"
  instance_class                        = var.db_instance_class
  allocated_storage                     = var.db_allocated_storage_gb
  max_allocated_storage                 = var.db_allocated_storage_gb * 4
  storage_type                          = "gp3"
  storage_encrypted                     = true
  kms_key_id                            = aws_kms_key.data.arn
  db_name                               = "bankverse"
  username                              = "bankverse_master"
  manage_master_user_password           = true
  master_user_secret_kms_key_id         = aws_kms_key.data.arn
  db_subnet_group_name                  = aws_db_subnet_group.main.name
  vpc_security_group_ids                = [aws_security_group.db.id]
  parameter_group_name                  = aws_db_parameter_group.pg16.name
  multi_az                              = var.db_multi_az
  publicly_accessible                   = false
  backup_retention_period               = var.db_backup_retention_days
  backup_window                         = "02:00-03:00"
  maintenance_window                    = "sun:03:30-sun:04:30"
  deletion_protection                   = var.environment != "staging"
  skip_final_snapshot                   = var.environment == "staging"
  final_snapshot_identifier             = var.environment == "staging" ? null : "${local.name}-final"
  copy_tags_to_snapshot                 = true
  auto_minor_version_upgrade            = true
  iam_database_authentication_enabled   = true
  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.data.arn
  performance_insights_retention_period = 7
  enabled_cloudwatch_logs_exports       = ["postgresql"]
  monitoring_interval                   = 60
  monitoring_role_arn                   = aws_iam_role.rds_monitoring.arn
}

resource "aws_iam_role" "rds_monitoring" {
  name = "${local.name}-rds-monitoring"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "monitoring.rds.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}
