"""Synthetic protocol fixtures only; not real-market Golden cases."""
from datetime import date, datetime
import sqlite3

import pytest

from app.agent_eval.historical_live import HistoricalDataDrift, HistoricalLiveRegistry
from app.agent_eval.local_capture import read_local_session
from app.agent_eval.local_promotion import LocalPromotionRegistry
from app.agent_eval.models import WorldSpec
from app.agent_eval.recorder import capture_tool
from app.agents import tools
from app.agents.react_runtime.evidence import EVIDENCE_VERSION, EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.collectors.limit_down_collector import LimitDownSnapshot
from app.models import StockDailyBar
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS

ANCHOR = datetime.fromisoformat("2026-09-21T18:00:00+08:00")


@pytest.fixture
def baseline(tmp_path):
    database = tmp_path / "source.sqlite"
    events = [SAMPLE_EVENTS[0].model_copy(update={
        "trade_date": date(2026, 9, day), "symbol": "000001", "name": "protocol-only",
        "closed_limit": True, "board_height": height, "break_count": 1,
    }) for day, height in [(18, 1), (21, 2), (22, 3)]]
    SQLiteLimitUpRepository(database).replace_events(events)
    bars = [StockDailyBar(symbol="000001", trade_date=date(2026, 9, day),
        open=opening, high=12, low=9, close=10, volume=100, amount=1000,
        source="synthetic-protocol-only", created_at=ANCHOR) for day, opening in [(18, 10), (21, 11), (22, 12)]]
    SQLiteFirstBoardRepository(database).upsert_daily_bars(bars)
    # Read back the persisted representation before capturing the production method.
    loaded = [item for day in (18, 21) for item in read_local_session(database, ANCHOR.replace(day=day))[2]]
    capture = capture_tool(LocalPromotionRegistry(loaded, bars[:2]), tool="daily_board_promotion",
        arguments={"days": 1, "end_date": "2026-09-21"}, anchor_datetime=ANCHOR,
        recording_id="protocol-promotion", provenance="synthetic protocol test", source_manifest={"synthetic": True})
    world = WorldSpec(world_id="protocol", world_version=1, profile="v1_close_review",
        anchor_datetime=ANCHOR, latest_local_trade_date=ANCHOR.date(),
        trading_calendar={"id": "observed", "version": 1, "start_date": "2026-09-18", "end_date": "2026-09-21",
                          "trading_dates": ["2026-09-18", "2026-09-21"]},
        tool_contract_version=tools.TOOL_CONTRACT_VERSION, evidence_version=EVIDENCE_VERSION,
        recordings=[capture.body.recording])
    return database, world


def test_unrecorded_arguments_execute_real_methods_without_writing(baseline, monkeypatch):
    database, world = baseline
    before = database.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("no remote collection expected")
    monkeypatch.setattr(tools, "collect_limit_down_pool", forbidden)
    registry = HistoricalLiveRegistry(database, world)
    assert len(registry.events) == 2 and len(registry.first_board_repository.bars) == 2
    assert registry.baseline_checks == ["protocol-promotion"]
    assert not hasattr(registry, "execute_frozen_calls")
    assert {s.name for s in registry.schemas()} == registry.enabled_tool_names
    assert not registry.is_enabled("web_search")
    gateway = ToolGateway(registry, EvidenceStore())
    with registry.anchored():
        for tool, arguments in [
            ("limit_up_events", {"trade_date": "2026-09-21", "event_status": "broken_intraday", "sort_by": "break_count", "limit": 7}),
            ("market_event_pool", {"trade_date": "2026-09-21", "event_type": "broken_board", "result_mode": "summary"}),
            ("daily_board_promotion", {"end_date": "2026-09-21", "days": 2}),
        ]:
            args = gateway.validate({"name": tool, "args": arguments})
            _, payload, state = gateway.execute(tool, args)
            assert state in {"ok", "empty"}
            if tool == "daily_board_promotion":
                assert len(payload) == 1 and payload[0]["promoted_stocks"][0]["open_gap_pct"] == 10
    with pytest.raises(ValueError, match="has not enabled"):
        registry.market_summary(include_limit_down=True)
    assert database.read_bytes() == before


def test_public_down_is_explicit_and_actual_collector_is_used(baseline, monkeypatch):
    database, world = baseline
    calls = []
    def collect(day):
        calls.append(day)
        return LimitDownSnapshot(day, [])
    monkeypatch.setattr(tools, "collect_limit_down_pool", collect)
    registry = HistoricalLiveRegistry(database, world, allow_limit_down=True)
    result = registry.market_summary(include_limit_down=True)
    assert calls == [ANCHOR.date()] and result.output["limit_down_count"] == 0
    def failed(day):
        raise OSError("synthetic unit-test failure")
    monkeypatch.setattr(tools, "collect_limit_down_pool", failed)
    assert registry.market_summary(include_limit_down=True).output["limit_down_count"] is None


def test_promotion_bar_drift_blocks_before_model(baseline):
    database, world = baseline
    with sqlite3.connect(database) as c:
        c.execute("UPDATE stock_daily_bars SET open=12 WHERE trade_date='2026-09-21'")
    with pytest.raises(HistoricalDataDrift, match="protocol-promotion"):
        HistoricalLiveRegistry(database, world)


def test_down_baseline_requires_opt_in_and_detects_drift(baseline, monkeypatch):
    database, world = baseline
    monkeypatch.setattr(tools, "collect_limit_down_pool", lambda day: LimitDownSnapshot(day, []))
    registry = HistoricalLiveRegistry(database, world, allow_limit_down=True)
    cap = capture_tool(registry, tool="market_summary", arguments={"include_limit_down": True},
        anchor_datetime=ANCHOR, recording_id="protocol-down", provenance="synthetic protocol test", source_manifest={"synthetic": True})
    world.recordings.append(cap.body.recording)
    with pytest.raises(ValueError, match="has not enabled"):
        HistoricalLiveRegistry(database, world)
    world.recordings[-1].observation.payload["limit_down_count"] = 9
    with pytest.raises(HistoricalDataDrift, match="protocol-down"):
        HistoricalLiveRegistry(database, world, allow_limit_down=True)
