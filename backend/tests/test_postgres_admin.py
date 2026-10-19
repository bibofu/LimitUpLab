"""Credential-safe client commands and fail-closed backup/restore behavior."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/postgres_admin.py"
spec = importlib.util.spec_from_file_location("postgres_admin", SCRIPT)
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


def test_client_credentials_stay_in_environment(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "unrelated")
    _, environment = admin.connection_environment(
        "postgresql+psycopg://person:my%40secret@localhost:5433/research?sslmode=require")
    assert environment["PGPASSWORD"] == "my@secret"
    assert environment["PGPORT"] == "5433"
    assert environment["PGSSLMODE"] == "require"
    assert environment["PGDATABASE"] == "research"


def test_unknown_url_options_are_rejected():
    with pytest.raises(admin.AdminError, match="Unsupported"):
        admin.connection_environment("postgresql://person@localhost/db?unexpected=value")


def test_backup_is_exclusive_checksummed_and_readable(tmp_path, monkeypatch):
    commands = []

    def fake_run(command, *, env, stdout, stderr, check):
        commands.append(command)
        if command[0] == "pg_dump":
            stdout.write(b"verified-custom-archive")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(admin.subprocess, "run", fake_run)
    archive = tmp_path / "backup.dump"
    report = admin.backup_database(archive, {"PGPASSWORD": "private"})
    assert report["archive_readable"]
    assert admin.verify_backup(archive, {}) == archive.resolve()
    assert all("private" not in " ".join(command) for command in commands)
    with pytest.raises(admin.AdminError, match="already exists"):
        admin.backup_database(archive, {})
    archive.write_bytes(b"modified")
    with pytest.raises(admin.AdminError, match="checksum"):
        admin.verify_backup(archive, {})


def test_client_failure_does_not_echo_server_secrets(monkeypatch):
    monkeypatch.setattr(admin.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stderr=b"password and private rows"))
    with pytest.raises(admin.AdminError, match="pg_dump failed with exit status 1") as captured:
        admin.client(["pg_dump"], {})
    assert "private" not in str(captured.value)
