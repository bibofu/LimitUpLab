"""Every database-using test owns disposable schemas in an explicit test DB."""

import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest
import database_support


# Validate before replacing runtime settings; no test may inherit a business DB.
_runtime_database_urls = {
    key: os.getenv(key, "") for key in database_support.RUNTIME_DATABASE_VARIABLES
}
_test_base_url = database_support.test_database_base_url(runtime_urls=_runtime_database_urls)
_test_eval_url = database_support.test_database_base_url(
    "LIMITUPLAB_TEST_EVAL_DATABASE_URL", runtime_urls=_runtime_database_urls,
)
if _test_base_url.database == _test_eval_url.database:
    raise pytest.UsageError("Test business and evaluation databases must have different names")
_scratch_root = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1] / "tmp"))
_scratch_root.mkdir(parents=True, exist_ok=True)
_test_environment = TemporaryDirectory(prefix="pytest-runtime-", dir=_scratch_root,
                                       ignore_cleanup_errors=True)
_test_root = Path(_test_environment.name)
os.environ["LIMITUPLAB_ENV_FILE"] = str(_test_root / "offline-only.env")
os.environ["LIMITUPLAB_DATABASE_URL"] = "postgresql+psycopg://invalid.invalid/collection_has_no_database"
os.environ["LIMITUPLAB_EVAL_DATABASE_URL"] = _test_eval_url.render_as_string(hide_password=False)
os.environ.pop("LIMITUPLAB_DATABASE_PATH", None)
os.environ["LIMITUPLAB_LLM_ENABLED"] = "false"


@pytest.fixture(autouse=True)
def isolated_default_database(monkeypatch):
    """Allocate cheaply; create tables only when a test actually connects."""
    from app.storage.postgres import PostgresConnection
    factory = database_support.SchemaFactory(_test_base_url)
    database_support._factory = factory
    default_url = factory.allocate("default")
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", default_url)
    original_init = PostgresConnection.__init__

    def initialize(self, url=None, *, readonly=False):
        selected = url or os.getenv("LIMITUPLAB_DATABASE_URL", "")
        factory.ensure(selected)
        original_init(self, url, readonly=readonly)

    monkeypatch.setattr(PostgresConnection, "__init__", initialize)
    # Evaluation schemas normally remain as evidence; isolated test evidence is
    # retained until the test finishes, then removed from the dedicated test DB.
    from evals.golden import runner
    original_storage = runner.isolated_session_storage
    evaluation_schemas = []

    @contextmanager
    def test_storage(work, configuration):
        with original_storage(work, configuration) as result:
            if configuration.url.database == _test_eval_url.database:
                evaluation_schemas.append(result[1]["schema"])
            yield result

    monkeypatch.setattr(runner, "isolated_session_storage", test_storage)
    try:
        yield default_url
    finally:
        factory.close()
        if evaluation_schemas:
            from app.storage.postgres import dispose_engines
            from sqlalchemy import create_engine
            dispose_engines()
            engine = create_engine(_test_eval_url, hide_parameters=True)
            try:
                with engine.begin() as db:
                    for schema in evaluation_schemas:
                        if not schema.startswith("golden_eval_") or not schema.isidentifier():
                            raise ValueError("Invalid evaluation fixture schema")
                        db.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            finally:
                engine.dispose()
        database_support._factory = None


@pytest.fixture
def postgresql_database(isolated_default_database):
    database_support._factory.ensure(isolated_default_database)
    return isolated_default_database
