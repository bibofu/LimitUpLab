"""Event-query regression cases use synthetic rows and no data providers."""

from datetime import date

import pytest

from app.agents.tools import AgentToolRegistry
from app.services.sample_data import SAMPLE_EVENTS


BASE = date(2026, 9, 18)
NEXT = date(2026, 9, 21)


def event(symbol, **changes):
    return SAMPLE_EVENTS[0].model_copy(update={
        "symbol": symbol, "name": f"样本{symbol}", "trade_date": NEXT,
        "board_height": 1, "closed_limit": True, "break_count": 0,
        "industry": "测试行业", "concept": "", "amount": 100.0,
        **changes,
    })


def registry(events):
    result = AgentToolRegistry.__new__(AgentToolRegistry)
    result.events = events
    return result


def test_group_counts_use_full_window_before_limiting_rows_and_groups():
    rows = [
        event("600001", trade_date=BASE, concept="算力+算力;机器人"),
        event("600001", concept="算力＋机器人"),
        event("600002", concept="算力", amount=200.0),
        event("600003", concept="", amount=50.0),
    ]
    result = registry(rows).limit_up_events(
        trade_date=NEXT, recent_trade_days=2, group_by="concept",
        sort_by="amount", limit=1,
    )
    facts = result.trace_output
    assert [row.symbol for row in result.output] == ["600002"]
    assert facts["matched_count"] == 4
    assert facts["unique_stock_count"] == 3
    assert facts["returned_count"] == 1
    assert facts["start_trade_date"] == BASE.isoformat()
    assert facts["selected_trade_day_count"] == 2
    assert facts["unclassified_event_count"] == 1
    assert facts["sector_summary"] == [{
        "sector_name": "算力", "unique_stock_count": 2,
        "limit_up_event_count": 3, "trade_day_count": 2,
        "stocks": [{"symbol": "600001", "name": "样本600001"},
                   {"symbol": "600002", "name": "样本600002"}],
    }]
    single_day = registry(rows).limit_up_events(trade_date=NEXT)
    assert single_day.trace_output["matched_count"] == 3
    assert all(row.trade_date == NEXT for row in single_day.output)


def test_industry_labels_remain_whole_and_equal_groups_sort_by_name():
    result = registry([
        event("600003", industry=""), event("600002", industry="B/行业"),
        event("600001", industry="A+行业"),
    ]).limit_up_events(trade_date=NEXT, group_by="industry")
    assert [group["sector_name"] for group in result.trace_output["sector_summary"]] == [
        "A+行业", "B/行业",
    ]
    assert result.trace_output["unclassified_event_count"] == 1


def test_highest_board_is_selected_after_market_status_and_text_filters():
    result = registry([
        event("300001", board_height=9, concept="目标"),
        event("600001", board_height=8, concept="其他"),
        event("600002", board_height=7, concept="目标", closed_limit=False),
        event("600004", board_height=3, concept="目标", amount=300.0),
        event("600003", board_height=3, concept="目标", amount=300.0),
        event("600005", board_height=2, concept="目标"),
    ]).limit_up_events(
        trade_date=NEXT, market="主板", query=" 目标 ", min_board_height=2,
        highest_only=True, sort_by="amount", sort_order="desc",
    )
    assert [row.symbol for row in result.output] == ["600003", "600004"]
    assert result.trace_output["matched_count"] == 2
    assert result.input["market"] == "main_board"


@pytest.mark.parametrize("arguments,expected,status", [
    ({}, ["600001", "600002"], "closed"),
    ({"broken_only": True}, ["600002", "600003"], "broken_intraday"),
    ({"closed_only": False}, ["600001", "600002", "600003"], "all"),
    ({"event_status": "failed", "broken_only": True}, ["600003"], "failed"),
    ({"event_status": "closed", "closed_only": False}, ["600001", "600002"], "closed"),
])
def test_explicit_status_precedes_legacy_flags(arguments, expected, status):
    result = registry([
        event("600001"), event("600002", break_count=2),
        event("600003", break_count=1, closed_limit=False),
    ]).limit_up_events(trade_date=NEXT, **arguments)
    assert [row.symbol for row in result.output] == expected
    assert result.input["event_status"] == status
