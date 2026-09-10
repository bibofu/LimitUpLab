"""Offline release safety tests; no Docker, SSH or production data required."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import urllib.error
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deployment_release", ROOT / "deploy/release.py")
release = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release
spec.loader.exec_module(release)


# Regression scenario: forced command rejects untrusted arguments.
@pytest.mark.parametrize("command", ["", "bash", "deploy v1.3.1 HEAD", "deploy v1.3.1 " + "a" * 40 + ";id",
                                      "deploy ../main " + "a" * 40, "deploy v1.3.1 " + "A" * 40])
def test_forced_command_rejects_untrusted_arguments(command):
    with pytest.raises(ValueError):
        release.parse_request(command)


# Regression scenario: forced command accepts only exact release.
def test_forced_command_accepts_only_exact_release():
    assert release.parse_request("deploy v1.3.1 " + "a" * 40) == ("v1.3.1", "a" * 40)


# Regression scenario: premarket guard.
@pytest.mark.parametrize("hour,minute,blocked", [(8, 29, False), (8, 30, True), (9, 0, True), (9, 34, True), (9, 35, False)])
def test_premarket_guard(hour, minute, blocked):
    now = datetime(2026, 9, 8, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
    if blocked:
        with pytest.raises(RuntimeError, match="08:30"):
            release.check_window(now)
    else:
        release.check_window(now)


# Prepare the deployment fixture or observation used by the surrounding regression scenario.
@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "STATE", tmp_path)
    monkeypatch.setattr(release, "MAINTENANCE", tmp_path / ".maintenance")
    monkeypatch.setattr(release, "check_window", Mock())
    item = release.Deployment("v1.3.1", "a" * 40)
    item.previous = {"sha": "b" * 40, "path": "old", "backend": "old-back", "frontend": "old-front"}
    item.prepare = Mock(return_value={"sha": "a" * 40, "path": "new", "backend": "new-back", "frontend": "new-front"})
    for name in ("wait_for_jobs", "compose", "backup", "probe", "start_worker", "restore_local_tags", "git"):
        setattr(item, name, Mock())
    item.schema = Mock(return_value={"version": 12, "hash": "unchanged"})
    return item


# Regression scenario: build failure leaves services untouched.
def test_build_failure_leaves_services_untouched(deployment):
    deployment.prepare.side_effect = RuntimeError("build failed")
    with pytest.raises(RuntimeError):
        deployment.execute()
    deployment.compose.assert_not_called()
    assert not release.MAINTENANCE.exists()


# Regression scenario: backup failure restores old services without starting target.
def test_backup_failure_restores_old_services_without_starting_target(deployment):
    deployment.backup.side_effect = RuntimeError("backup failed")
    with pytest.raises(RuntimeError):
        deployment.execute()
    assert deployment.record["status"] == "rolled_back"
    assert not release.MAINTENANCE.exists()
    assert all(call.args[0]["path"] == "old" for call in deployment.compose.call_args_list if "up" in call.args)


# Regression scenario: health failure rolls back only when schema unchanged.
def test_health_failure_rolls_back_only_when_schema_unchanged(deployment):
    deployment.probe.side_effect = [RuntimeError("unhealthy"), None]
    with pytest.raises(RuntimeError):
        deployment.execute()
    assert deployment.record["status"] == "rolled_back"
    deployment.restore_local_tags.assert_called_once_with(deployment.previous)
    assert not release.MAINTENANCE.exists()


# Regression scenario: schema migration failure requires operator recovery.
def test_schema_migration_failure_requires_operator_recovery(deployment):
    deployment.schema.side_effect = [{"version": 10}, {"version": 10}, {"version": 12}]
    deployment.probe.side_effect = RuntimeError("unhealthy")
    with pytest.raises(RuntimeError):
        deployment.execute()
    assert deployment.record["status"] == "manual_recovery_required"
    assert release.MAINTENANCE.exists()
    deployment.restore_local_tags.assert_not_called()
    deployment.start_worker.assert_not_called()


# Regression scenario: success records exact release and reopens site.
def test_success_records_exact_release_and_reopens_site(deployment):
    deployment.execute()
    assert deployment.record["status"] == "success"
    assert json.loads((release.STATE / "current.json").read_text())["sha"] == "a" * 40
    deployment.git.assert_called_once_with("merge", "--ff-only", "a" * 40)
    assert not release.MAINTENANCE.exists()


# Regression scenario: window is checked again after build and job wait.
def test_window_is_checked_again_after_build_and_job_wait(deployment, monkeypatch):
    monkeypatch.setattr(release, "check_window", Mock(side_effect=[None, RuntimeError("window closed")]))
    with pytest.raises(RuntimeError):
        deployment.execute()
    deployment.compose.assert_not_called()
    assert not release.MAINTENANCE.exists()


# Regression scenario: schema probe failure keeps maintenance.
def test_schema_probe_failure_keeps_maintenance(deployment):
    deployment.schema.side_effect = [{"version": 12}, {"version": 12}, RuntimeError("cannot read database")]
    deployment.probe.side_effect = RuntimeError("unhealthy")
    with pytest.raises(RuntimeError):
        deployment.execute()
    assert deployment.record["status"] == "manual_recovery_required"
    assert release.MAINTENANCE.exists()
    deployment.restore_local_tags.assert_not_called()


# Regression scenario: bad tag does not build or stop services.
def test_bad_tag_does_not_build_or_stop_services(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "STATE", tmp_path)
    monkeypatch.setattr(release, "MAINTENANCE", tmp_path / ".maintenance")
    item = release.Deployment("v1.3.1", "a" * 40)
    item.git = Mock(side_effect=["", "main", "b" * 40])
    item.run = Mock()
    item.compose = Mock()
    with pytest.raises(RuntimeError, match="Tag/SHA mismatch"):
        item.prepare()
    item.compose.assert_not_called()


# Regression scenario: tag outside main is rejected before build.
def test_tag_outside_main_is_rejected_before_build(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "STATE", tmp_path)
    monkeypatch.setattr(release, "MAINTENANCE", tmp_path / ".maintenance")
    item = release.Deployment("v1.3.1", "a" * 40)
    item.git = Mock(side_effect=["", "main", "a" * 40, RuntimeError("not an ancestor")])
    item.run = Mock()
    item.compose = Mock()
    with pytest.raises(RuntimeError, match="ancestor"):
        item.prepare()
    item.compose.assert_not_called()


# Regression scenario: shared lock rejects conflicting job.
def test_shared_lock_rejects_conflicting_job(tmp_path, monkeypatch):
    # Model flock deterministically on both Windows and Linux CI.
    lock = tmp_path / "release.lock"
    lock.touch()
    monkeypatch.setattr(release, "LOCK", lock)
    fcntl = Mock(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8)
    fcntl.flock.side_effect = BlockingIOError()
    monkeypatch.setitem(sys.modules, "fcntl", fcntl)
    with pytest.raises(RuntimeError, match="lock is busy"):
        with release.deployment_lock():
            pytest.fail("Must not acquire a busy lock")


# Regression scenario: workflow gates production on matrix success and tag push.
def test_workflow_gates_production_on_matrix_success_and_tag_push():
    text = (ROOT / ".github/workflows/validate.yml").read_text()
    assert 'tags: ["v*"]' in text
    assert "needs: validate" in text
    assert "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in text
    assert "group: limituplab-production\n      cancel-in-progress: false" in text
    assert "DEPLOY_SSH_KEY: ${{ secrets.DEPLOY_SSH_KEY }}" in text
    assert "--ff-only" in (ROOT / "deploy/release.py").read_text()


# Regression scenario: probe distinguishes missing snapshot from broken reader.
@pytest.mark.parametrize("status,allowed", [(404, True), (500, False)])
def test_probe_distinguishes_missing_snapshot_from_broken_reader(tmp_path, monkeypatch, status, allowed):
    monkeypatch.setattr(release, "STATE", tmp_path)
    item = release.Deployment("v1.3.2", "a" * 40)
    class Response:
        status = 200

        # Implement the context/response protocol expected by the code under test using this local
        # fixture.
        def __enter__(self):
            return self

        # Implement the context/response protocol expected by the code under test using this local
        # fixture.
        def __exit__(self, *_args):
            pass

        # Prepare the read fixture or observation used by the surrounding regression scenario.
        def read(self):
            return b'{"status":"ok"}'

    # Build the Response fixture used by the surrounding regression scenario.
    def open_url(url, timeout):
        if url.endswith("/api/agents/recommendation-intelligence"):
            raise urllib.error.HTTPError(url, status, "test", {}, None)
        return Response()

    opener = Mock()
    opener.open.side_effect = open_url
    monkeypatch.setattr(release.urllib.request, "build_opener", Mock(return_value=opener))
    if allowed:
        item.probe()
        assert len(opener.open.call_args_list) == 8
    else:
        with pytest.raises(urllib.error.HTTPError):
            item.probe()
