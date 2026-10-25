"""Alembic entry point; credentials come only from the process environment."""

import os

import sqlalchemy as sa
from alembic import context
from alembic.util import CommandError


def require_manual_revisions():
    """Block CLI and Python autogenerate/check before schema comparison."""
    revision = context.get_context().opts.get("revision_context")
    if revision is not None and revision.command_args.get("autogenerate"):
        raise CommandError(
            "LimitUpLab uses hand-written Alembic revisions; autogenerate and check "
            "are disabled because frozen baseline metadata is not the current schema. "
            "Use 'alembic revision -m <description>', edit upgrade(), and run migration tests."
        )


def database_url():
    value = os.environ.get("LIMITUPLAB_DATABASE_URL", "")
    if value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value[len("postgresql://"):]
    if not value.startswith("postgresql+psycopg://"):
        raise RuntimeError("LIMITUPLAB_DATABASE_URL must select PostgreSQL with psycopg")
    return value


def migrate(connection):
    context.configure(connection=connection, target_metadata=None)
    require_manual_revisions()
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=None, literal_binds=True)
    require_manual_revisions()
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
