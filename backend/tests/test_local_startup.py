"""The local launcher must start only after the configured database is ready."""

import importlib.util
import io
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("local_dev_launcher", ROOT / "scripts/start_dev_detached.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.mark.parametrize("stored,expected,result", [
    ("postgresql", "postgresql", True),
    ("sqlite", "postgresql", False),
    ("postgresql", "sqlite", False),
])
def test_readiness_requires_selected_database(monkeypatch, stored, expected, result):
    response = io.BytesIO(f'{{"status":"ok","database":"{stored}"}}'.encode())
    response.status = 200
    monkeypatch.setattr(launcher.urllib.request, "urlopen", Mock(return_value=response))
    assert launcher.probe("http://local/ready", 1, expected_database=expected) is result


@pytest.mark.parametrize("returncode,running", [(0, True), (3, False)])
def test_postgresql_worker_uses_database_lease(monkeypatch, returncode, running):
    run = Mock(return_value=subprocess.CompletedProcess([], returncode))
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.refresh_worker_running(Path("python"), {"LIMITUPLAB_DATABASE_URL": "postgresql://"}) is running
    assert "job:recommendation-refresh" in run.call_args.args[0][-1]


def test_failed_worker_query_cannot_trigger_duplicate_worker(monkeypatch):
    monkeypatch.setattr(launcher.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 1)))
    with pytest.raises(RuntimeError, match="startup aborted"):
        launcher.refresh_worker_running(Path("python"), {"LIMITUPLAB_DATABASE_URL": "postgresql://"})


def test_worker_without_database_url_is_rejected(monkeypatch):
    run = Mock()
    monkeypatch.setattr(launcher.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="required"):
        launcher.refresh_worker_running(Path("python"), {})
    run.assert_not_called()


def test_database_failure_stops_before_data_check_or_process_spawn(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["start_dev_detached.py"])
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(launcher, "build_env", lambda: {})
    monkeypatch.setattr(launcher, "run_database_check", lambda *_: 1)
    check, spawn = Mock(), Mock()
    monkeypatch.setattr(launcher, "run_data_check", check)
    monkeypatch.setattr(launcher, "spawn_detached", spawn)
    assert launcher.main() == 1
    check.assert_not_called()
    spawn.assert_not_called()


def test_old_sqlite_backend_is_not_accepted_after_switch(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["start_dev_detached.py", "--skip-data-check"])
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(launcher, "build_env", lambda: {"LIMITUPLAB_DATABASE_URL": "postgresql://"})
    monkeypatch.setattr(launcher, "run_database_check", lambda *_: 0)
    monkeypatch.setattr(launcher, "probe", Mock(side_effect=[False, True]))
    spawn = Mock()
    monkeypatch.setattr(launcher, "spawn_detached", spawn)
    assert launcher.main() == 1
    spawn.assert_not_called()


def test_existing_postgres_backend_and_refresh_worker_are_reused(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["start_dev_detached.py", "--skip-data-check"])
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(launcher, "build_env", lambda: {"LIMITUPLAB_DATABASE_URL": "postgresql://"})
    monkeypatch.setattr(launcher, "run_database_check", lambda *_: 0)
    monkeypatch.setattr(launcher, "probe", Mock(return_value=True))
    monkeypatch.setattr(launcher, "refresh_worker_running", Mock(return_value=True))
    monkeypatch.setattr(launcher, "wait_for", Mock(return_value=True))
    spawn = Mock()
    monkeypatch.setattr(launcher, "spawn_detached", spawn)
    assert launcher.main() == 0
    spawn.assert_not_called()


def test_launch_environment_loads_saved_database_and_preserves_model_settings(monkeypatch):
    from app import config
    def configure():
        monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql://saved")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setenv("LIMITUPLAB_LLM_MODEL", "configured-model")
    monkeypatch.setattr(config, "configure_runtime_environment", configure)
    monkeypatch.setattr(launcher, "auto_detect_local_proxy", lambda: "")
    env = launcher.build_env()
    assert env["LIMITUPLAB_DATABASE_URL"] == "postgresql://saved"
    assert env["LIMITUPLAB_LLM_MODEL"] == "configured-model"


def test_pg_ctl_uses_file_handles_instead_of_inherited_pipes(tmp_path, monkeypatch):
    from scripts import local_postgresql
    state = local_postgresql.LocalCluster(tmp_path, tmp_path / "data", 55433)
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(local_postgresql.subprocess, "run", run)
    local_postgresql.run_ctl(state, "start")
    options = run.call_args.kwargs
    assert options["stdout"] is options["stderr"]
    assert options["stdout"] is not subprocess.PIPE
    assert "capture_output" not in options
