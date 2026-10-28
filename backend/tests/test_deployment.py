"""Offline release safety tests; no Docker, SSH or production data required."""
from datetime import datetime
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import urllib.error
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import yaml

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
    for name in ("wait_for_jobs", "compose", "backup", "probe", "start_workers", "restore_local_tags", "git"):
        setattr(item, name, Mock())
    item.worker_services = Mock(return_value=release.WORKER_SERVICES)
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
    deployment.start_workers.assert_not_called()


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
    workflow = yaml.safe_load(text)
    assert 'tags: ["v*"]' in text
    assert set(workflow["jobs"]["deploy"]["needs"]) == {"validate", "postgresql"}
    assert workflow["jobs"]["postgresql"]["services"]["postgres"]["image"] == "pgvector/pgvector:pg18"
    assert "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in text
    assert "group: limituplab-production\n      cancel-in-progress: false" in text
    assert "DEPLOY_SSH_KEY: ${{ secrets.DEPLOY_SSH_KEY }}" in text
    assert "--ff-only" in (ROOT / "deploy/release.py").read_text()


def test_postgresql_overlay_keeps_sqlite_volume_and_waits_for_schema():
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    overlay = yaml.safe_load((ROOT / "docker-compose.postgresql.yml").read_text())
    postgres = overlay["services"]["postgres"]
    assert postgres["image"] == "pgvector/pgvector:pg18"
    assert postgres["volumes"] == ["limituplab-postgres:/var/lib/postgresql"]
    assert "ports" not in postgres
    assert "limituplab-data:/app/data" in base["services"]["backend"]["volumes"]
    assert base["services"]["backend"]["environment"]["LIMITUPLAB_MEMORY_EMBEDDING_CACHE"] == "/app/data/memory_embeddings"
    for name in ("backend", "recommendation-refresh", "memory-extraction", "memory-embeddings", "daily-update"):
        dependency = overlay["services"][name]["depends_on"]
        assert dependency["postgres"]["condition"] == "service_healthy"
        assert dependency["postgres-migrate"]["condition"] == "service_completed_successfully"


def test_memory_workers_share_backend_image_and_persist_model_cache():
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    for service, script in (
        ("memory-extraction", "scripts/run_memory_extraction.py"),
        ("memory-embeddings", "scripts/run_memory_embeddings.py"),
    ):
        definition = base["services"][service]
        assert definition["image"] == "limituplab-backend:${LIMITUPLAB_IMAGE_TAG:-local}"
        assert definition["command"] == ["python", script]
        assert "ports" not in definition
        assert definition["volumes"] == ["limituplab-data:/app/data"]
        assert definition["restart"] == "unless-stopped"
    embeddings = base["services"]["memory-embeddings"]
    assert embeddings["environment"]["LIMITUPLAB_MEMORY_EMBEDDING_CACHE"] == "/app/data/memory_embeddings"


def test_release_starts_and_inspects_every_background_worker(postgres_release, monkeypatch):
    item = postgres_release
    item.compose = Mock()
    item.run = Mock(return_value=json.dumps({"Running": True, "Restarting": False}))
    monkeypatch.setattr(release.time, "sleep", lambda _: None)
    item.start_workers(item.previous)
    item.compose.assert_called_once_with(
        item.previous, "up", "-d", "--no-build",
        "recommendation-refresh", "memory-extraction", "memory-embeddings",
    )
    inspected = [call.args[2] for call in item.run.call_args_list]
    assert inspected == [
        "limituplab-recommendation-refresh-1",
        "limituplab-memory-extraction-1",
        "limituplab-memory-embeddings-1",
    ]


def test_release_rejects_an_unstable_background_worker(postgres_release, monkeypatch):
    item = postgres_release
    item.compose = Mock()
    item.run = Mock(side_effect=[
        json.dumps({"Running": True, "Restarting": False}),
        json.dumps({"Running": False, "Restarting": True}),
    ])
    monkeypatch.setattr(release.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="memory-extraction worker is not stable"):
        item.start_workers(item.previous)


def test_removed_sqlite_deployment_mode_is_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="Only PostgreSQL"):
        release.compose_arguments(tmp_path, "sqlite")


