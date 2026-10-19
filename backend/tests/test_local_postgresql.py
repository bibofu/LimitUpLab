"""Local startup never falls back to SQLite or manages an unrelated cluster."""

from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
from sqlalchemy.engine import make_url

from scripts import local_postgresql as local


@pytest.fixture
def cluster(tmp_path):
    bin_dir = tmp_path / ".local/postgresql/pgsql/bin"
    data_dir = tmp_path / ".local/postgresql/data"
    bin_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    state = local.LocalCluster(bin_dir, data_dir, 55433)
    state.pg_ctl.touch()
    (data_dir / "PG_VERSION").write_text("18")
    env = {
        "LIMITUPLAB_DATABASE_URL": "postgresql://app:secret@127.0.0.1:55433/limituplab",
        "LIMITUPLAB_LOCAL_PG_BIN": ".local/postgresql/pgsql/bin",
        "LIMITUPLAB_LOCAL_PG_DATA": ".local/postgresql/data",
    }
    return tmp_path, state, env


def write_pid(state, *, port=55433, host="127.0.0.1", directory=None):
    (state.data_dir / "postmaster.pid").write_text(
        f"123\n{directory or state.data_dir}\n100\n{port}\n\n{host}\n0\nready\n",
        encoding="utf-8",
    )


def test_sqlite_selection_does_not_touch_postgres(monkeypatch):
    run = Mock()
    monkeypatch.setattr(local, "run_ctl", run)
    assert "SQLite selected" in local.manage("ensure", {})
    run.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("LIMITUPLAB_LOCAL_PG_DATA", "outside"),
    ("LIMITUPLAB_LOCAL_PG_DATA", ".local/postgresql/../../elsewhere"),
    ("LIMITUPLAB_LOCAL_PG_BIN", "outside/bin"),
    ("LIMITUPLAB_LOCAL_PG_BIN", ""),
    ("LIMITUPLAB_DATABASE_URL", "postgresql://app:secret@database.example/limituplab"),
    ("LIMITUPLAB_DATABASE_URL", "postgresql://app:secret@127.0.0.1/limituplab?host=remote"),
])
def test_unrelated_cluster_is_rejected(cluster, field, value, monkeypatch):
    root, state, env = cluster
    env[field] = value
    run = Mock()
    monkeypatch.setattr(local, "run_ctl", run)
    with pytest.raises(RuntimeError):
        local.manage("ensure", env, root=root)
    run.assert_not_called()


def test_external_database_is_checked_but_never_started_or_stopped(monkeypatch):
    env = {"LIMITUPLAB_DATABASE_URL": "postgresql://app:secret@remote/limituplab"}
    check, run = Mock(), Mock()
    monkeypatch.setattr(local, "verify_database", check)
    monkeypatch.setattr(local, "run_ctl", run)
    assert "externally managed" in local.manage("ensure", env)
    check.assert_called_once()
    with pytest.raises(RuntimeError, match="Refusing to stop"):
        local.manage("stop", env)
    run.assert_not_called()


def test_ensure_starts_only_declared_cluster_and_checks_schema(cluster, monkeypatch):
    root, state, env = cluster
    write_pid(state)
    run = Mock(side_effect=[subprocess.CompletedProcess([], 3), subprocess.CompletedProcess([], 0)])
    check = Mock()
    monkeypatch.setattr(local, "run_ctl", run)
    monkeypatch.setattr(local, "verify_database", check)
    assert "55433" in local.manage("ensure", env, root=root)
    assert run.call_args_list[1].args[-4:] == ("-w", "-t", "30", "start")
    assert "-h 127.0.0.1 -p 55433" in run.call_args_list[1].args
    check.assert_called_once_with(make_url(env["LIMITUPLAB_DATABASE_URL"]))


def test_status_never_starts_stopped_cluster(cluster, monkeypatch):
    root, state, env = cluster
    run = Mock(return_value=subprocess.CompletedProcess([], 3))
    monkeypatch.setattr(local, "run_ctl", run)
    with pytest.raises(RuntimeError, match="stopped"):
        local.manage("status", env, root=root)
    run.assert_called_once_with(state, "status")


@pytest.mark.parametrize("values", [{"port": 55432}, {"host": "*"}, {"directory": "/other"}])
def test_wrong_process_identity_cannot_be_stopped(cluster, monkeypatch, values):
    root, state, env = cluster
    write_pid(state, **values)
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(local, "run_ctl", run)
    with pytest.raises(RuntimeError, match="does not match"):
        local.manage("stop", env, root=root)
    run.assert_called_once_with(state, "status")


def test_stop_targets_verified_cluster_without_authentication(cluster, monkeypatch):
    root, state, env = cluster
    write_pid(state)
    run, check = Mock(return_value=subprocess.CompletedProcess([], 0)), Mock()
    monkeypatch.setattr(local, "run_ctl", run)
    monkeypatch.setattr(local, "verify_database", check)
    assert "stopped" in local.manage("stop", env, root=root)
    assert run.call_args.args == (state, "-m", "fast", "-w", "-t", "30", "stop")
    check.assert_not_called()


def test_database_failure_aborts_without_sqlite_fallback(cluster, monkeypatch):
    root, state, env = cluster
    write_pid(state)
    monkeypatch.setattr(local, "run_ctl", Mock(return_value=subprocess.CompletedProcess([], 0)))
    monkeypatch.setattr(local, "verify_database", Mock(side_effect=RuntimeError("not ready")))
    with pytest.raises(RuntimeError, match="not ready"):
        local.manage("ensure", env, root=root)


def test_connection_failure_does_not_disclose_password(monkeypatch):
    import sqlalchemy
    engine = Mock()
    engine.connect.side_effect = RuntimeError("postgresql://app:private-secret@remote/db")
    monkeypatch.setattr(sqlalchemy, "create_engine", Mock(return_value=engine))
    with pytest.raises(RuntimeError) as error:
        local.verify_database(make_url("postgresql://app:private-secret@remote/db"))
    assert "private-secret" not in str(error.value)
    engine.dispose.assert_called_once()


def test_real_database_readiness(postgresql_database):
    local.verify_database(make_url(postgresql_database))
