# Optional: Cognito user pool as an OIDC identity provider for bank SSO federation (SAML/OIDC upstream).
# Authorisation stays in the application (roles, tenant membership, MFA policy).
resource "aws_cognito_user_pool" "main" {
  count                    = var.enable_cognito ? 1 : 0
  name                     = local.name
  mfa_configuration        = "ON"
  deletion_protection      = var.environment == "staging" ? "INACTIVE" : "ACTIVE"
  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]
  software_token_mfa_configuration {
    enabled = true
  }
  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 3
  }
  admin_create_user_config {
    allow_admin_create_user_only = true
  }
}

resource "aws_cognito_user_pool_client" "web" {
  count                                = var.enable_cognito ? 1 : 0
  name                                 = "${local.name}-web"
  user_pool_id                         = aws_cognito_user_pool.main[0].id
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  callback_urls                        = ["https://${var.domain_name}/api/v1/auth/sso/callback"]
  supported_identity_providers         = ["COGNITO"]
  prevent_user_existence_errors        = "ENABLED"
}
