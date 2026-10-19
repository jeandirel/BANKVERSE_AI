terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state (S3 + DynamoDB lock). Created once by the bootstrap described in docs/AWS_DEPLOYMENT.md;
  # values are passed with `terraform init -backend-config=environments/<env>.backend.hcl`.
  backend "s3" {}
}
