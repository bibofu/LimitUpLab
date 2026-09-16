from contextlib import closing
from datetime import date, datetime
from types import SimpleNamespace
import socket
import sqlite3
import subprocess

import pytest

from app.agent_eval import basic70_live as live


def test_snapshot_guard_allows_only_exact_copy_and_restores_io(tmp_path):
    snapshot = tmp_path / "copy.sqlite"
    original_connect = sqlite3.connect
    with closing(sqlite3.connect(snapshot)) as connection:
        connection.execute("CREATE TABLE facts (value INTEGER)")
    with live.isolated_io(snapshot):
        with closing(sqlite3.connect(snapshot)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute("ATTACH DATABASE ? AS external", (str(tmp_path / "other.sqlite"),))
        with closing(sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
        for target in (tmp_path / "other.sqlite", ":memory:", snapshot.as_uri() + "?mode=rw"):
            with pytest.raises(PermissionError):
                sqlite3.connect(target)
        with pytest.raises(PermissionError):
            socket.create_connection(("example.invalid", 80))
        with pytest.raises(PermissionError):
            subprocess.Popen(["never-execute"])
    assert sqlite3.connect is original_connect
    assert not (tmp_path / "other.sqlite").exists()


def test_live_recipe_has_ten_distinct_real_tools():
    requests = live.live_requests("2026-09-11", "000636")
    assert len(requests) == len({r[0] for r in requests}) == 10
    assert all(args is not None and question for _, args, question in requests)


def test_preparation_preserves_source_and_records_all_failures(tmp_path, monkeypatch):
    database = tmp_path / "source.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript("CREATE TABLE limit_up_events (trade_date TEXT, symbol TEXT);"
                                 "CREATE TABLE stock_daily_bars (trade_date TEXT, symbol TEXT);")
        connection.execute("INSERT INTO limit_up_events VALUES ('2026-09-11','000636')")
        connection.executemany("INSERT INTO stock_daily_bars VALUES (?, '000636')",
                               [(f"2026-08-{n:02}",) for n in range(1, 20)] + [("2026-09-11",)])
        connection.commit()
    before = database.read_bytes()
    monkeypatch.setattr(live.SQLiteLimitUpRepository, "_event_from_row",
                        lambda *args: SimpleNamespace(trade_date=date(2026, 9, 11), board_height=1, symbol="000636"))

    def failing_capture(registry, **kwargs):
        # Even a service write changes only the copy, never the source.
        with closing(sqlite3.connect(registry.first_board_repository.database_path)) as connection:
            connection.execute("UPDATE limit_up_events SET symbol='changed'")
            connection.commit()
        raise ValueError("deliberate unavailable data")

    monkeypatch.setattr(live, "capture_tool", failing_capture)
    output = tmp_path / "ready"
    anchor = datetime.fromisoformat("2026-09-11T18:00:00+08:00")
    report = live.prepare(database, output, anchor)
    assert report["blocked"] == 10 and report["captured"] == 0
    assert report["live_agent_runs"] == report["new_active_golden"] == 0
    assert len(list(output.glob("progress-*.json"))) == 10
    assert database.read_bytes() == before
    with pytest.raises(FileExistsError):
        live.prepare(database, output, anchor)
