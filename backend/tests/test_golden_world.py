"""Exercise evaluator fixtures through production validation, without external IO."""

from datetime import date
from inspect import signature
import socket
import sqlite3

import pytest

from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import AgentToolRegistry, TOOL_SCHEMAS
from app.models import FirstBoardRatingsResponse, StockKLineFacts, StockNewsFacts
from evals.golden.contracts import Case, Expectation, Turn
from evals.golden.world import (
    DATES, SOURCE, SUPPORTED_TOOLS, FixtureCoverageGap, FrozenRegistry,
)


@pytest.fixture(autouse=True)
def no_production_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Golden-world tests must never open a database or network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(AgentToolRegistry, "__init__", forbidden)


def registry(variant="normal"):
    return FrozenRegistry(Case(id="world_test", category="single", family="fixture",
                               tags=["fixture"], variant=variant,
                               turns=[Turn(user="查询合成资料", expect=Expectation())]))


def execute(target, name, **arguments):
    gateway = ToolGateway(target, EvidenceStore())
    arguments = gateway.validate({"name": name, "args": arguments})
    return gateway.execute(name, arguments)


CALLS = [
    ("limit_up_events", {"trade_date": "2026-09-22", "limit": 100}),
    ("market_event_pool", {"event_type": "limit_up", "trade_date": "2026-09-22"}),
    ("market_summary", {}),
    ("first_board_ratings", {"trade_date": "2026-09-22"}),
    ("first_board_filter", {"query": "人工智能", "trade_date": "2026-09-22"}),
    ("stock_kline", {"symbol": "华岳科技", "end_date": "2026-09-22", "days": 20}),
    ("stock_news", {"symbol": "600101"}),
    ("hot_stock_ranking", {"limit": 5}),
]


def test_catalog_is_complete_and_not_restricted_to_expected_tools():
    target = registry()
    assert [schema.model_dump() for schema in target.schemas()] == [schema.model_dump() for schema in TOOL_SCHEMAS]
    assert target.enabled_tool_names == {schema.name for schema in TOOL_SCHEMAS}
    assert {name for name, _ in CALLS} == SUPPORTED_TOOLS
    assert len(target.enabled_tool_names) > len(SUPPORTED_TOOLS)
    for schema in target.schemas():
        assert target.is_enabled(schema.name)
        if schema.adapter == "direct":
            assert set(signature(getattr(target, schema.name)).parameters) == set(schema.args_schema["properties"])


@pytest.mark.parametrize("name,arguments", CALLS)
def test_supported_tools_accept_production_schema_and_are_offline(name, arguments):
    target = registry()
    result, payload, state = execute(target, name, **arguments)
    assert result.name == name and state == "ok"
    assert isinstance(payload, dict)
    assert payload["source"] in {SOURCE, "first-board-ratings"}
    assert not target.unsupported_tools


@pytest.mark.parametrize("name", sorted({schema.name for schema in TOOL_SCHEMAS} - SUPPORTED_TOOLS))
def test_unimplemented_tools_record_coverage_gaps_without_running_production(name):
    target = registry()
    schema = next(schema for schema in TOOL_SCHEMAS if schema.name == name)
    values = {"start_date": "2026-09-18", "end_date": "2026-09-22",
              "symbol": "600101", "sector": "半导体", "query": "合成"}
    arguments = {key: values[key] for key in schema.args_schema.get("required", [])}
    for _ in range(2):
        with pytest.raises(FixtureCoverageGap, match="Synthetic fixture does not implement"):
            execute(target, name, **arguments)
    assert target.unsupported_tools == [name]


