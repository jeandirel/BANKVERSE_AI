locals {
  buckets = {
    documents = "Tenant documents, KB files and generated reports (prefix orgs/<org_id>/)"
    lake      = "Data lake: raw / bronze / silver / gold Parquet"
    artifacts = "Model artifacts (SHA-256 verified at load) and MLflow artifacts"
    logs      = "ALB and CloudFront access logs"
  }
}

resource "aws_s3_bucket" "this" {
  #checkov:skip=CKV_AWS_21:Versioning enabled for every bucket in aws_s3_bucket_versioning.this (for_each)
  #checkov:skip=CKV_AWS_145:SSE-KMS set in aws_s3_bucket_server_side_encryption_configuration.this; the logs bucket uses SSE-S3 (ALB log delivery requirement)
  #checkov:skip=CKV2_AWS_62:No event-driven consumers
  #checkov:skip=CKV_AWS_144:Data residency: no replication outside eu-west-3; versioning + backups instead
  #checkov:skip=CKV2_AWS_6:Public access block defined in aws_s3_bucket_public_access_block.this (for_each)
  #checkov:skip=CKV2_AWS_61:Lifecycle defined in aws_s3_bucket_lifecycle_configuration.this (for_each)
  #checkov:skip=CKV_AWS_18:Access logging configured in aws_s3_bucket_logging.this (logs bucket excluded to avoid recursion)
  for_each      = local.buckets
  bucket        = "${local.name}-${each.key}-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
  tags          = { Purpose = each.value }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    object_ownership = each.key == "logs" ? "BucketOwnerPreferred" : "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = { for k, v in aws_s3_bucket.this : k => v if k != "logs" }
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

# Access-log destinations do not support SSE-KMS for ALB delivery: SSE-S3 is used for the logs bucket.
resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.this["logs"].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id

  rule {
    id     = "noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = each.key == "documents" ? 365 : 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  dynamic "rule" {
    for_each = each.key == "logs" ? [1] : []
    content {
      id     = "expire-logs"
      status = "Enabled"
      filter {}
      expiration {
        days = 400
      }
    }
  }
}

# TLS-only access for every bucket.
resource "aws_s3_bucket_policy" "tls_only" {
  for_each = { for k, v in aws_s3_bucket.this : k => v if k != "logs" }
  bucket   = each.value.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [each.value.arn, "${each.value.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

data "aws_elb_service_account" "main" {}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.this["logs"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AlbAccessLogs"
        Effect    = "Allow"
        Principal = { AWS = data.aws_elb_service_account.main.arn }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.this["logs"].arn}/alb/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.this["logs"].arn, "${aws_s3_bucket.this["logs"].arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }
    ]
  })
}

resource "aws_s3_bucket_logging" "this" {
  for_each      = { for k, v in aws_s3_bucket.this : k => v if k != "logs" }
  bucket        = each.value.id
  target_bucket = aws_s3_bucket.this["logs"].id
  target_prefix = "s3/${each.key}/"
}
