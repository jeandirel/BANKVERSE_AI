variable "project" {
  type    = string
  default = "bankverse"
}

variable "environment" {
  type        = string
  description = "staging | pilot | prod"
  validation {
    condition     = contains(["staging", "pilot", "prod"], var.environment)
    error_message = "environment must be staging, pilot or prod."
  }
}

variable "region" {
  type    = string
  default = "eu-west-3" # Europe (Paris)
}

variable "cost_center" {
  type    = string
  default = "bankverse-platform"
}

variable "domain_name" {
  type        = string
  description = "Public host name served by CloudFront (e.g. app.example.com). Its ACM certificate must exist in us-east-1."
}

variable "cloudfront_certificate_arn" {
  type        = string
  description = "ACM certificate ARN in us-east-1 for domain_name."
}

variable "alb_certificate_arn" {
  type        = string
  description = "ACM certificate ARN in var.region for the origin host name (origin.<domain>)."
}

variable "origin_domain_name" {
  type        = string
  description = "DNS name pointing at the ALB, used by CloudFront as origin (e.g. origin.app.example.com)."
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "single_nat_gateway" {
  type        = bool
  default     = true
  description = "One NAT gateway (cost) vs one per AZ (resilience)."
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 50
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "db_backup_retention_days" {
  type    = number
  default = 7
}

variable "api_image" {
  type        = string
  description = "Full image reference of the API (ECR), pinned by digest in CI."
}

variable "web_image" {
  type = string
}

variable "worker_image" {
  type = string
}

variable "api_cpu" {
  type    = number
  default = 1024
}

variable "api_memory" {
  type    = number
  default = 3072
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "api_max_count" {
  type    = number
  default = 4
}

variable "web_cpu" {
  type    = number
  default = 512
}

variable "web_memory" {
  type    = number
  default = 1024
}

variable "web_desired_count" {
  type    = number
  default = 1
}

variable "monthly_budget_usd" {
  type        = number
  description = "AWS Budgets monthly limit; alerts at 50 %, 80 % and 100 % (forecasted)."
}

variable "budget_alert_emails" {
  type = list(string)
}

variable "alarm_email" {
  type    = string
  default = ""
}

variable "github_repository" {
  type        = string
  description = "owner/repo allowed to assume the deployment role through GitHub OIDC."
  default     = "jeandirel/BANKVERSE_AI"
}

variable "enable_cognito" {
  type        = bool
  default     = false
  description = "Optional Cognito user pool for SSO federation (the application keeps its own RBAC)."
}

variable "llm_provider" {
  type        = string
  default     = "none"
  description = "none | bedrock. Bedrock use is opt-in and billed per token."
}

variable "bedrock_model_id" {
  type    = string
  default = ""
}

variable "log_retention_days" {
  type    = number
  default = 90
}
