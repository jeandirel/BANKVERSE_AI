"""Alembic environment for the 'tenant' migration stream."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool, text

from bankverse.core.config import get_settings
from bankverse.db.base import TenantBase
import bankverse.db.models  # noqa: F401  (populate metadata)

config = context.config
target_metadata = TenantBase.metadata
SCHEMAS = ("bank",)
VERSION_SCHEMA = "bank"


def _url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    return x.get("db_url") or get_settings().database_url


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    schema = getattr(obj, "schema", None) or (obj.table.schema if hasattr(obj, "table") else None)
    if type_ == "table":
        return schema in SCHEMAS
    return True


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      include_schemas=True, include_object=include_object,
                      version_table_schema=VERSION_SCHEMA)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        for schema in SCHEMAS:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, include_schemas=True,
                          include_object=include_object, version_table_schema=VERSION_SCHEMA,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
