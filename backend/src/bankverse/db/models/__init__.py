"""Import every model module so that metadata is complete for Alembic and create_all."""

from bankverse.db.models import control, platform, tenant  # noqa: F401
