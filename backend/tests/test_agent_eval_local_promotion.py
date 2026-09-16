from datetime import date, datetime
import socket
import sqlite3
import json
import sys

import pytest

from app.agent_eval.local_promotion import LocalPromotionRegistry, record_local_promotion
from app.agent_eval.recorder import digest
from app.models import StockDailyBar
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS


ANCHOR = datetime.fromisoformat("2026-09-11T18:00:00+08:00")
START = date(2026, 9, 10)


@pytest.fixture
def source(tmp_path):
    database = tmp_path / "source.sqlite"
    events = [SAMPLE_EVENTS[0].model_copy(update={
        "symbol": "000001", "trade_date": day, "closed_limit": True, "board_height": height,
    }) for day, height in [(START, 1), (ANCHOR.date(), 2), (date(2026, 9, 14), 3)]]
    SQLiteLimitUpRepository(database).replace_events(events)
    SQLiteFirstBoardRepository(database).upsert_daily_bars([
        StockDailyBar(symbol="000001", trade_date=day, open=opening, high=12, low=9,
                      close=10, volume=100, amount=1000, source="synthetic-test", created_at=ANCHOR)
        for day, opening in [(START, 10), (ANCHOR.date(), 11)]
    ])
    return database


def test_production_tool_over_readonly_snapshot(source, tmp_path, monkeypatch):
    from app.agents import tools
    from app.repositories import first_board_repository, limit_up_repository

    def forbidden(*args, **kwargs):
        raise AssertionError("network or writable repository access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(tools.HithinkFinanceCollector, "__init__", forbidden)
    monkeypatch.setattr(tools.AgentToolRegistry, "__init__", forbidden)
    monkeypatch.setattr(first_board_repository, "connect", forbidden)
    monkeypatch.setattr(limit_up_repository, "connect", forbidden)
    before = source.read_bytes()
    output = tmp_path / "capture.json"
    result = record_local_promotion(source, START, ANCHOR, output)
    assert result["observed_days"] == 1
    assert result["model_calls"] == 0
    assert not result["quality_verified"]
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert digest(artifact["body"]) == artifact["checksum"]
    item = artifact["body"]["trace_output"]["items"][0]
    assert item["sample_size"] == item["promoted_count"] == 1
    assert item["probability"] == 1
    assert item["promoted_stocks"][0]["open_gap_pct"] == 10
    assert artifact["body"]["source_manifest"]["event_rows"] == 2  # No future rows.
    assert source.read_bytes() == before
    second = tmp_path / "second.json"
    record_local_promotion(source, START, ANCHOR, second)
    assert json.loads(second.read_text(encoding="utf-8")) == artifact
    with pytest.raises(FileExistsError):
        record_local_promotion(source, START, ANCHOR, output)
    assert source.read_bytes() == before


def test_missing_bars_remain_explicit(source, tmp_path):
    with sqlite3.connect(source) as connection:
        connection.execute("DELETE FROM stock_daily_bars")
    output = tmp_path / "missing-bars.json"
    record_local_promotion(source, START, ANCHOR, output)
    item = json.loads(output.read_text(encoding="utf-8"))["body"]["trace_output"]["items"][0]
    assert item["first_board_opening_missing_symbols"] == ["000001"]
    assert item["promoted_stocks"][0]["open_gap_pct"] is None


def test_insufficient_and_empty_windows(source, tmp_path):
    assert record_local_promotion(source, ANCHOR.date(), ANCHOR, tmp_path / "one.json")["data_readiness"] == "insufficient"
    with pytest.raises(ValueError, match="no local events"):
        record_local_promotion(source, date(2026, 9, 1), ANCHOR.replace(day=2), tmp_path / "empty.json")
    assert not (tmp_path / "empty.json").exists()


def test_missing_database_and_invalid_parameters(tmp_path):
    source = tmp_path / "absent.sqlite"
    output = tmp_path / "capture.json"
    with pytest.raises(sqlite3.OperationalError):
        record_local_promotion(source, START, ANCHOR, output)
    assert not source.exists()
    with pytest.raises(ValueError, match="timezone"):
        record_local_promotion(source, START, ANCHOR.replace(tzinfo=None), output)
    with pytest.raises(ValueError, match="snapshot window"):
        record_local_promotion(source, START, ANCHOR, output, days=61)
    registry = LocalPromotionRegistry([], [])
    assert [schema.name for schema in registry.schemas()] == ["daily_board_promotion"]
    assert not registry.is_enabled("stock_kline")


def test_cli(source, tmp_path, monkeypatch, capsys):
    from app.agent_eval.__main__ import main

    monkeypatch.setattr(sys, "argv", [
        "agent_eval", "record-local-promotion", "--database", str(source),
        "--start-date", START.isoformat(), "--anchor", ANCHOR.isoformat(),
        "--output", str(tmp_path / "cli.json"),
    ])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["observed_days"] == 1
