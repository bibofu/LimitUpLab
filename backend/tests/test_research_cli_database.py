"""Research commands use configured PostgreSQL without a retained SQLite file."""

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

from app.database import connect
from app.models import StockDailyBar
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS


COMMANDS = [
    "research_limit_up_paths", "research_pullback_consolidation",
    "export_consolidation_cases", "validate_consolidation_evidence",
]


@pytest.mark.parametrize("module_name", COMMANDS)
def test_research_command_loads_env_and_defaults_to_configured_database(
    module_name, tmp_path, monkeypatch,
):
    env_file = tmp_path / ".env"
    url = "postgresql://research:fixture-secret@127.0.0.1:5432/research"
    env_file.write_text(f"LIMITUPLAB_DATABASE_URL={url}\n", encoding="utf-8")
    monkeypatch.setenv("LIMITUPLAB_ENV_FILE", str(env_file))
    monkeypatch.delenv("LIMITUPLAB_DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", [module_name])
    module = importlib.import_module("scripts." + module_name)
    calls = []

    def analyze(*args):
        assert os.environ["LIMITUPLAB_DATABASE_URL"] == url
        calls.append(args)

    monkeypatch.setattr(module, "analyze", analyze)
    module.main()
    db_index = 0 if module_name == "research_limit_up_paths" else 1
    assert calls[0][db_index] is None
    archive = tmp_path / "explicit-archive.sqlite"
    monkeypatch.setattr(sys, "argv", [module_name, "--db", str(archive)])
    module.main()
    assert calls[1][db_index] == archive


def seed_research_cohorts():
    """Independent artificial fixtures cover each unchanged research cohort."""
    dates = []
    day = date(2026, 7, 1)
    while len(dates) < 35:
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)
    events = [SAMPLE_EVENTS[0].model_copy(update={
        "symbol": "999999", "trade_date": day, "closed_limit": False,
    }) for day in dates]
    bars = []
    paths = {
        "600001": [11, 10.9, 11, 11, 11],
        "600002": [11, 10.9, 11, 11, 11],
        "600003": [11, 10.4, 10.5, 10.5, 10.5],
        "600004": [11, 9.9, 9, 9.1, 9.1],
    }
    for symbol, path in paths.items():
        events.append(SAMPLE_EVENTS[0].model_copy(update={
            "symbol": symbol, "name": "synthetic research fixture", "trade_date": dates[23],
            "closed_limit": True, "board_height": 1,
        }))
        for index, day in enumerate(dates):
            close = 10 if index < 23 else path[min(index - 23, len(path) - 1)]
            bars.append(StockDailyBar(
                symbol=symbol, trade_date=day, open=close, high=close + .02,
                low=close - .08, close=close,
                volume=50 if symbol == "600001" and index > 23 else 100,
                amount=1000, source="synthetic-test",
                created_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
            ))
    SQLiteLimitUpRepository().upsert_events(events)
    SQLiteFirstBoardRepository().upsert_daily_bars(bars)
    return dates


def test_all_research_commands_run_on_postgres_without_sqlite(
    postgresql_database, tmp_path, monkeypatch, capsys,
):
    dates = seed_research_cohorts()
    missing_sqlite = tmp_path / "deleted-legacy.sqlite"
    env_file = tmp_path / "postgresql.env"
    env_file.write_text(f"LIMITUPLAB_DATABASE_URL={postgresql_database}\n", encoding="utf-8")
    monkeypatch.setenv("LIMITUPLAB_ENV_FILE", str(env_file))
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(missing_sqlite))

    def forbid_sqlite(*args, **kwargs):
        pytest.fail("A PostgreSQL research command attempted to open SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbid_sqlite)
    output = tmp_path / "research"
    for name in COMMANDS:
        # Exercise .env discovery at every command, not a previously loaded URL.
        monkeypatch.delenv("LIMITUPLAB_DATABASE_URL", raising=False)
        argv = ([name, "--start", dates[0].isoformat(), "--output", str(output)]
                if name == "research_limit_up_paths" else [name, "--input", str(output)])
        monkeypatch.setattr(sys, "argv", argv)
        importlib.import_module("scripts." + name).main()
    original = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert original["database_backend"] == "postgresql"
    assert original["database_path"] is None
    assert original["kind"] == "historical_exploration_not_live"
    assert original["summary"]["consolidation"]["main"]["n"] > 0
    for filename, key in [
        ("pullback_vs_consolidation.json", "source_input_sha256"),
        ("consolidation_case_evidence.json", "input_sha256"),
        ("consolidation_validation.json", "source_input_sha256"),
    ]:
        result = json.loads((output / filename).read_text(encoding="utf-8"))
        assert result[key] == original["input_sha256"]
    with closing(connect()) as db:
        inputs = [[dict(row) for row in db.execute(query)] for query in original["queries"]]
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    assert digest == original["input_sha256"]
    assert not missing_sqlite.exists()
    printed = capsys.readouterr()
    assert postgresql_database not in printed.out + printed.err

    # New facts cannot be relabelled as the existing frozen research input.
    with closing(connect()) as db, db:
        db.execute("UPDATE stock_daily_bars SET volume=volume+1 WHERE symbol=?", ("600001",))
    before = {path: path.read_bytes() for path in output.iterdir()}
    for name in COMMANDS[1:]:
        monkeypatch.setattr(sys, "argv", [name, "--input", str(output)])
        with pytest.raises(ValueError, match="differs from frozen"):
            importlib.import_module("scripts." + name).main()
    assert all(path.read_bytes() == value for path, value in before.items())
    assert not missing_sqlite.exists()
