"""runtime grants and append-only audit

Revision ID: a4660f81f8fd
Revises: 79a204b1b0ff
Create Date: 2026-10-16 04:44:27.397959
"""
from collections.abc import Sequence

from alembic import op

revision: str = 'a4660f81f8fd'
down_revision: str | None = '79a204b1b0ff'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GRANTS = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bankverse_app') THEN
    GRANT USAGE ON SCHEMA control, platform TO bankverse_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA control, platform TO bankverse_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA control, platform TO bankverse_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA control GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bankverse_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA platform GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bankverse_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA control GRANT USAGE, SELECT ON SEQUENCES TO bankverse_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA platform GRANT USAGE, SELECT ON SEQUENCES TO bankverse_app;
    -- The audit trail is append-only for the runtime role (tamper evidence is also provided by the hash chain).
    REVOKE UPDATE, DELETE, TRUNCATE ON control.audit_log FROM bankverse_app;
    -- Public reference data is read-only for the API (written by pipelines with the owner role).
    REVOKE INSERT, UPDATE, DELETE ON platform.ref_series, platform.ref_observations,
      platform.public_documents, platform.public_chunks, platform.data_sources,
      platform.dataset_versions, platform.quality_results, platform.lineage_edges FROM bankverse_app;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(GRANTS)
    op.execute("""
CREATE OR REPLACE FUNCTION control.audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'control.audit_log is append-only';
END $$;
DROP TRIGGER IF EXISTS audit_log_no_update ON control.audit_log;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON control.audit_log
  FOR EACH ROW EXECUTE FUNCTION control.audit_log_immutable();
""")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update ON control.audit_log")
    op.execute("DROP FUNCTION IF EXISTS control.audit_log_immutable()")