# Regression scenario: probe distinguishes missing snapshot from broken reader.
@pytest.mark.parametrize("status,allowed", [(404, True), (500, False)])
def test_probe_distinguishes_missing_snapshot_from_broken_reader(tmp_path, monkeypatch, status, allowed):
    monkeypatch.setattr(release, "STATE", tmp_path)
    item = release.Deployment("v1.3.2", "a" * 40)
    item.run = Mock(return_value='{"status":"ok","database":"postgresql"}')
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


@pytest.mark.parametrize("marker,current,expected", [
    ({"engine": "postgresql"}, None, "postgresql"),
    ({"engine": "postgresql"}, {"engine": "postgresql"}, "postgresql"),
])
def test_database_mode_keeps_explicit_selection(tmp_path, monkeypatch, marker, current, expected):
    monkeypatch.setattr(release, "STATE", tmp_path)
    if marker is not None:
        (tmp_path / "database.json").write_text(json.dumps(marker))
    if current is not None:
        (tmp_path / "current.json").write_text(json.dumps(current))
    assert release.database_mode() == expected


@pytest.mark.parametrize("marker,current", [
    (None, None),
    (None, {"sha": "legacy"}),
    ({"engine": "sqlite"}, {"engine": "sqlite"}),
    ({"engine": "postgresql"}, {"sha": "missing-engine"}),
    (None, {"engine": "postgresql"}),
    ({"engine": "sqlite"}, {"engine": "postgresql"}),
    ({"engine": "postgresql"}, {"engine": "sqlite"}),
    ({}, None),
    ({"engine": "unknown"}, None),
    (None, {"engine": "unknown"}),
])
def test_database_mode_cannot_silently_revert_to_sqlite(tmp_path, monkeypatch, marker, current):
    monkeypatch.setattr(release, "STATE", tmp_path)
    if marker is not None:
        (tmp_path / "database.json").write_text(json.dumps(marker))
    if current is not None:
        (tmp_path / "current.json").write_text(json.dumps(current))
    with pytest.raises(RuntimeError):
        release.database_mode()


@pytest.mark.parametrize("configured,running,code", [
    ("postgresql", "sqlite", 0), ("sqlite", "postgresql", 0), ("postgresql", "", 1),
])
def test_runtime_mode_mismatch_blocks_release(monkeypatch, tmp_path, configured, running, code):
    monkeypatch.setattr(release, "REPO", tmp_path)
    runner = Mock(return_value=SimpleNamespace(returncode=code, stdout=running, stderr="private error"))
    monkeypatch.setattr(release.subprocess, "run", runner)
    with pytest.raises(RuntimeError, match="cannot be verified") as error:
        release.assert_runtime_database_mode(configured)
    assert "private error" not in str(error.value)
    assert runner.call_args.args[0][:4] == ["docker", "exec", "limituplab-backend-1", "python"]


def test_runtime_mode_returns_only_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(release, "REPO", tmp_path)
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout="postgresql\n"))
    monkeypatch.setattr(release.subprocess, "run", runner)
    release.assert_runtime_database_mode("postgresql")
    code = runner.call_args.args[0][-1]
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql+psycopg://private:password@internal/db")
    output = io.StringIO()
    with redirect_stdout(output):
        exec(code)
    assert output.getvalue() == "postgresql\n"


@pytest.fixture
def postgres_release(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "STATE", tmp_path)
    monkeypatch.setattr(release, "REPO", tmp_path)
    (tmp_path / "releases").mkdir()
    (tmp_path / ".env.postgresql").touch()
    (tmp_path / "docker-compose.postgresql.yml").touch()
    item = release.Deployment("v1.6.1", "a" * 40)
    item.mode = "postgresql"
    item.previous = {"sha": "b" * 40, "path": str(tmp_path), "engine": "postgresql",
                     "backend": "old-back", "frontend": "old-front"}
    item.declared_services = Mock(return_value=(
        "backend", "frontend", "postgres", "postgres-migrate", "daily-update",
        *release.WORKER_SERVICES,
    ))
    return item


