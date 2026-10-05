"""Exercise evaluator fixtures through production validation, without external IO."""

from datetime import date
from inspect import signature
from types import SimpleNamespace
import socket
import sqlite3
import subprocess

import pytest

from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import AgentToolRegistry, TOOL_SCHEMAS, V1_CLOSED_MARKET_TOOL_NAMES
from app.collectors.hithink_finance_collector import HithinkFinanceCollector, HithinkLimitUpFact
from app.models import FirstBoardRatingsResponse, MarketIndexTrendFacts, StockActivityFacts, StockKLineBar, StockKLineFacts, StockNewsFacts, WebSearchFacts
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
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(HithinkFinanceCollector, "_invoke", forbidden)
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
    ("remote_limit_up_pool", {"trade_date": "2026-09-22", "limit": 100}),
    ("stock_activity", {"symbol": "华岳科技"}),
    ("web_search", {"query": "华岳科技 股票"}),
    ("dragon_tiger_list", {"trade_date": "2026-09-22"}),
    ("market_index_trend", {"days": 5, "end_date": "2026-09-22"}),
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


def test_ratings_exclusions_audit_synthetic_eligibility_before_symbol_selection():
    target = registry()
    _, full, _ = execute(target, "first_board_ratings")
    excluded = {row["symbol"]: row for row in full["filtered_out"]}
    assert {symbol: row["excluded_reasons"] for symbol, row in excluded.items()} == {
        "688404": ["非首板", "未配置合成评级分数"],
        "830505": ["未配置合成评级分数"], "600606": ["非首板"],
        "000707": ["收盘未封住"], "300808": ["收盘未封住", "未配置合成评级分数"],
        "600909": ["非首板"],
    }
    assert excluded["830505"]["data_missing"] == ["synthetic_rating_score"]
    assert all(row["included"] is False for row in excluded.values())
    assert full["universe_count"] == len(full["top_candidates"]) + len(excluded)
    _, directed, _ = execute(target, "first_board_ratings", symbols=["000202"])
    assert directed["filtered_out"] == full["filtered_out"]
    assert directed["universe_count"] == 10 and directed["matched_count"] == 1
    assert "600101" not in excluded  # omitted by symbols, not excluded from eligibility
    assert "合成" in full["filter_policy"]
    assert "不是已入池后的再次过滤阶段" in full["field_semantics"]["filtered_out"]
    _, history, _ = execute(target, "first_board_ratings", trade_date="2026-09-18")
    assert history["universe_count"] == 7 and history["matched_count"] == 6
    assert [(row["symbol"], row["excluded_reasons"]) for row in history["filtered_out"]] == [
        ("688404", ["未配置合成评级分数"])]


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
    for key in ("events", "items", "candidates", "bars", "indices", "top_candidates", "results", "recent_limit_up_events"):
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


def test_remote_pool_uses_real_filters_and_dated_event_universe():
    target = registry()
    _, payload, _ = execute(target, "remote_limit_up_pool", board_height=1)
    assert payload["upstream_total"] == 8
    assert [row["symbol"] for row in payload["items"]] == [
        "600101", "000202", "300303", "830505", "000910"]
    _, payload, _ = execute(target, "remote_limit_up_pool", board_height=1, query="人工智能", limit=1)
    assert [row["symbol"] for row in payload["items"]] == ["600101"]
    _, payload, _ = execute(target, "remote_limit_up_pool", trade_date="2026-09-18", board_height=1)
    assert len(payload["items"]) == 7 and payload["trade_date"] == "2026-09-18"
    assert all(row["board_height"] == 1 for row in payload["items"])