def test_latest_filters_statuses_and_ranking_use_real_event_logic():
    target = registry()
    _, payload, _ = execute(target, "limit_up_events", trade_date="2026-09-22",
                            market="main_board", board_height=1)
    assert [row["symbol"] for row in payload["events"]] == ["000202", "000910", "600101"]
    _, payload, _ = execute(target, "limit_up_events", event_status="failed")
    assert [row["symbol"] for row in payload["events"]] == ["000707", "300808"]
    _, payload, _ = execute(target, "limit_up_events", event_status="closed", broken_only=True)
    # Explicit event_status has production precedence over the legacy flag.
    assert payload["matched_count"] == 8
    _, payload, _ = execute(target, "limit_up_events", broken_only=True)
    assert {row["symbol"] for row in payload["events"]} == {"000202", "688404", "000707", "300808", "600909"}
    _, payload, _ = execute(target, "limit_up_events", sort_by="amount", sort_order="desc", limit=3)
    assert [row["symbol"] for row in payload["events"]] == ["600101", "000202", "300303"]
    assert payload["matched_count"] == 8


def test_dates_and_count_mode_have_independent_expected_facts():
    target = registry()
    assert sorted({event.trade_date for event in target.events}) == list(DATES)
    for day, expected in [("2026-09-18", 7), ("2026-09-21", 7), ("2026-09-22", 8)]:
        _, payload, _ = execute(target, "market_event_pool", event_type="limit_up",
                                trade_date=day, result_mode="count")
        assert payload["matched_count"] == expected
        assert payload["items"] == []
    _, payload, _ = execute(target, "market_summary", include_limit_down=True)
    assert {key: payload[key] for key in ("limit_up_count", "first_board_count", "continued_board_count",
                                         "unsealed_count", "max_board_height", "limit_down_count")} == {
        "limit_up_count": 8, "first_board_count": 5, "continued_board_count": 3,
        "unsealed_count": 2, "max_board_height": 3, "limit_down_count": 0,
    }


def test_ratings_are_separate_from_event_universe_and_keep_typed_output():
    target = registry()
    result, payload, _ = execute(target, "first_board_ratings", trade_date="2026-09-22")
    FirstBoardRatingsResponse.model_validate(result.output)
    assert payload["universe_count"] == 10
    assert [(row["symbol"], row["score"]) for row in payload["top_candidates"]] == [
        ("600101", 90), ("000202", 85), ("300303", 80), ("000910", 70)]
    _, payload, _ = execute(target, "first_board_ratings", symbols=["000202"])
    assert payload["universe_count"] == 10
    assert [row["symbol"] for row in payload["top_candidates"]] == ["000202"]
    _, payload, _ = execute(target, "first_board_filter", query="人工智能")
    assert [row["symbol"] for row in payload["items"]] == ["600101", "300303"]


def test_kline_metrics_match_generated_intervals_and_news_are_synthetic():
    target = registry()
    result, payload, _ = execute(target, "stock_kline", symbol="600101", days=20)
    StockKLineFacts.model_validate(result.output)
    assert len(payload["bars"]) == 20 and payload["return_10d_pct"] == 1.2
    closes = [bar["close"] for bar in payload["bars"]]
    assert (closes[-1] / closes[-11] - 1) * 100 == pytest.approx(1.2, abs=0.00001)
    assert all(bar["source"] == SOURCE for bar in payload["bars"])
    result, payload, _ = execute(target, "stock_news", symbol="600101")
    StockNewsFacts.model_validate(result.output)
    assert payload["items"][0]["title"] == "【合成】华岳科技研究资料"
    assert payload["items"][0]["url"] == "https://example.invalid/golden/600101"
    _, payload, _ = execute(target, "hot_stock_ranking", limit=5)
    assert [row["symbol"] for row in payload["items"]] == ["000202", "600101", "300303", "688404", "600606"]


@pytest.mark.parametrize("name,arguments", CALLS)
def test_empty_and_error_variants_preserve_real_execution_boundary(name, arguments):
    _, payload, state = execute(registry("empty"), name, **arguments)
    assert state == "empty"
    for key in ("events", "items", "candidates", "bars", "top_candidates"):
        if key in payload:
            assert payload[key] == []
    with pytest.raises(RuntimeError, match="Synthetic provider failure"):
        execute(registry("error"), name, **arguments)


