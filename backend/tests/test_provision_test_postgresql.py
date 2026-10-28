"""The CI helper cannot provision or stop the configured application cluster."""

from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from scripts import provision_test_postgresql as provision


def test_start_requires_ci_environment_before_any_files_or_processes(tmp_path, monkeypatch):
    cluster = tmp_path / "cluster"
    monkeypatch.setattr(provision, "CLUSTER", cluster)
    monkeypatch.setattr(provision.os, "environ", {})
    monkeypatch.setattr("sys.argv", ["provision", "start", "--bin-dir", str(tmp_path)])
    run = Mock()
    monkeypatch.setattr(provision, "run", run)
    with pytest.raises(RuntimeError, match="GITHUB_ENV and GITHUB_PATH"):
        provision.main()
    assert not cluster.exists()
    run.assert_not_called()


def test_existing_directory_is_never_reinitialized(tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "CLUSTER", tmp_path)
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "environment"))
    monkeypatch.setenv("GITHUB_PATH", str(tmp_path / "path"))
    monkeypatch.setattr("sys.argv", ["provision", "start", "--bin-dir", str(tmp_path)])
    run = Mock()
    monkeypatch.setattr(provision, "run", run)
    with pytest.raises(RuntimeError, match="already exists"):
        provision.main()
    run.assert_not_called()


def test_stop_without_owned_cluster_has_no_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "CLUSTER", tmp_path / "absent")
    monkeypatch.setattr("sys.argv", ["provision", "stop"])
    run = Mock()
    monkeypatch.setattr(provision, "run", run)
    provision.main()
    run.assert_not_called()


@pytest.fixture
def disposable_start(tmp_path, monkeypatch):
    cluster = tmp_path / "cluster"
    monkeypatch.setattr(provision, "CLUSTER", cluster)
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "environment"))
    monkeypatch.setenv("GITHUB_PATH", str(tmp_path / "path"))
    monkeypatch.setattr("sys.argv", ["provision", "start", "--bin-dir", str(tmp_path / "bin")])
    listener = Mock()
    listener.getsockname.return_value = ("127.0.0.1", 56432)
    socket_context = Mock()
    socket_context.__enter__ = Mock(return_value=listener)
    socket_context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(provision.socket, "socket", Mock(return_value=socket_context))

    def initialize(_bin_dir, name, *_arguments):
        if name == "initdb":
            (cluster / "data").mkdir()
            (cluster / "data/postgresql.conf").write_text(
                "unix_socket_directories = '/var/run/postgresql'\n", encoding="utf-8")

    runner = Mock(side_effect=initialize)
    monkeypatch.setattr(provision, "run", runner)
    return cluster, listener, runner, initialize


def test_disposable_cluster_overrides_shared_socket_and_uses_loopback_tcp(disposable_start, tmp_path):
    cluster, listener, runner, _initialize = disposable_start
    provision.main()
    listener.bind.assert_called_once_with(("127.0.0.1", 0))
    configuration = (cluster / "data/postgresql.conf").read_text(encoding="utf-8")
    # The last assignment overrides initdb's distribution-specific value.
    assert configuration.rstrip().endswith("unix_socket_directories = ''")
    assert "listen_addresses = '127.0.0.1'\nport = 56432\n" in configuration
    databases = [call.args for call in runner.call_args_list if call.args[1] == "createdb"]
    assert len(databases) == 1
    assert all(args[2:6] == ("-h", "127.0.0.1", "-p", "56432") for args in databases)
    exported = (tmp_path / "environment").read_text(encoding="utf-8")
    assert "@127.0.0.1:56432/limituplab_test" in exported


def test_provision_requires_vector_in_the_disposable_database(disposable_start):
    _cluster, _listener, runner, _initialize = disposable_start
    provision.main()
    checks = [call.args for call in runner.call_args_list if call.args[1] == "psql"]
    assert len(checks) == 1
    assert checks[0][2:] == (
        "-X", "-h", "127.0.0.1", "-p", "56432", "-U", "limituplab_test",
        "-d", "limituplab_test", "-v", "ON_ERROR_STOP=1", "-c",
        "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public",
    )


def test_missing_vector_keeps_cleanup_state_without_exporting_test_url(disposable_start, tmp_path):
    cluster, _listener, runner, initialize = disposable_start
    failure = subprocess.CalledProcessError(1, "psql")

    def missing_extension(bin_dir, name, *arguments):
        initialize(bin_dir, name, *arguments)
        if name == "psql":
            raise failure

    runner.side_effect = missing_extension
    with pytest.raises(subprocess.CalledProcessError) as raised:
        provision.main()
    assert raised.value is failure
    assert (cluster / "cluster.json").is_file()
    assert not (tmp_path / "environment").exists()
    assert not (tmp_path / "path").exists()


@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(1, "pg_ctl"), subprocess.TimeoutExpired("pg_ctl", 90),
])
@pytest.mark.parametrize("has_log", [True, False])
def test_failed_start_exposes_cluster_log_without_masking_error(disposable_start, tmp_path, capsys,
                                                               failure, has_log):
    cluster, _listener, runner, initialize = disposable_start

    def failed_start(bin_dir, name, *arguments):
        initialize(bin_dir, name, *arguments)
        if name == "pg_ctl":
            if has_log:
                (cluster / "postgresql.log").write_text(
                    "FATAL: could not create lock file: Permission denied\n", encoding="utf-8")
            raise failure

    runner.side_effect = failed_start
    with pytest.raises(type(failure)) as raised:
        provision.main()
    assert raised.value is failure
    error_output = capsys.readouterr().err
    assert "Disposable PostgreSQL startup failed" in error_output
    assert ("Permission denied" if has_log else "Cluster log unavailable") in error_output
    assert not (tmp_path / "environment").exists()
    assert not any(call.args[1] == "createdb" for call in runner.call_args_list)