@pytest.mark.parametrize("exclude_st", [False, True])
@pytest.mark.parametrize("exclude_new", [False, True])
def test_st_and_new_filters_use_test_only_upstream_rows(exclude_st, exclude_new):
    class BoundaryRegistry(FrozenRegistry):
        def _remote_pool_rows(self, target):
            rows = super()._remote_pool_rows(target)
            for symbol, name, st, new in [("600111", "ST合成材料", True, False),
                                           ("688112", "合成新芯", False, True)]:
                rows.append(HithinkLimitUpFact(
                    symbol=symbol, thscode=self._thscode(symbol), name=name,
                    is_st=st, is_new=new, last_price=None, change_pct=None,
                    limit_up_time="09:45:00", limit_up_reason="仅用于过滤器单测的上游样本",
                    board_height=1, board_height_text="1板", seal_amount=100_000, max_seal_amount=200_000))
            return rows

    target = BoundaryRegistry(registry().case)
    _, payload, _ = execute(target, "remote_limit_up_pool", exclude_st=exclude_st, exclude_new=exclude_new)
    symbols = {row["symbol"] for row in payload["items"]}
    assert ("600111" in symbols) is not exclude_st
    assert ("688112" in symbols) is not exclude_new
    assert payload["upstream_total"] == 10
    assert len(symbols) == 10 - int(exclude_st) - int(exclude_new)


@pytest.mark.parametrize("day", ["2026-09-18", "2026-09-21", "2026-09-22"])
@pytest.mark.parametrize("exclude_st,exclude_new", [(False, False), (True, False), (False, True), (True, True)])
def test_default_market_sources_have_identical_complete_stock_sets(day, exclude_st, exclude_new):
    target = registry()
    _, remote, _ = execute(target, "remote_limit_up_pool", trade_date=day,
                            exclude_st=exclude_st, exclude_new=exclude_new, limit=100)
    _, local, _ = execute(target, "limit_up_events", trade_date=day, event_status="closed", limit=100)
    _, events, _ = execute(target, "market_event_pool", trade_date=day, event_type="limit_up", limit=100)
    remote_set = {row["symbol"] for row in remote["items"]}
    assert remote_set == {row["symbol"] for row in local["events"]}
    assert remote_set == {row["symbol"] for row in events["items"]}
    assert len(remote_set) == remote["upstream_total"] == local["matched_count"] == events["matched_count"]
    assert {"600111", "688112"}.isdisjoint(target._names)
    if day == "2026-09-22":
        _, summary, _ = execute(target, "market_summary")
        assert summary["limit_up_count"] == len(remote_set) == 8


def test_dragon_tiger_keeps_distinct_intervals_and_honors_board_query_date_limit():
    target = registry()
    _, payload, _ = execute(target, "dragon_tiger_list")
    assert payload["stock_count"] == 4 and payload["matched_count"] == 5
    assert {(row["symbol"], row["range_days"]) for row in payload["items"]} == {
        ("600101", 1), ("600101", 3), ("000202", 1), ("688404", 1), ("600606", 1)}
    assert all(row["buy_amount"] - row["sell_amount"] == row["net_buy_amount"] for row in payload["items"])
    _, institution, _ = execute(target, "dragon_tiger_list", board_type="org")
    assert {row["symbol"] for row in institution["items"]} == {"600101", "688404", "600606"}
    _, hot_money, _ = execute(target, "dragon_tiger_list", board_type="hot_money")
    assert {(row["symbol"], row["range_days"]) for row in hot_money["items"]} == {
        ("600101", 3), ("000202", 1), ("688404", 1)}
    _, historical, _ = execute(target, "dragon_tiger_list", trade_date="2026-09-18", query="华岳")
    assert [(row["symbol"], row["range_days"]) for row in historical["items"]] == [("600101", 1)]
    result, limited, state = execute(target, "dragon_tiger_list", query="600101", limit=1)
    assert limited["matched_count"] == 2 and limited["returned_count"] == 1 and limited["source_truncated"]
    store = EvidenceStore()
    key = store.add(tool=result.name, payload=limited, state=state, arguments=result.input)
    assert store.get(key)["result_state"] == "partial"