def test_postgres_compose_pins_migration_image_and_explicit_configuration(postgres_release):
    item = postgres_release
    item.run = Mock(return_value="captured")
    assert item.compose(item.previous, "run", "--no-deps", "backend", capture=True) == "captured"
    args = item.run.call_args.args
    assert str(release.REPO / ".env.production") in args
    assert str(release.REPO / ".env.postgresql") in args
    assert str(release.REPO / "docker-compose.postgresql.yml") in args
    override = json.loads((release.STATE / "releases" / ("b" * 40 + "-images.json")).read_text())
    assert override["services"]["postgres-migrate"]["image"] == "old-back"
    assert override["services"]["memory-extraction"]["image"] == "old-back"
    assert override["services"]["memory-embeddings"]["image"] == "old-back"
    assert item.run.call_args.kwargs["capture"] is True


def test_compose_discovers_service_names_without_exporting_configuration(postgres_release):
    item = postgres_release
    del item.declared_services
    names = ("backend", "frontend", "postgres", "postgres-migrate", "recommendation-refresh")
    item.run = Mock(return_value="\n".join(names))
    assert item.declared_services(item.previous) == names
    assert item.declared_services(item.previous) == names
    item.run.assert_called_once()
    assert item.run.call_args.args[-4:] == ("--profile", "*", "config", "--services")
    assert item.run.call_args.kwargs == {"capture": True, "timeout": 30}


def test_compose_service_discovery_fails_before_creating_override(postgres_release):
    item = postgres_release
    del item.declared_services
    item.run = Mock(return_value="backend\nfrontend")
    with pytest.raises(RuntimeError, match="missing required"):
        item.compose(item.previous, "up", "-d")
    assert not list((release.STATE / "releases").glob("*-images.json"))
    assert item.run.call_count == 1


def test_old_release_override_never_invents_memory_workers(postgres_release):
    item = postgres_release
    item.declared_services.return_value = (
        "backend", "frontend", "postgres", "postgres-migrate", "daily-update",
        "recommendation-refresh", *release.LEGACY_WORKER_SERVICES,
    )
    item.run = Mock(return_value="")
    item.compose(item.previous, "stop", "backend")
    override = json.loads((release.STATE / "releases" / ("b" * 40 + "-images.json")).read_text())
    services = override["services"]
    assert "memory-extraction" not in services
    assert "memory-embeddings" not in services
    assert "postgres" not in services
    assert services["postgres-migrate"]["image"] == "old-back"
    assert services["quality-worker"]["image"] == "old-back"
    assert services["quality-judge-worker"]["image"] == "old-back"


def test_rollback_restarts_only_previously_running_declared_workers(postgres_release, monkeypatch):
    item = postgres_release
    item.declared_services.return_value = (
        "backend", "frontend", "postgres", "postgres-migrate",
        "recommendation-refresh", *release.LEGACY_WORKER_SERVICES,
    )
    item.previous["running_workers"] = ["recommendation-refresh", "quality-worker"]
    item.compose = Mock()
    item.run = Mock(return_value=json.dumps({"Running": True, "Restarting": False}))
    monkeypatch.setattr(release.time, "sleep", lambda _: None)
    item.start_workers(item.previous)
    item.compose.assert_called_once_with(item.previous, "up", "-d", "--no-build",
                                         "recommendation-refresh", "quality-worker")
    assert [call.args[2] for call in item.run.call_args_list] == [
        "limituplab-recommendation-refresh-1", "limituplab-quality-worker-1",
    ]


def test_rollback_with_no_running_workers_does_not_start_all_services(postgres_release):
    item = postgres_release
    item.previous["running_workers"] = []
    item.compose = Mock()
    item.run = Mock()
    item.start_workers(item.previous)
    item.compose.assert_not_called()
    item.run.assert_not_called()


def test_upgrade_stops_legacy_workers_before_backup_without_new_worker_names(deployment):
    legacy = ("recommendation-refresh", *release.LEGACY_WORKER_SERVICES)
    deployment.worker_services.side_effect = lambda row: legacy if row["path"] == "old" else release.WORKER_SERVICES
    calls = []
    deployment.compose.side_effect = lambda *args, **kwargs: calls.append(("compose", args))
    deployment.backup.side_effect = lambda: calls.append(("backup", ()))
    deployment.execute()
    assert calls[0] == ("compose", (deployment.previous, "stop", "-t", "60",
                                    *legacy, "frontend", "backend"))
    assert calls[1][0] == "backup"


