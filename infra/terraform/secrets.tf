# Application secrets. Values are generated here once; rotation is a documented runbook step.
# The two database role passwords (bankverse_owner, bankverse_app) are created by the bootstrap task
# (backend/scripts/db/init.sql run with the RDS master secret) from these secrets.
resource "random_password" "app" {
  for_each = toset(["db_owner_password", "db_app_password", "jwt_secret", "password_pepper", "field_encryption_key", "metrics_token"])
  length   = 48
  special  = false
}

resource "aws_secretsmanager_secret" "app" {
  #checkov:skip=CKV2_AWS_57:Rotation of application keys is a documented runbook (requires coordinated redeploy)
  for_each                = random_password.app
  name                    = "${local.name}/${each.key}"
  kms_key_id              = aws_kms_key.data.arn
  recovery_window_in_days = var.environment == "staging" ? 0 : 30
}

resource "aws_secretsmanager_secret_version" "app" {
  for_each      = random_password.app
  secret_id     = aws_secretsmanager_secret.app[each.key].id
  secret_string = each.value.result
}

# Connection URLs consumed by the containers (BANKVERSE_DATABASE_URL / BANKVERSE_APP_DATABASE_URL).
# They embed generated passwords: the Terraform state is therefore encrypted (S3 SSE-KMS) and access-restricted.
locals {
  db_urls = {
    database_url     = "postgresql+psycopg://bankverse_owner:${random_password.app["db_owner_password"].result}@${aws_db_instance.main.address}:5432/bankverse?sslmode=require"
    app_database_url = "postgresql+psycopg://bankverse_app:${random_password.app["db_app_password"].result}@${aws_db_instance.main.address}:5432/bankverse?sslmode=require"
  }
}

resource "aws_secretsmanager_secret" "db_url" {
  #checkov:skip=CKV2_AWS_57:Role passwords rotated by runbook together with the URL secret
  for_each                = toset(keys(local.db_urls))
  name                    = "${local.name}/${each.key}"
  kms_key_id              = aws_kms_key.data.arn
  recovery_window_in_days = var.environment == "staging" ? 0 : 30
}

resource "aws_secretsmanager_secret_version" "db_url" {
  for_each      = toset(keys(local.db_urls))
  secret_id     = aws_secretsmanager_secret.db_url[each.key].id
  secret_string = local.db_urls[each.key]
}
