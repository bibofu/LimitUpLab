"""Golden evaluations never write to the configured production database."""

import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from evals.golden.runner import run_case
from evals.golden.storage import (
    EvaluationStorage, EvaluationStorageError, configured_evaluation_storage, isolated_session_storage,
)
from test_golden_runner import ScriptedModel, cli_module, example_case


@pytest.mark.parametrize("evaluation", [
    "postgresql://other:secret-b@127.0.0.1:5432/live",
    "postgresql+psycopg://other:secret-b@localhost/live?options=-csearch_path=other",
    "postgresql://other:secret-b@[::1]/live",
    "postgresql://other:secret-b@production-dns-alias/live",
])
def test_same_database_is_rejected_despite_credentials_schema_and_loopback_aliases(evaluation, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql://app:secret-a@127.0.0.1/live")
    monkeypatch.setenv("LIMITUPLAB_EVAL_DATABASE_URL", evaluation)
    with pytest.raises(EvaluationStorageError, match="different database") as error:
        configured_evaluation_storage()
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("evaluation", [
    "malformed-secret-url", "sqlite:///archive.sqlite",
    "postgresql://user:secret@host/eval?host=production",
    "postgresql://user:secret@host/eval?dbname=live",
    "postgresql://user:secret@host/eval?service=production",
])
def test_invalid_or_rerouted_database_configuration_is_rejected_without_secret(evaluation, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_EVAL_DATABASE_URL", evaluation)
    with pytest.raises(EvaluationStorageError) as error:
        configured_evaluation_storage()
    assert "secret" not in str(error.value)


def test_postgres_runtime_requires_separate_evaluation_url_before_provider_or_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql://app:private@127.0.0.1/live")
    monkeypatch.delenv("LIMITUPLAB_EVAL_DATABASE_URL", raising=False)
    monkeypatch.setattr("app.config.configure_runtime_environment", lambda: None)
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", lambda: pytest.fail("provider initialized"))
    with pytest.raises(SystemExit) as error:
        cli_module().main(["--mode", "live", "--output", str(tmp_path / "report")])
    assert error.value.code == 2
    assert not (tmp_path / "report").exists()
    with pytest.raises(EvaluationStorageError, match="LIMITUPLAB_EVAL_DATABASE_URL"):
        run_case(example_case(), trial=1, directory=tmp_path, provider=ScriptedModel())
    assert not (tmp_path / "sessions").exists()
    assert "private" not in capsys.readouterr().err


def test_storage_manifest_does_not_include_or_fingerprint_credentials(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "")
    monkeypatch.setenv("LIMITUPLAB_EVAL_DATABASE_URL", "postgresql://user:secret-a@localhost/eval")
    first = configured_evaluation_storage()
    monkeypatch.setenv("LIMITUPLAB_EVAL_DATABASE_URL", "postgresql://other:secret-b@127.0.0.1:5432/eval")
    second = configured_evaluation_storage()
    assert first.manifest() == second.manifest()
    assert first.manifest()["backend"] == "postgresql"
    assert "secret" not in json.dumps(first.manifest()) + repr(first)


def test_missing_evaluation_configuration_never_creates_an_offline_sqlite_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "")
    monkeypatch.delenv("LIMITUPLAB_EVAL_DATABASE_URL", raising=False)
    with pytest.raises(EvaluationStorageError, match="LIMITUPLAB_EVAL_DATABASE_URL"):
        configured_evaluation_storage()
    assert not list(tmp_path.iterdir())


def test_invalid_engine_configuration_is_sanitized_before_model_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql://app:secret-a@host/live")
    monkeypatch.setenv("LIMITUPLAB_EVAL_DATABASE_URL", "postgresql://eval:secret-b@host/eval")

    def invalid_engine(*args, **kwargs):
        raise ValueError("private configuration detail secret-b")

    monkeypatch.setattr("evals.golden.storage.create_engine", invalid_engine)
    with pytest.raises(EvaluationStorageError, match="no model call was made") as error:
        run_case(example_case(), trial=1, directory=tmp_path, provider=ScriptedModel())
    assert "secret" not in str(error.value)
    assert not list(tmp_path.rglob("*.sqlite"))


def test_scripted_golden_cases_run_only_in_new_postgres_schemas(
    postgresql_database, tmp_path, monkeypatch,
):
    from evals.golden import storage
    from app.storage.postgres import dispose_engines

    base = make_url(postgresql_database)
    runtime = base.set(database="limituplab_production_guard").render_as_string(hide_password=False)
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", runtime)
    monkeypatch.setenv("LIMITUPLAB_EVAL_DATABASE_URL", postgresql_database)
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(tmp_path / "removed-legacy.sqlite"))
    created_schemas = []

    def case_uuid():
        value = uuid4()
        created_schemas.append("golden_eval_" + value.hex)
        return value

    def forbid_sqlite(*args, **kwargs):
        pytest.fail("Golden PostgreSQL evaluation attempted SQLite")

    monkeypatch.setattr(storage, "uuid4", case_uuid)
    monkeypatch.setattr(sqlite3, "connect", forbid_sqlite)
    engine = create_engine(base, hide_parameters=True)
    try:
        for trial in (1, 2):
            result = run_case(example_case(), trial=trial, directory=tmp_path,
                              provider=ScriptedModel(), judge_provider=ScriptedModel())
            assert result["verdict"] == "pass", result["error"]
            assert result["completed"]
            schema = result["storage"]["schema"]
            assert schema == created_schemas[-1]
            assert result["storage"]["backend"] == "postgresql"
            work = Path(result["session_directory"])
            assert not (work / "session.sqlite").exists()
            assert json.loads((work / "storage.json").read_text()) == result["storage"]
            assert os.environ["LIMITUPLAB_DATABASE_URL"] == runtime
            with engine.connect() as db:
                assert db.exec_driver_sql(f'SELECT count(*) FROM "{schema}".chat_sessions').scalar_one() == 1
                assert db.exec_driver_sql(f'SELECT count(*) FROM "{schema}".react_runs').scalar_one() == 1
                assert db.exec_driver_sql(
                    f'SELECT count(*) FROM "{schema}".react_runs WHERE active=0 AND response_json IS NOT NULL'
                ).scalar_one() == 1
                # Fixture schema came from the evaluation URL and must remain empty.
                assert db.exec_driver_sql("SELECT count(*) FROM chat_sessions").scalar_one() == 0
                assert db.exec_driver_sql("SELECT count(*) FROM react_runs").scalar_one() == 0
            serialized = json.dumps(result)
            assert postgresql_database not in serialized
            if base.password:
                assert base.password not in serialized
        assert len(set(created_schemas)) == 2
        assert not list(tmp_path.rglob("*.sqlite"))
    finally:
        dispose_engines()
        with engine.begin() as db:
            for schema in created_schemas:
                # Names came exclusively from this test's generated UUIDs.
                db.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


def test_live_cli_manifest_records_selected_storage_before_case_execution(tmp_path, monkeypatch):
    from evals.golden import runner

    monkeypatch.setattr("app.config.configure_runtime_environment", lambda: None)
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql://app:secret-a@host/live")
    monkeypatch.setenv("LIMITUPLAB_EVAL_DATABASE_URL", "postgresql://eval:secret-b@host/eval")
    monkeypatch.setattr("app.services.llm_provider.get_llm_provider", ScriptedModel)
    module = cli_module()
    monkeypatch.setattr(module, "load_cases", lambda: [example_case()])

    def stop_before_network(*args, **kwargs):
        manifest = json.loads((tmp_path / "report.json").read_text())["manifest"]
        assert manifest["storage"]["backend"] == "postgresql"
        assert "secret" not in json.dumps(manifest["storage"])
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_case", stop_before_network)
    assert module.main(["--mode", "live", "--output", str(tmp_path)]) == 2
