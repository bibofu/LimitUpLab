"""Every database-using test owns disposable schemas in an explicit test DB."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest
import database_support


# Validate before replacing runtime settings; no test may inherit a business DB.
_runtime_database_urls = {
    key: os.getenv(key, "") for key in database_support.RUNTIME_DATABASE_VARIABLES
}
_test_base_url = database_support.test_database_base_url(runtime_urls=_runtime_database_urls)
_scratch_root = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1] / "tmp"))
_scratch_root.mkdir(parents=True, exist_ok=True)
_test_environment = TemporaryDirectory(prefix="pytest-runtime-", dir=_scratch_root,
                                       ignore_cleanup_errors=True)
_test_root = Path(_test_environment.name)
os.environ["LIMITUPLAB_ENV_FILE"] = str(_test_root / "offline-only.env")
os.environ["LIMITUPLAB_DATABASE_URL"] = "postgresql+psycopg://invalid.invalid/collection_has_no_database"
os.environ.pop("LIMITUPLAB_DATABASE_PATH", None)
os.environ["LIMITUPLAB_LLM_ENABLED"] = "false"
# Offline tests must not download or load an embedding model.
os.environ["LIMITUPLAB_MEMORY_EMBEDDINGS"] = "off"


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
    try:
        yield default_url
    finally:
        factory.close()
        database_support._factory = None


@pytest.fixture
def postgresql_database(isolated_default_database):
    database_support._factory.ensure(isolated_default_database)
    return isolated_default_database
