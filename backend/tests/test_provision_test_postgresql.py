"""The CI helper cannot provision or stop the configured application cluster."""

from pathlib import Path
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