def test_stock_activity_assembles_the_named_stock_with_production_window_semantics():
    target = registry()
    result, payload, _ = execute(target, "stock_activity", symbol="华岳科技", days=1, news_limit=1)
    StockActivityFacts.model_validate(result.output)
    assert payload["symbol"] == "600101" and payload["data_as_of"] == "2026-09-22"
    assert payload["kline"]["requested_days"] == 20
    assert payload["kline"]["return_10d_pct"] == 1.2
    assert payload["news"]["window_days"] == 1 and len(payload["news"]["items"]) == 1
    # In production, days controls the news window; recent events are the latest five records.
    assert [row["trade_date"] for row in payload["recent_limit_up_events"]] == [
        "2026-09-22", "2026-09-21", "2026-09-18"]
    assert payload["rating_context"]["popularity_rank"] == 2
    _, other, _ = execute(target, "stock_activity", symbol="000202", days=7)
    assert other["symbol"] == "000202" and other["news"]["window_days"] == 7
    assert other["rating_context"]["popularity_rank"] == 1
    _, empty, state = execute(registry("empty"), "stock_activity", symbol="600101")
    assert state == "empty" and empty["kline"] is None
    assert empty["rating_context"] == {} and empty["news"]["items"] == []


def test_web_search_queries_fixed_documents_without_inventing_an_answer():
    target = registry()
    result, payload, _ = execute(target, "web_search", query="  华岳科技   股票  ", limit=2)
    WebSearchFacts.model_validate(result.output)
    assert payload["query"] == "华岳科技 股票" and payload["provider"] == SOURCE
    assert [row["url"] for row in payload["results"]] == [
        "https://example.invalid/golden/600101/2026-09-22",
        "https://example.invalid/golden/600101/2026-09-21"]
    _, historical, _ = execute(target, "web_search", query="华岳科技 2026-09-18 公告")
    assert len(historical["results"]) == 1 and "2026-09-18" in historical["results"][0]["snippet"]
    _, sector, _ = execute(target, "web_search", query="半导体 2026-09-22 新闻", limit=8)
    assert {row["url"].split("/")[-2] for row in sector["results"]} == {"600101", "688404", "830505"}
    _, unknown, state = execute(target, "web_search", query="未收录的不存在公司")
    assert state == "empty" and unknown["results"] == []
    _, missing_date, state = execute(target, "web_search", query="华岳科技 2026-09-20 公告")
    assert state == "empty" and missing_date["results"] == []


@pytest.mark.parametrize("name,arguments,collection", [
    ("remote_limit_up_pool", {}, "items"), ("dragon_tiger_list", {}, "items"),
    ("stock_activity", {"symbol": "600101"}, "recent_limit_up_events"),
    ("web_search", {"query": "华岳科技 股票"}, "results"),
])
@pytest.mark.parametrize("variant", ["partial", "truncated", "stale"])
def test_added_tools_propagate_source_failure_metadata(name, arguments, collection, variant):
    result, payload, state = execute(registry(variant), name, **arguments)
    assert state == "partial" and payload["source"] == SOURCE
    if variant == "partial":
        assert payload["data_missing"] and payload[collection]
    elif variant == "truncated":
        assert payload["source_truncated"] and len(payload[collection]) == 2
    else:
        assert payload["data_fresh"] is False
        if "trade_date" in payload:
            assert payload["trade_date"] == "2026-09-21"
        else:
            assert payload["fetched_at"].startswith("2026-09-21")
        if name == "stock_activity":
            assert payload["data_as_of"] == "2026-09-21"
            assert all(row["trade_date"] <= "2026-09-21" for row in payload[collection])
        if name == "web_search":
            assert all("2026-09-22" not in row["snippet"] for row in payload[collection])


@pytest.mark.parametrize("name", ["remote_limit_up_pool", "dragon_tiger_list"])
def test_added_dated_tools_never_relabel_stale_sources(name):
    with pytest.raises(ValueError, match="requires 2026-09-22"):
        execute(registry("stale"), name, trade_date="2026-09-22")


def test_web_and_activity_injection_stays_in_untrusted_source_fields():
    _, web, _ = execute(registry("injection"), "web_search", query="华岳科技 股票")
    assert "SYNTHETIC_INJECTION_CANARY" in web["results"][0]["snippet"]
    _, activity, _ = execute(registry("injection"), "stock_activity", symbol="600101")
    assert "SYNTHETIC_INJECTION_CANARY" in activity["news"]["items"][0]["summary"]
    assert activity["kline"]["return_10d_pct"] == 1.2


