"""Regression runs reject URLs that could route disposable schemas into live data."""

import json

import pytest
from sqlalchemy.engine import make_url

import database_support
from evals.golden.storage import EvaluationStorage, EvaluationStorageError


@pytest.mark.parametrize("override", ["host=live", "hostaddr=127.0.0.1", "port=5432", "dbname=live",
                                      "database=live", "service=live"])
def test_test_url_cannot_override_the_validated_database_identity(monkeypatch, override):
    monkeypatch.setenv("LIMITUPLAB_TEST_DATABASE_URL",
                       "postgresql://user:private@localhost/isolated_test?" + override)
    with pytest.raises(pytest.UsageError) as failure:
        database_support.test_database_base_url()
    assert "private" not in str(failure.value)


def test_test_url_rejects_original_business_names_even_after_environment_reset(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "")
    monkeypatch.setenv("LIMITUPLAB_TEST_FORBIDDEN_DATABASES", json.dumps(["custom_business"]))
    monkeypatch.setenv("LIMITUPLAB_TEST_DATABASE_URL",
                       "postgresql://different_user:private@different_host/custom_business")
    with pytest.raises(pytest.UsageError, match="separate"):
        database_support.test_database_base_url()


@pytest.mark.parametrize("variable", ["LIMITUPLAB_TEST_DATABASE_URL", "LIMITUPLAB_TEST_EVAL_DATABASE_URL"])
@pytest.mark.parametrize("source", ["environment", "env_file"])
def test_test_urls_cannot_select_a_real_evaluation_database(monkeypatch, tmp_path, variable, source):
    live_evaluation = "postgresql://live:private@localhost/production_evidence"
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "")
    monkeypatch.setenv("LIMITUPLAB_EVAL_DATABASE_URL", live_evaluation if source == "environment" else "")
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        "LIMITUPLAB_EVAL_DATABASE_URL=" + live_evaluation if source == "env_file" else "",
        encoding="utf-8",
    )
    monkeypatch.setattr(database_support, "BACKEND_ENV_PATH", env_file)
    monkeypatch.setenv(variable, "postgresql://different:private@alias/production_evidence")
    with pytest.raises(pytest.UsageError, match="separate") as failure:
        database_support.test_database_base_url(variable)
    assert "private" not in str(failure.value)


def test_collection_snapshot_distinguishes_live_eval_from_injected_test_eval(monkeypatch, tmp_path):
    original = {
        "LIMITUPLAB_DATABASE_URL": "postgresql://live:private@localhost/business",
        "LIMITUPLAB_EVAL_DATABASE_URL": "postgresql://live:private@localhost/production_evidence",
    }
    selected = "postgresql://test:private@localhost/isolated_evaluation_test"
    monkeypatch.setattr(database_support, "BACKEND_ENV_PATH", tmp_path / "absent.env")
    monkeypatch.setenv("LIMITUPLAB_TEST_EVAL_DATABASE_URL", selected)
    monkeypatch.setenv("LIMITUPLAB_EVAL_DATABASE_URL", selected)
    result = database_support.test_database_base_url(
        "LIMITUPLAB_TEST_EVAL_DATABASE_URL", runtime_urls=original,
    )
    assert result.database == "isolated_evaluation_test"
    with pytest.raises(pytest.UsageError, match="separate"):
        database_support.test_database_base_url("LIMITUPLAB_TEST_EVAL_DATABASE_URL")


@pytest.mark.parametrize("backend,url", [("sqlite", make_url("sqlite:///legacy.sqlite")),
                                         ("postgresql", make_url("sqlite:///legacy.sqlite")),
                                         ("postgresql", None)])
def test_evaluation_storage_cannot_bypass_configuration_with_a_sqlite_instance(backend, url):
    with pytest.raises(EvaluationStorageError, match="PostgreSQL"):
        EvaluationStorage(backend, url)
