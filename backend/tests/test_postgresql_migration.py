"""PostgreSQL migration contracts; real tests use isolated schemas."""

import os
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.storage.schema import upgrade_schema


@pytest.fixture
def pg_engine():
    url = os.getenv("LIMITUPLAB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set LIMITUPLAB_TEST_DATABASE_URL for isolated PostgreSQL integration tests")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    schema = "migration_test_" + uuid4().hex
    admin = sa.create_engine(url, poolclass=sa.pool.NullPool)
    with admin.begin() as db:
        db.execute(sa.schema.CreateSchema(schema))
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool,
                              connect_args={"options": f"-csearch_path={schema}"})
    try:
        upgrade_schema(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(sa.schema.DropSchema(schema, cascade=True))
        admin.dispose()


def test_postgresql_drops_retired_quality_tables(pg_engine):
    tables = set(sa.inspect(pg_engine).get_table_names())
    assert not {name for name in tables if name.startswith("quality_")}
    assert "answer_feedback" in tables


def test_postgresql_enforces_single_champion(pg_engine):
    with pg_engine.begin() as db:
        db.execute(sa.text("INSERT INTO scoring_policies(version,status,factor_weights_json,source,rationale_json,created_at) "
                           "VALUES('v1','champion','{}','test','{}','now')"))
    with pytest.raises(sa.exc.IntegrityError), pg_engine.begin() as db:
        db.execute(sa.text("INSERT INTO scoring_policies(version,status,factor_weights_json,source,rationale_json,created_at) "
                           "VALUES('v2','champion','{}','test','{}','now')"))
