"""The scheduler can recover a stopped local PostgreSQL after reboot."""

from types import SimpleNamespace

import pytest

from scripts import run_scheduled_close_loop as runner


@pytest.mark.parametrize("job_exit", [0, 1, 7])
def test_ensures_database_then_forwards_close_job_and_exit_code(monkeypatch, job_exit):
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(returncode=0 if len(calls) == 1 else job_exit)

    monkeypatch.setattr(runner.subprocess, "run", run)
    arguments = ["--trigger", "scheduled", "--date", "20261016", "--skip-import"]
    assert runner.main(arguments) == job_exit
    assert calls[0][0] == [runner.sys.executable,
                           str(runner.BACKEND_ROOT / "scripts/local_postgresql.py"), "ensure"]
    assert calls[1][0] == [runner.sys.executable,
                           str(runner.BACKEND_ROOT / "scripts/run_daily_close_loop.py"), *arguments]
    assert all(options == {"cwd": runner.BACKEND_ROOT, "check": False} for _, options in calls)


def test_unready_database_prevents_any_close_job(monkeypatch):
    calls = []

    def fail(command, **_options):
        calls.append(command)
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(runner.subprocess, "run", fail)
    assert runner.main(["--trigger", "scheduled"]) == 2
    assert len(calls) == 1
    assert calls[0][-1] == "ensure"


def test_help_does_not_start_database(monkeypatch):
    calls = []

    def run(command, **_options):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main(["--help"]) == 0
    assert len(calls) == 1
    assert calls[0][1].endswith("run_daily_close_loop.py")


def test_windows_task_uses_preflight_wrapper_without_changing_job_arguments():
    task_script = (runner.BACKEND_ROOT.parent / "scripts/daily_close_loop_task.ps1").read_text(encoding="utf-8")
    assert '"scripts\\run_scheduled_close_loop.py"' in task_script
    assert '--trigger scheduled' in task_script