@pytest.mark.parametrize("symbol,day,expected_return,expected_close,trend", [
    ("600101", "2026-09-18", .8, 100.8, "rising"),
    ("600101", "2026-09-21", 1.0, 101.0, "rising"),
    ("600101", "2026-09-22", 1.2, 101.2, "rising"),
    ("000202", "2026-09-22", 3.6, 113.96, "rising"),
    ("300303", "2026-09-22", -2.4, 117.12, "falling"),
])
def test_kline_discriminates_object_and_date_with_independent_scalar_labels(symbol, day, expected_return, expected_close, trend):
    _, payload, _ = execute(registry(), "stock_kline", symbol=symbol, end_date=day, days=20)
    assert payload["return_10d_pct"] == expected_return
    assert payload["latest_close"] == expected_close and payload["trend"] == trend
    assert payload["data_as_of"] == day and payload["bars"][-1]["trade_date"] == day
    if trend == "rising":
        assert payload["latest_close"] >= payload["ma5"] >= payload["ma10"] >= payload["ma20"]
    else:
        assert payload["latest_close"] <= payload["ma5"] <= payload["ma10"] <= payload["ma20"]
        assert payload["max_drawdown_pct"] == -2.4


def test_historical_kline_queries_preserve_overlapping_observations_and_interval_metrics():
    target = registry()
    _, full, _ = execute(target, "stock_kline", symbol="300303", days=60)
    _, short, _ = execute(target, "stock_kline", symbol="300303", end_date="2026-09-21", days=5)
    dated = {bar["trade_date"]: bar for bar in full["bars"]}
    assert all(bar == dated[bar["trade_date"]] for bar in short["bars"])
    closes = [bar["close"] for bar in full["bars"]]
    for periods in (5, 10, 20):
        assert full[f"return_{periods}d_pct"] == round((closes[-1] / closes[-1 - periods] - 1) * 100, 3)
    assert short["return_10d_pct"] == -2.0  # may use more history than the five displayed bars


@pytest.mark.parametrize("symbol", ["600101", "000202", "300303"])
@pytest.mark.parametrize("day", ["2026-09-18", "2026-09-21", "2026-09-22"])
@pytest.mark.parametrize("days", [5, 20, 60])
def test_kline_public_metrics_match_production_service_on_identical_bars(monkeypatch, symbol, day, days):
    from app.services import stock_kline

    target = registry()
    _, history, _ = execute(target, "stock_kline", symbol=symbol, end_date=day, days=60)
    bars = [StockKLineBar.model_validate(bar) for bar in history["bars"]]
    # Only storage/loading is replaced. The complete production metric builder
    # independently derives returns, MA precision, trend, volume and drawdown.
    monkeypatch.setattr(stock_kline, "load_stock_kline_bars", lambda **kwargs: bars[-kwargs["days"]:])
    monkeypatch.setattr(stock_kline, "_cached_bars", lambda *args: bars)
    expected = stock_kline.build_stock_kline_facts(
        symbol=symbol, days=days, end_date=date.fromisoformat(day), repository=SimpleNamespace())
    _, payload, _ = execute(target, "stock_kline", symbol=symbol, end_date=day, days=days)
    assert StockKLineFacts.model_validate(payload) == expected


def test_kline_precision_and_drawdown_have_independent_reference_values():
    _, rising, _ = execute(registry(), "stock_kline", symbol="600101", days=20)
    assert {key: rising[key] for key in ("return_5d_pct", "return_10d_pct", "ma5", "ma10", "ma20", "max_drawdown_pct")} == {
        "return_5d_pct": .697, "return_10d_pct": 1.2, "ma5": 100.86,
        "ma10": 100.58, "ma20": 100.29, "max_drawdown_pct": 0.0,
    }
    _, falling, _ = execute(registry(), "stock_kline", symbol="300303", days=5)
    assert falling["return_5d_pct"] == -1.414
    assert falling["max_drawdown_pct"] == -1.215  # displayed five bars, not the complete history


