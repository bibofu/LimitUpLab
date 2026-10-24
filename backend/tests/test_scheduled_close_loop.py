"""The scheduler can recover a stopped local PostgreSQL after reboot."""

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts import run_scheduled_close_loop as runner
from scripts.run_daily_close_loop import resolve_target_trade_date


SHANGHAI = ZoneInfo("Asia/Shanghai")


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
    assert '[string]$RunAt = "15:30"' in task_script


def _calendar(*days: date):
    return lambda _start, _end: list(days)


def test_close_loop_targets_today_at_1530():
    target = resolve_target_trade_date(
        now=datetime(2026, 10, 23, 15, 30, tzinfo=SHANGHAI),
        requested_date=None,
        force=False,
        calendar_collector=_calendar(date(2026, 10, 22), date(2026, 10, 23)),
    )

    assert target.trade_date == date(2026, 10, 23)
    assert target.skip_reason is None


def test_close_loop_before_1530_targets_previous_session():
    target = resolve_target_trade_date(
        now=datetime(2026, 10, 23, 15, 29, tzinfo=SHANGHAI),
        requested_date=None,
        force=False,
        calendar_collector=_calendar(date(2026, 10, 22)),
    )

    assert target.trade_date == date(2026, 10, 22)
    assert target.skip_reason is None


def test_close_loop_on_holiday_targets_last_completed_session():
    target = resolve_target_trade_date(
        now=datetime(2026, 10, 1, 15, 30, tzinfo=SHANGHAI),
        requested_date=None,
        force=False,
        calendar_collector=_calendar(date(2026, 9, 30)),
    )

    assert target.trade_date == date(2026, 9, 30)
    assert target.skip_reason is None
