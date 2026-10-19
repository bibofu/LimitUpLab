"""Alembic entry point; credentials come only from the process environment."""

import os

import sqlalchemy as sa
from alembic import context

from app.storage.schema import metadata


def database_url():
    value = os.environ.get("LIMITUPLAB_DATABASE_URL", "")
    if value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value[len("postgresql://"):]
    if not value.startswith("postgresql+psycopg://"):
        raise RuntimeError("LIMITUPLAB_DATABASE_URL must select PostgreSQL with psycopg")
    return value


def migrate(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    migrate(context.config.attributes["connection"])
else:
    engine = sa.create_engine(database_url(), poolclass=sa.pool.NullPool)
    try:
        with engine.begin() as connection:
            # Serialize explicit deploy-time upgrades from concurrent launchers.
            connection.execute(sa.text("SELECT pg_advisory_xact_lock(483924107)"))
            migrate(connection)
    finally:
        engine.dispose()
