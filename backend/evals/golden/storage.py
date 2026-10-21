"""Dedicated evaluation storage; production databases are never a default target."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


class EvaluationStorageError(ValueError):
    """Invalid or unavailable isolated evaluation storage, with secret-free details."""


def _postgres_url(raw: str) -> URL:
    try:
        url = make_url(raw)
        if (url.drivername not in {"postgresql", "postgresql+psycopg"}
                or not url.host or not url.database or "," in url.host
                or set(url.query) & {"host", "hostaddr", "port", "dbname", "database", "service"}):
            raise ValueError
        # Reading .port also validates its type before anything opens a connection.
        _ = url.port
        return url.set(drivername="postgresql+psycopg")
    except (ArgumentError, TypeError, ValueError):
        raise EvaluationStorageError(
            "Evaluation database configuration requires an explicit PostgreSQL host/database "
            "without connection-routing overrides"
        ) from None


def _identity(url: URL) -> tuple[str, int, str]:
    host = url.host.lower().rstrip(".")
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return host, url.port or 5432, url.database


@dataclass(frozen=True)
class EvaluationStorage:
    backend: str
    url: URL = field(repr=False)

    def __post_init__(self):
        if self.backend != "postgresql" or not isinstance(self.url, URL):
            raise EvaluationStorageError("Evaluation storage must select PostgreSQL")
        _postgres_url(self.url.render_as_string(hide_password=False))

    def manifest(self) -> dict:
        result = {"backend": self.backend, "isolation": "unique_schema_per_case"}
        # Credentials never enter the report or its identity fingerprint.
        result["target_sha256"] = hashlib.sha256(
            json.dumps(_identity(self.url)).encode()
        ).hexdigest()
        return result


def configured_evaluation_storage() -> EvaluationStorage:
    runtime = os.getenv("LIMITUPLAB_DATABASE_URL", "").strip()
    selected = os.getenv("LIMITUPLAB_EVAL_DATABASE_URL", "").strip()
    if not selected:
        raise EvaluationStorageError(
            "Evaluations require a separate LIMITUPLAB_EVAL_DATABASE_URL; "
            "the live database and implicit local file fallback are forbidden"
        )
    url = _postgres_url(selected)
    # A different hostname may merely be a DNS/IP alias of the same server.
    # Require a distinct database name even across hosts so aliases cannot bypass
    # the dedicated-database boundary.
    if runtime and url.database == _postgres_url(runtime).database:
        raise EvaluationStorageError(
            "LIMITUPLAB_EVAL_DATABASE_URL must use a different database from LIMITUPLAB_DATABASE_URL; "
            "a different host, user or schema with the same database name is insufficient"
        )
    # Each case supplies its own search_path; no inherited schema may receive writes.
    return EvaluationStorage("postgresql", url.difference_update_query(["options"]))


@contextmanager
def isolated_session_storage(work: Path, configuration: EvaluationStorage):
    """Keep case evidence for inspection in a newly created PostgreSQL schema."""
    from app.storage.schema import upgrade_schema

    metadata = configuration.manifest()
    schema = "golden_eval_" + uuid4().hex
    metadata["schema"] = schema
    engine = None
    try:
        engine = create_engine(configuration.url, hide_parameters=True)
        # CREATE must never adopt another run's schema. DDL and migrations are atomic.
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            upgrade_schema(connection)
        scoped = configuration.url.update_query_dict({"options": f"-csearch_path={schema}"})
    except Exception as error:
        raise EvaluationStorageError(
            "Cannot initialize isolated PostgreSQL evaluation storage "
            f"({type(error).__name__}); no model call was made"
        ) from None
    finally:
        if engine is not None:
            engine.dispose()
    database = scoped.render_as_string(hide_password=False)
    (work / "storage.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    environment = {
        "LIMITUPLAB_DATABASE_URL": database,
        "LIMITUPLAB_SESSION_MEMORY_ENABLED": "true",
        "LIMITUPLAB_SESSION_MEMORY_REFRESH_MESSAGES": "8",
    }
    try:
        with patch.dict(os.environ, environment):
            yield database, metadata
    finally:
        # Close only this case's pool; retained schema holds the replay evidence.
        from app.storage.postgres import get_engine
        get_engine(database).dispose()