@pytest.mark.parametrize("variant", ["partial", "truncated"])
def test_missing_and_truncation_are_not_promoted_to_complete_evidence(variant):
    result, payload, state = execute(registry(variant), "limit_up_events", trade_date="2026-09-22")
    store = EvidenceStore()
    key = store.add(tool=result.name, payload=payload, state=state, arguments=result.input)
    assert store.get(key)["result_state"] == "partial"
    assert payload["matched_count"] == 8
    if variant == "partial":
        assert len(payload["events"]) == 8 and store.get(key)["data_missing"]
    else:
        assert len(payload["events"]) == 2 and store.get(key)["source_truncated"]


def test_stale_dates_are_preserved_and_gateway_rejects_them():
    target = registry("stale")
    result = target.limit_up_events(trade_date=date(2026, 9, 22))
    assert result.output["trade_date"] == "2026-09-21"
    assert {row["trade_date"] for row in result.output["events"]} == {"2026-09-21"}
    with pytest.raises(ValueError, match="requires 2026-09-22"):
        execute(target, "limit_up_events", trade_date="2026-09-22")


def test_injection_is_untrusted_source_text_not_a_tool_instruction():
    _, news, _ = execute(registry("injection"), "stock_news", symbol="600101")
    assert "SYNTHETIC_INJECTION_CANARY" in news["items"][0]["summary"]
    _, events, _ = execute(registry("injection"), "limit_up_events", board_height=1)
    assert len(events["events"]) == 5


def test_registry_rejects_unknown_names_dates_and_arguments():
    target = registry()
    with pytest.raises(ValueError, match="Unknown or ambiguous"):
        target.resolve_stock_identity("不存在的股票")
    with pytest.raises(ValueError, match="only contains"):
        target.limit_up_events(trade_date=date(2026, 9, 23))
    with pytest.raises(ValueError, match="Invalid tool arguments"):
        execute(target, "limit_up_events", invented=True)
    with pytest.raises(AttributeError):
        target.production_database


def test_three_condition_intersection_detects_each_omitted_constraint():
    target = registry()
    # Read the raw frozen facts: these reference sets do not reuse the query
    # implementation or derive their expected members from the case oracle.
    sealed = [event for event in target.events
              if event.trade_date == date(2026, 9, 22) and event.closed_limit]
    first = {event.symbol for event in sealed if event.board_height == 1}
    industry = {event.symbol for event in sealed if event.industry == "半导体"}
    _, payload, _ = execute(target, "hot_stock_ranking", limit=5)
    hot = {row["symbol"] for row in payload["items"]}
    assert hot == {"000202", "600101", "300303", "688404", "600606"}
    complete = hot & first & industry
    assert complete == {"600101"}
    omissions = {
        "hot": (first & industry, {"600101", "830505"}),
        "first": (hot & industry, {"600101", "688404"}),
        "industry": (hot & first, {"600101", "000202", "300303"}),
    }
    for name, (actual, expected) in omissions.items():
        assert actual == expected, name
        assert complete < actual, name


def test_industry_change_updates_all_affected_manual_oracles():
    from evals.golden.cases import load_cases

    cases = {case.id: case for case in load_cases()}
    assert cases["s10_industry"].turns[0].expect.rows == [["000202", "北辰制造"]]
    assert cases["s27_semiconductor_first"].turns[0].expect.rows == [
        ["600101", "华岳科技"], ["830505", "岭南精工"],
    ]
    assert cases["m10_narrow_prior_set"].turns[0].expect.rows == [
        ["600101", "华岳科技"], ["688404", "青岚芯片"], ["830505", "岭南精工"],
    ]
    assert cases["m10_narrow_prior_set"].turns[1].expect.rows == [
        ["600101", "华岳科技"], ["830505", "岭南精工"],
    ]
    assert cases["s30_three_condition_intersection"].turns[0].expect.rows == [["600101", "华岳科技"]]
