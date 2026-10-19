"""row level security and grants

Revision ID: 7de10753bcbf
Revises: c6f20c5a0ef6
Create Date: 2026-10-16 04:44:26.826637
"""
from collections.abc import Sequence

import sqlalchemy as sa
import pgvector.sqlalchemy
from alembic import op


revision: str = '7de10753bcbf'
down_revision: str | None = 'c6f20c5a0ef6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APPLY_RLS_FN = """
CREATE OR REPLACE FUNCTION bank.apply_tenant_rls(tbl regclass) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
  EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
  EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s', tbl);
  -- Fail closed: if app.org_id is not set, NULLIF(...) yields NULL and no row matches.
  EXECUTE format($p$CREATE POLICY tenant_isolation ON %s
                 USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)
                 WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)$p$, tbl);
END;
$$;
"""

APPLY_ALL = """
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.oid::regclass AS tbl
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped
           WHERE n.nspname = 'bank' AND c.relkind = 'r'
  LOOP
    PERFORM bank.apply_tenant_rls(r.tbl);
  END LOOP;
END $$;
"""

GRANTS = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bankverse_app') THEN
    GRANT USAGE ON SCHEMA bank TO bankverse_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA bank TO bankverse_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA bank TO bankverse_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA bank GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bankverse_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA bank GRANT USAGE, SELECT ON SEQUENCES TO bankverse_app;
    -- Inference log is append-only for the API.
    REVOKE UPDATE, DELETE ON bank.inference_log FROM bankverse_app;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(APPLY_RLS_FN)
    op.execute(APPLY_ALL)
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute("""
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.oid::regclass AS tbl FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'bank' AND c.relkind = 'r' AND c.relrowsecurity
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s', r.tbl);
    EXECUTE format('ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', r.tbl);
    EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', r.tbl);
  END LOOP;
END $$;
""")
    op.execute("DROP FUNCTION IF EXISTS bank.apply_tenant_rls(regclass)")
