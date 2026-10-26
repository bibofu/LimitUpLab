"""Remote CLI calls must never reuse or create a local market database."""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock

import pytest

from app.collectors.hithink_finance_collector import (
    HithinkFinanceCollector,
    HithinkFinanceError,
)


def remote_result(command, *, payload=None, returncode=0):
    if payload is None:
        payload = {"ok": True, "data": {"item": []}, "meta": {"source": "remote"}}
    return subprocess.CompletedProcess(
        command, returncode, stdout=json.dumps(payload), stderr="",
    )


def selected_database(command):
    assert command[command.index("--source") + 1] == "remote"
    database = Path(command[command.index("--db") + 1])
    assert database.is_absolute()
    assert database.parent.is_dir()
    assert not database.exists()
    assert list(database.parent.iterdir()) == []
    return database


@pytest.mark.parametrize("arguments", [
    ("market", "history"),
    ("market", "snapshot"),
    ("special", "hot-stock"),
])
def test_remote_calls_override_personal_database_without_changing_it(
    arguments, tmp_path, monkeypatch,
):
    personal_database = tmp_path / "personal.duckdb"
    personal_database.write_bytes(b"existing user file; must not be touched")
    monkeypatch.setenv("HITHINK_FINANCE_DB_PATH", str(personal_database))
    selected = []

    def runner(command, **kwargs):
        database = selected_database(command)
        selected.append(database)
        assert database != personal_database
        assert os.environ["HITHINK_FINANCE_DB_PATH"] == str(personal_database)
        assert command[-2:] == ["--format", "json"]
        return remote_result(command)

    collector = HithinkFinanceCollector(executable="hithink-finance", runner=runner)
    result = collector._invoke(*arguments)

    assert result["meta"]["source"] == "remote"
    assert personal_database.read_bytes() == b"existing user file; must not be touched"
    assert os.environ["HITHINK_FINANCE_DB_PATH"] == str(personal_database)
    assert not selected[0].parent.exists()


@pytest.mark.parametrize("failure", ["missing_executable", "timeout", "unexpected"])
def test_remote_call_cleans_up_on_runner_failures(failure):
    selected = []

    def runner(command, **kwargs):
        selected.append(selected_database(command))
        if failure == "missing_executable":
            raise FileNotFoundError("missing CLI")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        raise RuntimeError("runner failed")

    collector = HithinkFinanceCollector(
        executable="hithink-finance", runner=runner, timeout_seconds=2,
    )
    expected = RuntimeError if failure == "unexpected" else HithinkFinanceError
    with pytest.raises(expected) as caught:
        collector._invoke("market", "history")

    if failure == "timeout":
        assert caught.value.code == "timeout"
        assert caught.value.retryable is True
    assert not selected[0].parent.exists()


def test_remote_cli_business_failure_keeps_error_contract_and_cleans_up():
    selected = []

    def runner(command, **kwargs):
        selected.append(selected_database(command))
        return remote_result(command, returncode=1, payload={
            "ok": False,
            "error": {"code": "FUYAO_2003", "message": "Invalid API key", "retryable": False},
        })

    collector = HithinkFinanceCollector(executable="hithink-finance", runner=runner)
    with pytest.raises(HithinkFinanceError, match="Invalid API key") as caught:
        collector._invoke("market", "history")

    assert caught.value.code == "FUYAO_2003"
    assert caught.value.retryable is False
    assert not selected[0].parent.exists()


@pytest.mark.parametrize("filename", ["database", "sidecar"])
def test_remote_call_rejects_cli_creating_local_files(filename):
    selected = []

    def runner(command, **kwargs):
        database = selected_database(command)
        selected.append(database)
        target = database if filename == "database" else database.with_suffix(".wal")
        target.write_bytes(b"unexpected local state")
        return remote_result(command)

    collector = HithinkFinanceCollector(executable="hithink-finance", runner=runner)
    with pytest.raises(HithinkFinanceError) as caught:
        collector._invoke("market", "history")

    assert caught.value.code == "local_database_forbidden"
    assert not selected[0].parent.exists()


def test_remote_call_rejects_local_source_even_without_created_files():
    selected = []

    def runner(command, **kwargs):
        selected.append(selected_database(command))
        return remote_result(command, payload={
            "ok": True, "data": {"item": []}, "meta": {"source": "local"},
        })

    collector = HithinkFinanceCollector(executable="hithink-finance", runner=runner)
    with pytest.raises(HithinkFinanceError) as caught:
        collector._invoke("market", "history")

    assert caught.value.code == "local_database_forbidden"
    assert not selected[0].parent.exists()


def test_remote_call_accepts_legacy_success_without_source_metadata():
    selected = []

    def runner(command, **kwargs):
        selected.append(selected_database(command))
        return remote_result(command, payload={"ok": True, "data": {"item": []}})

    collector = HithinkFinanceCollector(executable="hithink-finance", runner=runner)
    assert collector._invoke("market", "history")["ok"] is True
    assert not selected[0].parent.exists()


def test_concurrent_remote_calls_have_independent_empty_directories():
    selected = []
    lock = Lock()
    both_running = Barrier(2)

    def runner(command, **kwargs):
        database = selected_database(command)
        with lock:
            selected.append(database)
        both_running.wait(timeout=5)
        assert len({path.parent for path in selected}) == 2
        assert all(path.parent.is_dir() and not path.exists() for path in selected)
        both_running.wait(timeout=5)
        return remote_result(command)

    collector = HithinkFinanceCollector(executable="hithink-finance", runner=runner)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: collector._invoke("market", "history"), range(2)))

    assert all(result["meta"]["source"] == "remote" for result in results)
    assert len({path.parent for path in selected}) == 2
    assert all(not path.parent.exists() for path in selected)
