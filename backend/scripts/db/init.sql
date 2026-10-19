-- BANKVERSE AI — database bootstrap (run once as a PostgreSQL superuser / RDS master user).
-- Creates the two roles used by the platform:
--   * bankverse_owner : owns the schemas and runs migrations / batch jobs. NOBYPASSRLS.
--   * bankverse_app   : runtime role of the API. Least privilege. NOBYPASSRLS.
-- Passwords below are LOCAL DEVELOPMENT values only; in AWS they come from Secrets Manager
-- (see infra/terraform/modules/database). Usage:
--   psql -v owner_pw=... -v app_pw=... -v dbname=bankverse -f init.sql

\if :{?owner_pw}
\else
  \set owner_pw bankverse_owner_dev
\endif
\if :{?app_pw}
\else
  \set app_pw bankverse_app_dev
\endif
\if :{?dbname}
\else
  \set dbname bankverse
\endif

SELECT 'CREATE ROLE bankverse_owner LOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS PASSWORD ' || quote_literal(:'owner_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bankverse_owner') \gexec
SELECT 'CREATE ROLE bankverse_app LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS PASSWORD ' || quote_literal(:'app_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bankverse_app') \gexec

SELECT format('CREATE DATABASE %I OWNER bankverse_owner', :'dbname')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname') \gexec

\connect :dbname
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
REVOKE ALL ON DATABASE :dbname FROM PUBLIC;
GRANT CONNECT ON DATABASE :dbname TO bankverse_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
