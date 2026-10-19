"""Production watchdog regressions using a simulated Docker daemon and clock."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
release_spec = importlib.util.spec_from_file_location("release", ROOT / "deploy/release.py")
release = importlib.util.module_from_spec(release_spec)
release_spec.loader.exec_module(release)
job_spec = importlib.util.spec_from_file_location("production_job", ROOT / "deploy/job.py")
job = importlib.util.module_from_spec(job_spec)
previous_release = sys.modules.get("release")
sys.modules["release"] = release
try:
    job_spec.loader.exec_module(job)
finally:
    if previous_release is None:
        sys.modules.pop("release", None)
    else:
        sys.modules["release"] = previous_release


class DockerSimulation:
    def __init__(self):
        self.events = []
        self.commands = []
        self.held = False
        self.clock = 0
        self.name = None
        self.status = "running"
        self.exit_code = 0
        self.graceful = True
        self.kill_works = True
        self.launch_error = None
        self.launch_code = 0
        self.daemon_down = False
        self.logs_fail = False
        self.interrupt = False

    @contextmanager
    def lock(self, timeout):
        assert timeout == 7200
        self.held = True
        self.events.append("lock")
        try:
            yield
        finally:
            self.events.append("unlock")
            self.held = False

    def sleep(self, seconds):
        self.clock += seconds

    def run(self, command, **kwargs):
        assert self.held, "Every Docker operation must remain inside the shared lock"
        assert 0 < kwargs["timeout"] <= 60
        assert "shell" not in kwargs
        self.commands.append(command)
        action = command[1]
        code, output = 0, ""
        if action in {"compose", "run"}:
            self.events.append("launch")
            self.name = command[command.index("--name") + 1]
            assert "--detach" in command and "--rm" not in command
            if self.launch_error:
                raise self.launch_error(command, kwargs["timeout"])
            code = self.launch_code
        elif action == "inspect":
            self.events.append(f"inspect:{self.status}")
            if self.interrupt:
                self.interrupt = False
                raise InterruptedError("terminated")
            code = 1 if self.daemon_down or self.status == "missing" else 0
            output = json.dumps({"Status": self.status, "Running": self.status == "running",
                                 "Restarting": False, "ExitCode": self.exit_code})
        elif action == "container":
            self.events.append("inventory")
            code = 1 if self.daemon_down else 0
            output = self.name if self.status != "missing" else ""
        elif action in {"stop", "kill"}:
            assert command[-1] == self.name
            self.events.append(action)
            if self.status != "missing" and not self.daemon_down and ((action == "stop" and self.graceful)
                                         or (action == "kill" and self.kill_works)):
                self.status = "exited"
            else:
                code = 1
        elif action == "logs":
            self.events.append("logs")
            assert kwargs["stdout"] is None and kwargs["stderr"] is None
            code = int(self.logs_fail)
        elif action == "rm":
            self.events.append("rm")
            assert self.status in {"exited", "dead", "missing"}
            assert "--force" not in command
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, code, output, "docker error" if code else "")


@pytest.fixture
def docker(monkeypatch, tmp_path):
    fake = DockerSimulation()
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".env.postgresql").write_text("# simulated configuration\n")
    (repository / "docker-compose.postgresql.yml").write_text("services: {}\n")
    monkeypatch.setattr(job, "REPO", repository)
    monkeypatch.setattr(release, "REPO", repository)
    monkeypatch.setattr(job, "MAINTENANCE", tmp_path / ".maintenance")
    monkeypatch.setattr(job, "deployment_lock", fake.lock)
    monkeypatch.setattr(job.subprocess, "run", fake.run)
    monkeypatch.setattr(job.time, "monotonic", lambda: fake.clock)
    monkeypatch.setattr(job.time, "sleep", fake.sleep)
    monkeypatch.setattr(job, "database_mode", lambda: "sqlite")
    monkeypatch.setattr(job, "assert_runtime_database_mode", lambda mode: None)
    monkeypatch.setattr(job, "POSTGRES_BACKUPS", tmp_path / "postgresql")
    return fake


def test_timeout_stops_container_and_confirms_exit_before_unlock(docker):
    assert job.main(["daily-update", "--timeout-seconds", "3"]) == 124
    assert docker.events[-5:] == ["stop", "inspect:exited", "logs", "rm", "unlock"]
    assert docker.clock == 3
    assert not job.MAINTENANCE.exists()


def test_failed_stop_kills_container_then_verifies_exit(docker):
    docker.graceful = False
    assert job.main(["backup", "--timeout-seconds", "1"]) == 124
    assert docker.events[-7:] == ["stop", "inspect:running", "kill", "inspect:exited", "logs", "rm", "unlock"]


@pytest.mark.parametrize("unavailable", [False, True])
def test_unconfirmed_cleanup_closes_maintenance_gate(docker, unavailable):
    docker.graceful = docker.kill_works = False
    docker.daemon_down = unavailable
    assert job.main(["daily-update", "--timeout-seconds", "1"]) == 1
    assert "rm" not in docker.events
    assert docker.name in job.MAINTENANCE.read_text()
    assert "operator recovery required" in job.MAINTENANCE.read_text()
    commands = list(docker.commands)
    assert job.main(["backup"]) == 1
    assert docker.commands == commands


def test_maintenance_record_preserves_existing_details(docker):
    job.MAINTENANCE.write_text("previous deployment failure\n")
    job.require_recovery("container-one", "stop not confirmed")
    assert job.MAINTENANCE.read_text().startswith("previous deployment failure\n")


def test_maintenance_write_failure_retains_lock_until_gate_is_persisted(docker, monkeypatch):
    docker.graceful = docker.kill_works = False
    original_open = Path.open
    writes = 0

    def flaky_open(path, *args, **kwargs):
        nonlocal writes
        if path == job.MAINTENANCE and args and args[0] == "a":
            assert docker.held
            writes += 1
            if writes == 1:
                raise PermissionError("temporary filesystem failure")
        return original_open(path, *args, **kwargs)

    def wait_for_gate(seconds):
        assert docker.held
        if seconds == 30:
            assert "unlock" not in docker.events
        docker.clock += seconds

    monkeypatch.setattr(Path, "open", flaky_open)
    monkeypatch.setattr(job.time, "sleep", wait_for_gate)
    assert job.main(["daily-update", "--timeout-seconds", "1"]) == 1
    assert writes == 2
    assert docker.name in job.MAINTENANCE.read_text()
    assert docker.events[-1] == "unlock"


@pytest.mark.parametrize("exit_code", [0, 2, 7])
def test_completed_job_preserves_exit_code_and_logs_before_removal(docker, exit_code):
    docker.status, docker.exit_code = "exited", exit_code
    assert job.main(["daily-update"]) == exit_code
    assert docker.events == ["lock", "launch", "inspect:exited", "logs", "rm", "unlock"]


def test_uncertain_launch_requires_recovery_even_if_container_disappears(docker):
    docker.launch_error = subprocess.TimeoutExpired
    docker.status = "missing"
    assert job.main(["backup"]) == 1
    assert "launch was not acknowledged" in job.MAINTENANCE.read_text()


def test_nonzero_launch_cannot_exclude_delayed_daemon_creation(docker):
    docker.launch_code = 1
    docker.status = "missing"
    assert job.main(["backup"]) == 1
    assert "launch was not acknowledged" in job.MAINTENANCE.read_text()
    assert "inventory" in docker.events


def test_interruption_also_stops_container_before_unlock(docker):
    docker.interrupt = True
    assert job.main(["daily-update"]) == 1
    assert docker.events[-5:] == ["stop", "inspect:exited", "logs", "rm", "unlock"]


def test_failed_log_read_keeps_stopped_container_for_report_recovery(docker):
    docker.status, docker.logs_fail = "exited", True
    assert job.main(["daily-update"]) == 0
    assert "rm" not in docker.events


def test_jobs_have_unique_names_and_default_time_limits(docker):
    docker.status = "exited"
    assert job.main(["daily-update"]) == 0
    first_name = docker.name
    assert job.main(["daily-update"]) == 0
    assert docker.name != first_name
    assert job.parse_args(["daily-update"]).timeout_seconds == 3600
    assert job.parse_args(["backup"]).timeout_seconds == 900


def test_daily_args_are_validated_and_inner_lock_is_ephemeral(docker):
    docker.status = "exited"
    assert job.main(["daily-update", "--date", "20260923", "--max-attempts", "1"]) == 0
    command = docker.commands[0]
    assert command[command.index("daily-update") + 1:] == [
        "python", "scripts/run_daily_close_loop.py", "--trigger", "manual",
        "--lock-path", "/tmp/daily_close_loop.lock", "--date", "20260923", "--max-attempts", "1",
    ]
    default = job.command_for(job.parse_args(["daily-update"]), "example")
    assert default[-6:] == ["python", "scripts/run_daily_close_loop.py", "--trigger", "scheduled",
                           "--lock-path", "/tmp/daily_close_loop.lock"]


@pytest.mark.parametrize("argv", [
    ["daily-update", "--date", "20260230"],
    ["daily-update", "--date", "20260923;id"],
    ["daily-update", "--date", "2026-09-23"],
    ["daily-update", "--max-attempts", "0"],
    ["daily-update", "--max-attempts", "11"],
    ["daily-update", "--force"],
    ["daily-update", "--lock-path", "/app/data/other"],
    ["backup", "--date", "20260923"],
    ["backup", "--timeout-seconds", "0"],
])
def test_unknown_or_unsafe_arguments_never_launch_containers(docker, argv):
    with pytest.raises(SystemExit) as error:
        job.main(argv)
    assert error.value.code == 2
    assert docker.commands == []


def test_runtime_database_mismatch_blocks_jobs_without_launch(docker, monkeypatch):
    def mismatch(mode):
        assert docker.held and mode == "postgresql"
        raise RuntimeError("Configured database disagrees with the running application")

    monkeypatch.setattr(job, "database_mode", lambda: "postgresql")
    monkeypatch.setattr(job, "assert_runtime_database_mode", mismatch)
    assert job.main(["daily-update"]) == 1
    assert docker.commands == []
    assert not job.MAINTENANCE.exists()


def test_postgresql_daily_job_uses_both_environment_files_and_overlay(docker, monkeypatch):
    monkeypatch.setattr(job, "database_mode", lambda: "postgresql")
    docker.status = "exited"
    assert job.main(["daily-update"]) == 0
    command = docker.commands[0]
    environment_files = [command[index + 1] for index, part in enumerate(command) if part == "--env-file"]
    compose_files = [command[index + 1] for index, part in enumerate(command) if part == "-f"]
    assert environment_files == [str(release.REPO / ".env.production"), str(release.REPO / ".env.postgresql")]
    assert str(job.REPO / "docker-compose.postgresql.yml") in compose_files
    assert command[-6:] == ["python", "scripts/run_daily_close_loop.py", "--trigger", "scheduled",
                           "--lock-path", "/tmp/daily_close_loop.lock"]


def create_archive(directory, index, *, payload=b"archive", checksum=True):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"limituplab-pg-20261019T030000Z-{index:032x}.dump"
    path.write_bytes(payload)
    if checksum:
        path.with_suffix(".dump.sha256").write_text(hashlib.sha256(payload).hexdigest() + "\n", encoding="ascii")
    return path


def test_postgresql_backup_uses_networked_compose_and_prunes_only_after_success(docker, monkeypatch):
    monkeypatch.setattr(job, "database_mode", lambda: "postgresql")
    docker.status = "exited"
    original_run = docker.run
    retained = []

    def create_successful_backup(command, **kwargs):
        result = original_run(command, **kwargs)
        if command[1] == "compose":
            assert "scripts/postgres_admin.py" in command
            assert "scripts/backup_database.py" not in command
            assert "--network" not in command
            assert f"{job.POSTGRES_BACKUPS}:/backups" in command
            output = command[command.index("--output") + 1]
            archive = job.POSTGRES_BACKUPS / Path(output).name
            archive.write_bytes(b"consistent-pg-archive")
            archive.with_suffix(".dump.sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest())
        return result

    def retain(archive):
        assert docker.held and docker.events[-1] == "rm"
        assert job.verified_postgres_backup(archive)
        retained.append(archive)
        return 0

    monkeypatch.setattr(job.subprocess, "run", create_successful_backup)
    monkeypatch.setattr(job, "retain_postgres_backups", retain)
    assert job.main(["backup"]) == 0
    assert len(retained) == 1


@pytest.mark.parametrize("exit_code", [1, 7])
def test_failed_postgresql_backup_never_prunes_existing_archives(docker, monkeypatch, exit_code):
    monkeypatch.setattr(job, "database_mode", lambda: "postgresql")
    monkeypatch.setattr(job, "retain_postgres_backups", lambda _: pytest.fail("Do not prune failed backups"))
    docker.status, docker.exit_code = "exited", exit_code
    assert job.main(["backup"]) == exit_code


def test_successful_container_without_verified_backup_fails_job(docker, monkeypatch):
    monkeypatch.setattr(job, "database_mode", lambda: "postgresql")
    docker.status = "exited"
    assert job.main(["backup"]) == 1
    assert not job.MAINTENANCE.exists()


def test_retention_keeps_14_complete_pairs_and_preserves_foreign_sqlite_or_invalid_files(docker, tmp_path):
    paths = [create_archive(job.POSTGRES_BACKUPS, index) for index in range(16)]
    sqlite_backup = tmp_path / "limituplab-20261018.sqlite"
    sqlite_backup.write_bytes(b"retained original")
    incomplete = create_archive(job.POSTGRES_BACKUPS, 100, checksum=False)
    corrupt = create_archive(job.POSTGRES_BACKUPS, 101)
    corrupt.with_suffix(".dump.sha256").write_text("incorrect")
    foreign = job.POSTGRES_BACKUPS / "manual-recovery.dump"
    foreign.write_bytes(b"manual recovery")
    original_bytes = {path: path.read_bytes() for path in (sqlite_backup, incomplete, corrupt, foreign)}

    assert job.retain_postgres_backups(paths[-1]) == 2
    assert all(not path.exists() and not path.with_suffix(".dump.sha256").exists() for path in paths[:2])
    assert all(job.verified_postgres_backup(path) for path in paths[2:])
    assert all(path.read_bytes() == payload for path, payload in original_bytes.items())


def test_unverified_new_archive_prevents_all_retention(docker):
    old = create_archive(job.POSTGRES_BACKUPS, 1)
    new = create_archive(job.POSTGRES_BACKUPS, 2, checksum=False)
    with pytest.raises(RuntimeError, match="verified archive/checksum"):
        job.retain_postgres_backups(new, retain_count=1)
    assert old.exists() and old.with_suffix(".dump.sha256").exists()


@pytest.mark.parametrize("filename", [
    "manual.dump", "limituplab-pg-20260230T030000Z-" + "a" * 32 + ".dump",
    "limituplab-pg-20261019T030000Z-" + "a" * 31 + ".dump",
    "limituplab-pg-20261019T030000Z-" + "g" * 32 + ".dump",
    "limituplab-pg-20261019T030000Z-" + "a" * 32 + ".dump.sha256",
])
def test_retention_does_not_accept_foreign_filenames(filename):
    assert not job.is_postgres_backup(Path(filename))


def test_command_rejects_unknown_database_and_unsafe_backup_path(docker):
    with pytest.raises(ValueError, match="database mode"):
        job.command_for(job.parse_args(["daily-update"]), "example", "unexpected")
    with pytest.raises(ValueError, match="backup directory"):
        job.command_for(job.parse_args(["backup"]), "example", "postgresql",
                        backup_path=job.POSTGRES_BACKUPS.parent / "manual.dump")