def test_index_trend_matches_native_model_and_five_closes_span_four_intervals():
    result, payload, _ = execute(registry(), "market_index_trend")
    MarketIndexTrendFacts.model_validate(result.output)
    assert payload["requested_days"] == 5 and payload["data_as_of"] == "2026-09-22"
    assert [(row["symbol"], row["return_pct"], row["max_drawdown_pct"], row["positive_days"], row["negative_days"])
            for row in payload["indices"]] == [
        ("000001.SH", 2.0, -.5, 3, 1), ("399001.SZ", -2.0, -2.0, 1, 3),
        ("399006.SZ", 1.0, -1.49, 2, 2),
    ]
    assert [row["close"] for row in payload["indices"][0]["points"]] == [3200, 3232, 3216, 3248, 3264]
    for row in payload["indices"]:
        assert row["start_date"] == "2026-09-16" and row["end_date"] == "2026-09-22"
        assert row["points"][0]["change_pct"] is None
        assert len(row["points"]) == 5 and row["source"] == SOURCE
    _, historical, _ = execute(registry(), "market_index_trend", end_date="2026-09-18", days=2)
    assert [row["return_pct"] for row in historical["indices"]] == [-.5, .51, -.5]
    assert all([point["trade_date"] for point in row["points"]] == ["2026-09-17", "2026-09-18"]
               for row in historical["indices"])


@pytest.mark.parametrize("day", ["2026-09-18", "2026-09-21", "2026-09-22"])
@pytest.mark.parametrize("days", [2, 5, 20])
def test_index_metrics_match_production_normalizer_on_identical_points(day, days):
    from app.collectors.market_index_collector import _trend_from_rows

    _, payload, _ = execute(registry(), "market_index_trend", end_date=day, days=days)
    facts = MarketIndexTrendFacts.model_validate(payload)
    assert facts.requested_days == days and facts.requested_end_date == date.fromisoformat(day)
    for item in facts.indices:
        expected = _trend_from_rows(
            name=item.name, display_symbol=item.symbol,
            rows=[{"date": point.trade_date, "close": point.close} for point in item.points],
            requested_end_date=date.fromisoformat(day), days=days, source=SOURCE)
        assert item == expected


@pytest.mark.parametrize("variant", ["partial", "truncated", "stale", "empty"])
def test_index_faults_preserve_coverage_and_actual_dates(variant):
    result, payload, state = execute(registry(variant), "market_index_trend")
    MarketIndexTrendFacts.model_validate(result.output)
    assert state == ("empty" if variant == "empty" else "partial")
    if variant == "partial":
        assert payload["data_missing"] and len(payload["indices"]) == 3
    if variant == "truncated":
        assert payload["source_truncated"] and len(payload["indices"]) == 2
        assert payload["matched_count"] == 3
    if variant == "stale":
        assert payload["requested_end_date"] == "2026-09-22" and payload["data_as_of"] == "2026-09-21"
        assert payload["data_fresh"] is False
        assert all(row["end_date"] == "2026-09-21" for row in payload["indices"])
        with pytest.raises(ValueError, match="requires 2026-09-22"):
            execute(registry(variant), "market_index_trend", end_date="2026-09-22")
    if variant == "empty":
        assert payload["indices"] == []


def test_default_profile_reuses_production_allowlist_and_blocks_deferred_tools(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_AGENT_PROFILE", "v1_close_review")
    assert registry().profile == "extended"  # stable default, independent of shell configuration
    target = FrozenRegistry(registry().case, profile="v1")
    assert target.profile == "v1_close_review" and target.enabled_tool_names == V1_CLOSED_MARKET_TOOL_NAMES
    assert {schema.name for schema in target.schemas()} == V1_CLOSED_MARKET_TOOL_NAMES
    _, payload, _ = execute(target, "market_index_trend")
    assert len(payload["indices"]) == 3
    for name, arguments in [("remote_limit_up_pool", {}), ("web_search", {"query": "华岳科技"})]:
        assert not target.is_enabled(name)
        with pytest.raises(ValueError, match="not registered for this research profile"):
            execute(target, name, **arguments)
    with pytest.raises(FixtureCoverageGap, match="Synthetic fixture does not implement"):
        execute(target, "sector_performance")
    assert target.unsupported_tools == ["sector_performance"]
    with pytest.raises(ValueError, match="must be v1_close_review or extended"):
        FrozenRegistry(registry().case, profile="unknown")