@pytest.mark.parametrize("missing", [".env.postgresql", "docker-compose.postgresql.yml"])
def test_postgres_compose_missing_configuration_fails_closed(postgres_release, missing):
    (release.REPO / missing).unlink()
    with pytest.raises(RuntimeError):
        release.compose_arguments(release.REPO, "postgresql")


def test_release_never_automatically_crosses_database_engines(postgres_release):
    postgres_release.run = Mock()
    with pytest.raises(RuntimeError, match="cross-database"):
        postgres_release.compose({**postgres_release.previous, "engine": "sqlite"}, "up", "-d")
    postgres_release.run.assert_not_called()


def test_postgres_schema_fingerprint_ignores_only_random_dump_restriction_tokens(postgres_release, monkeypatch):
    admin = Mock()
    admin.connection_environment.return_value = ("private-url", {})
    sql = Mock()
    sql.create_engine.return_value.connect.return_value.execute.return_value.scalars.return_value = ["revision"]
    monkeypatch.setitem(sys.modules, "scripts.postgres_admin", admin)
    monkeypatch.setitem(sys.modules, "sqlalchemy", sql)
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "private-url")

    def run_schema(_release, *args, **kwargs):
        assert args[:3] == ("run", "--rm", "--no-deps")
        assert kwargs["capture"] is True
        output = io.StringIO()
        with redirect_stdout(output):
            exec(args[-1])
        return output.getvalue()

    postgres_release.compose = run_schema
    hashes = []
    for token, table in (("one", "first"), ("two", "first"), ("three", "changed")):
        admin.client.return_value.stdout = (
            f"\\restrict {token}\nCREATE TABLE {table} (id bigint);\n\\unrestrict {token}\n"
        ).encode()
        result = postgres_release.schema()
        assert result["engine"] == "postgresql" and result["version"] == ["revision"]
        hashes.append(result["hash"])
    assert hashes[0] == hashes[1]
    assert hashes[1] != hashes[2]
    assert admin.client.call_args.args[0] == ["pg_dump", "--schema-only", "--no-owner", "--no-privileges"]


def test_postgres_release_backup_is_verified_and_separate_from_daily_retention(postgres_release):
    item = postgres_release
    path = f"/backups/deployments/{item.journal.stem}/postgresql.dump"
    item.compose = Mock(return_value=json.dumps({"backup": path, "sha256": "a" * 64, "archive_readable": True}))
    item.backup()
    assert item.record["backup"] == path.replace("/backups/", "/var/backups/limituplab/")
    assert item.record["backup_sha256"] == "a" * 64
    args = item.compose.call_args.args
    assert "scripts/postgres_admin.py" in args and "backup" in args
    assert "scripts/backup_database.py" not in args


@pytest.mark.parametrize("result", [
    {}, {"archive_readable": False}, {"backup": "/wrong/path", "archive_readable": True, "sha256": "a" * 64},
])
def test_postgres_release_rejects_unverified_backup(postgres_release, result):
    postgres_release.compose = Mock(return_value=json.dumps(result))
    with pytest.raises(RuntimeError, match="verified archive"):
        postgres_release.backup()


@pytest.mark.parametrize("body", [
    {"status": "ok", "database": "sqlite"}, {"status": "unready", "database": "postgresql"},
])
def test_postgres_probe_requires_actual_database_readiness(postgres_release, body):
    postgres_release.run = Mock(return_value=json.dumps(body))
    with pytest.raises(RuntimeError, match="confirm PostgreSQL"):
        postgres_release.probe()


def test_postgres_schema_change_forbids_automatic_rollback(deployment):
    deployment.mode = "postgresql"
    deployment.previous["engine"] = "postgresql"
    deployment.prepare.return_value["engine"] = "postgresql"
    deployment.schema.side_effect = [
        {"engine": "postgresql", "version": ["old"], "hash": "one"},
        {"engine": "postgresql", "version": ["old"], "hash": "one"},
        {"engine": "postgresql", "version": ["new"], "hash": "two"},
    ]
    deployment.probe.side_effect = RuntimeError("readiness failed")
    with pytest.raises(RuntimeError):
        deployment.execute()
    assert deployment.record["status"] == "manual_recovery_required"
    assert release.MAINTENANCE.exists()
    deployment.restore_local_tags.assert_not_called()
