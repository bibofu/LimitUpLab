"""Event-query regression cases use synthetic rows and no data providers."""

from datetime import date, time

import pytest

from app.agents.tools import AgentToolRegistry
from app.agents.runner.gateway import BusinessToolGateway
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


@pytest.mark.parametrize("order,expected", [
    (None, ["000002", "300001"]),
    ("asc", ["000002", "300001"]),
    ("desc", ["600003", "300001"]),
])
def test_gateway_accepts_symbol_sort_on_first_call_before_limiting_rows(order, expected):
    tool_registry = registry([
        event("600003", board_height=1), event("000002", board_height=2),
        event("300001", board_height=3),
    ])
    tool_registry.profile = "v1_close_review"
    gateway = BusinessToolGateway(tool_registry)
    args = gateway.validate({"name": "limit_up_events", "args": {
        "trade_date": NEXT.isoformat(), "sort_by": "symbol", "sort_order": order, "limit": 2,
    }})
    _, payload, status = gateway.execute("limit_up_events", args)
    assert status == "ok"
    assert [row["symbol"] for row in payload["events"]] == expected
    assert payload["matched_count"] == 3 and payload["returned_count"] == 2
    assert payload["sort_by"] == "symbol"
    assert payload["sort_order"] == (order or "asc")


@pytest.mark.parametrize("sort_by,expected,order", [
    (None, ["300001", "000002", "600003"], "desc"),
    ("height", ["300001", "000002", "600003"], "desc"),
    ("first_limit_time", ["600003", "000002", "300001"], "asc"),
    ("封板时间", ["600003", "000002", "300001"], "asc"),
    ("code", ["000002", "300001", "600003"], "asc"),
    ("股票代码", ["000002", "300001", "600003"], "asc"),
])
def test_symbol_support_preserves_existing_sort_defaults_and_normalized_names(sort_by, expected, order):
    result = registry([
        event("000002", board_height=2, first_limit_time=time(10)),
        event("600003", board_height=1, first_limit_time=time(9, 30)),
        event("300001", board_height=3, first_limit_time=time(11)),
    ]).limit_up_events(trade_date=NEXT, sort_by=sort_by)
    assert [row.symbol for row in result.output] == expected
    assert result.input["sort_order"] == order
