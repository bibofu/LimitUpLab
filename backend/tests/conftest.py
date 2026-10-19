"""Real PostgreSQL integration fixtures; each test owns a disposable schema."""

import os
from uuid import uuid4
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@pytest.fixture
def postgresql_database(monkeypatch):
    raw_url = os.getenv("LIMITUPLAB_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set LIMITUPLAB_TEST_DATABASE_URL to run real PostgreSQL integration tests")
    from app.storage.schema import upgrade_schema
    from app.storage.postgres import dispose_engines
    base = make_url(raw_url).set(drivername="postgresql+psycopg")
    admin = create_engine(base, isolation_level="AUTOCOMMIT", hide_parameters=True)
    name = "limituplab_test_" + uuid4().hex
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{name}"')
    scoped = base.update_query_dict({"options": f"-csearch_path={name}"})
    engine = create_engine(scoped, hide_parameters=True)
    try:
        upgrade_schema(engine)
        monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", scoped.render_as_string(hide_password=False))
        yield scoped.render_as_string(hide_password=False)
    finally:
        dispose_engines()
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{name}" CASCADE')
        admin.dispose()
