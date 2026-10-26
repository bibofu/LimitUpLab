"""Frozen revision builders and readiness checks for hand-written migrations.

The baseline manifest describes revision 0001, not today's schema. Later
revisions are authoritative; no incomplete current metadata model is exposed.
JSON and historical dates intentionally remain TEXT.
"""

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext


BASELINE_REVISION = "0001_postgresql_baseline"
HEAD_REVISION = "0008_memory_scopes"
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def baseline_metadata() -> sa.MetaData:
    """Build the immutable first revision without inspecting a live database."""
    manifest = json.loads(Path(__file__).with_name("schema_v0001.json").read_text(encoding="utf-8"))
    result = sa.MetaData()
    types = {"TEXT": sa.Text, "INTEGER": sa.BigInteger, "REAL": sa.Double}
    for name, definition in manifest.items():
        columns = []
        for column_name, kind, required, default in definition["columns"]:
            args = [sa.Identity()] if column_name in definition["identity"] else []
            options = {"nullable": not required}
            if default is not None:
                options["server_default"] = sa.text(default)
            columns.append(sa.Column(column_name, types[kind](), *args, **options))
        constraints = [sa.PrimaryKeyConstraint(*definition["primary_key"])]
        constraints.extend(sa.UniqueConstraint(*columns) for columns in definition["unique"])
        for local, target, remote, on_delete in definition["foreign_keys"]:
            constraints.append(sa.ForeignKeyConstraint(
                local, [f"{target}.{column}" for column in remote],
                ondelete=on_delete, deferrable=True, initially="DEFERRED",
            ))
        table = sa.Table(name, result, *columns, *constraints)
        for index_name, columns, unique, predicate in definition["indexes"]:
            options = {"postgresql_where": sa.text(predicate)} if predicate else {}
            sa.Index(index_name, *(table.c[column] for column in columns), unique=unique, **options)
    return result


def add_coordination_metadata(result: sa.MetaData) -> None:
    """Append revision 0002's shared leases and admission control tables."""
    sa.Table("runtime_leases", result,
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("owner_token", sa.Text, nullable=False),
        sa.Column("fence", sa.BigInteger, nullable=False),
        sa.Column("expires_at", sa.Double, nullable=False))
    attempts = sa.Table("agent_request_attempts", result,
        sa.Column("attempt_id", sa.Text, primary_key=True),
        sa.Column("owner_id", sa.Text, nullable=False),
        sa.Column("ip_address", sa.Text, nullable=False),
        sa.Column("attempted_at", sa.Double, nullable=False))
    sa.Index("idx_agent_request_attempts_time", attempts.c.attempted_at)
    sa.Index("idx_agent_request_attempts_owner", attempts.c.owner_id, attempts.c.attempted_at)
    sa.Index("idx_agent_request_attempts_ip", attempts.c.ip_address, attempts.c.attempted_at)
    slots = sa.Table("agent_request_slots", result,
        sa.Column("token", sa.Text, primary_key=True),
        sa.Column("owner_id", sa.Text, nullable=False),
        sa.Column("expires_at", sa.Double, nullable=False))
    sa.Index("idx_agent_request_slots_expiry", slots.c.expires_at)
    sa.Index("idx_agent_request_slots_owner", slots.c.owner_id)
    sa.Table("agent_request_days", result,
        sa.Column("owner_id", sa.Text, primary_key=True),
        sa.Column("day_key", sa.Text, primary_key=True),
        sa.Column("request_count", sa.BigInteger, nullable=False))


def assert_schema_current(engine_or_connection) -> None:
    """Reject uninitialized/stale databases instead of running request-time DDL."""
    if isinstance(engine_or_connection, sa.Engine):
        with engine_or_connection.connect() as connection:
            return assert_schema_current(connection)
    heads = MigrationContext.configure(engine_or_connection).get_current_heads()
    if heads != (HEAD_REVISION,):
        raise RuntimeError(
            "PostgreSQL schema is not current; run alembic -c backend/alembic.ini upgrade head"
        )


def upgrade_schema(engine_or_connection) -> None:
    """Explicit administrative/test entry point; never called by requests."""
    if isinstance(engine_or_connection, sa.Engine):
        with engine_or_connection.begin() as connection:
            return upgrade_schema(connection)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["connection"] = engine_or_connection
    command.upgrade(config, "head")
