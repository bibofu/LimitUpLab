"""Disposable PostgreSQL schemas for pytest and unittest-style test cases."""

from __future__ import annotations

import os
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


RUNTIME_DATABASE_VARIABLES = ("LIMITUPLAB_DATABASE_URL",)
BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def test_database_base_url(variable="LIMITUPLAB_TEST_DATABASE_URL", *, runtime_urls=None):
    """Reject live business/evaluation databases before test URL injection.

    Collection passes its original runtime URL snapshot when validating both test
    databases, so a later injected evaluation URL is never mistaken for live data.
    Other callers validate the current environment by default.
    """
    raw = os.getenv(variable, "").strip()
    if not raw:
        raise pytest.UsageError(f"Set {variable} to a dedicated PostgreSQL test database")
    try:
        selected = make_url(raw)
        if (selected.drivername not in {"postgresql", "postgresql+psycopg"}
                or not selected.host or not selected.database or "," in selected.host
                or set(selected.query) & {"host", "hostaddr", "port", "dbname", "database", "service"}):
            raise ValueError
        business_names = {"limituplab", "postgres", "template0", "template1"}
        forbidden = json.loads(os.getenv("LIMITUPLAB_TEST_FORBIDDEN_DATABASES", "[]"))
        if not isinstance(forbidden, list) or not all(isinstance(name, str) for name in forbidden):
            raise ValueError
        business_names.update(forbidden)
        runtime_urls = os.environ if runtime_urls is None else runtime_urls
        for key in RUNTIME_DATABASE_VARIABLES:
            configured = runtime_urls.get(key, "").strip()
            if configured:
                business_names.add(make_url(configured).database)
        if BACKEND_ENV_PATH.exists():
            for line in BACKEND_ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
                key, separator, value = line.strip().partition("=")
                if separator and key in RUNTIME_DATABASE_VARIABLES and value.strip():
                    business_names.add(make_url(value.strip().strip("\"'")).database)
        if selected.database in business_names:
            raise pytest.UsageError("The test database must be separate from configured business and evaluation databases")
        return selected.set(drivername="postgresql+psycopg").difference_update_query(["options"])
    except pytest.UsageError:
        raise
    except Exception:
        raise pytest.UsageError(f"{variable} must be a valid PostgreSQL URL without routing overrides") from None


class SchemaFactory:
    def __init__(self, base):
        self.base = base
        self.admin = create_engine(base, isolation_level="AUTOCOMMIT", hide_parameters=True)
        self.schemas = {}
        self.labels = {}
        self.lock = RLock()

    def allocate(self, label=None):
        with self.lock:
            if label is not None and label in self.labels:
                return self.labels[label]
            schema = "limituplab_test_" + uuid4().hex
            url = self.base.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
            self.schemas[url] = [schema, False]
            if label is not None:
                self.labels[label] = url
            return url

    def ensure(self, url):
        with self.lock:
            record = self.schemas.get(url)
            if record is None or record[1]:
                return
            from app.storage.schema import upgrade_schema
            schema = record[0]
            with self.admin.connect() as db:
                db.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            record[1] = True
            engine = create_engine(url, hide_parameters=True)
            try:
                upgrade_schema(engine)
            finally:
                engine.dispose()

    def release(self, url):
        with self.lock:
            record = self.schemas.get(url)
            if record and record[1]:
                from app.storage.postgres import dispose_engines
                dispose_engines()
                with self.admin.connect() as db:
                    db.exec_driver_sql(f'DROP SCHEMA "{record[0]}" CASCADE')
                record[1] = False

    def close(self):
        try:
            for url in list(self.schemas):
                self.release(url)
            self.schemas.clear()
            self.labels.clear()
        finally:
            self.admin.dispose()


_factory = None


def isolated_database_url(label=None):
    """Return a fresh initialized schema, reusing an optional per-test label."""
    if _factory is None:
        raise RuntimeError("Database fixtures are only available during a pytest test")
    url = _factory.allocate(label)
    _factory.ensure(url)
    return url


def release_database_url(url):
    """Allow unittest cleanup hooks to release a schema before fixture teardown."""
    if _factory is not None:
        _factory.release(url)


def reject_writes(db, *, table, trigger, event="INSERT"):
    """Inject a PostgreSQL integrity failure at the same transaction boundary."""
    if not table.isidentifier() or not trigger.isidentifier() or event not in {"INSERT", "UPDATE"}:
        raise ValueError("Invalid fixture trigger")
    db.execute("""CREATE OR REPLACE FUNCTION fixture_reject_write() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture' USING ERRCODE='23514'; END $$""")
    db.execute(f'CREATE TRIGGER "{trigger}" BEFORE {event} ON "{table}" '
               'FOR EACH ROW EXECUTE FUNCTION fixture_reject_write()')
