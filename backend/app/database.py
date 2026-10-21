"""PostgreSQL connections and schema readiness for application storage.

Historical SQLite archives are read only by the dedicated migration tools.
Runtime configuration must select PostgreSQL explicitly; no local file fallback.
"""

import os
from pathlib import Path

from psycopg import IntegrityError
from sqlalchemy.engine import make_url

from app.storage.postgres import PostgresConnection


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def get_database_url(database_url: str | None = None) -> str:
    """Validate configuration without including credentials in error messages."""
    value = database_url if database_url is not None else os.getenv("LIMITUPLAB_DATABASE_URL", "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("LIMITUPLAB_DATABASE_URL is required and must select PostgreSQL")
    try:
        parsed = make_url(value.strip())
    except Exception:
        raise ValueError("LIMITUPLAB_DATABASE_URL must be a valid PostgreSQL URL") from None
    if parsed.drivername not in {"postgresql", "postgresql+psycopg"} or not parsed.database:
        raise ValueError("LIMITUPLAB_DATABASE_URL must select a PostgreSQL database")
    return value.strip()


def get_data_directory() -> Path:
    """Keep reports and caches independent of database connection settings."""
    configured = os.getenv("LIMITUPLAB_DATA_DIRECTORY", "").strip()
    return Path(configured) if configured else BACKEND_ROOT / "data"


def connect(database_url: str | None = None) -> PostgresConnection:
    return PostgresConnection(get_database_url(database_url))


def connect_readonly(database_url: str | None = None) -> PostgresConnection:
    return PostgresConnection(get_database_url(database_url), readonly=True)


def is_postgres(connection) -> bool:
    return getattr(connection, "dialect", None) == "postgresql"


def begin_write(connection, scope: str = "write") -> None:
    connection.begin_write(scope)


def table_exists(connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=?",
        (name,),
    ).fetchone() is not None


def is_integrity_error(error) -> bool:
    return isinstance(error, IntegrityError)


def initialize_database(connection: PostgresConnection) -> None:
    """Reject stale schemas; Alembic alone applies application migrations."""
    from app.storage.schema import HEAD_REVISION
    if getattr(connection, "_schema_checked", False):
        return
    versions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    if [row[0] for row in versions] != [HEAD_REVISION]:
        raise RuntimeError("PostgreSQL schema is not current; run Alembic upgrade head")
    connection._schema_checked = True
