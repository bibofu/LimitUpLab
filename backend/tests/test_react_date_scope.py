"""Offline date-contract regressions; no market data or real model is used."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.agents.react_runtime.date_scope import DateScope
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.runtime import Run
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest
from app.services.prompt_security import review_input


def reviewed(message, scope):
    class Provider:
        def generate_messages(self, messages, tools, **kwargs):
            schema = tools[0]["function"]["parameters"]
            assert "date_scope" in schema["required"]
            args = {"decision": "allow", "signals": [], "reason": "scripted semantic result",
                    "request_kind": "research", "context_mode": "standalone", "date_scope": scope}
            return AIMessage(content="", tool_calls=[{
                "name": "submit_input_security_review", "id": "review", "args": args}])
    return review_input(Provider(), message=message, anchor_date=date(2026, 10, 15), timeout_seconds=5)


@pytest.mark.parametrize("message", [
    "不要查询今天，查询2026-09-18的热门股票。",
    "当前帮我查2026年9月18日的热门股票。",
    "不要2026-09-17，改查2026-09-18。",
    "日期沿用上次的9月18日，其他条件不变。",
])
def test_semantic_date_wins_over_negation_current_words_and_first_date(message):
    result = reviewed(message, {"mode": "dates", "dates": ["2026-09-18"]})
    assert result.requested_date == date(2026, 9, 18)
    assert result.time_scope == "explicit"


def test_relative_current_date_is_resolved_once_by_semantic_review():
    result = reviewed("今天的热门股票", {"mode": "dates", "dates": ["2026-10-15"]})
    assert result.requested_date == date(2026, 10, 15)
    assert result.time_scope == "current"


def test_comparison_dates_are_not_collapsed_to_the_first_date():
    result = reviewed("比较9月18日和9月21日。", {
        "mode": "dates", "dates": ["2026-09-18", "2026-09-21"]})
    assert result.requested_date is None
    assert result.date_scope.dates == [date(2026, 9, 18), date(2026, 9, 21)]


def test_missing_semantic_date_contract_fails_closed():
    class Provider:
        def generate_messages(self, *args, **kwargs):
            return AIMessage(content="", tool_calls=[{
                "name": "submit_input_security_review", "id": "review", "args": {
                    "decision": "allow", "signals": [], "reason": "omitted contract",
                    "request_kind": "research", "context_mode": "standalone"}}])
    with pytest.raises(ValueError, match="date_scope"):
        review_input(Provider(), message="不要今天，查2026-09-18。", anchor_date=date(2026, 10, 15), timeout_seconds=5)


def target(scope, **methods):
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True, **methods)
    gateway = ToolGateway(registry, EvidenceStore())
    gateway.date_scope = DateScope.model_validate(scope)
    return gateway


SINGLE = {"mode": "dates", "dates": ["2026-09-18"]}
COMPARE = {"mode": "dates", "dates": ["2026-09-18", "2026-09-21"]}
RANGE = {"mode": "range", "start_date": "2026-09-01", "end_date": "2026-09-30"}


@pytest.mark.parametrize("args", [{}, {"trade_date": None}])
def test_single_date_omission_and_null_use_the_semantic_date(args):
    assert target(SINGLE).validate({"name": "dragon_tiger_list", "args": args})["trade_date"] == "2026-09-18"


def test_conflicting_single_tool_date_is_rejected():
    with pytest.raises(ValueError, match="conflicts"):
        target(SINGLE).validate({"name": "dragon_tiger_list", "args": {"trade_date": "2026-09-17"}})


@pytest.mark.parametrize("args", [{}, {"requested_as_of": None}, {"requested_as_of": "2026-10-15"}])
def test_historical_request_cannot_bypass_current_only_capability(args, monkeypatch):
    monkeypatch.setattr("app.agents.react_runtime.tools.current_query_reference_date", lambda: date(2026, 10, 15))
    with pytest.raises(ValueError, match="historical scope|conflicts"):
        target(SINGLE).validate({"name": "hot_stock_ranking", "args": args})


@pytest.mark.parametrize("day", ["2026-09-18", "2026-09-21"])
def test_comparison_calls_can_choose_each_requested_date(day):
    assert target(COMPARE).validate({"name": "dragon_tiger_list", "args": {"trade_date": day}})["trade_date"] == day


@pytest.mark.parametrize("day", [None, "2026-09-19"])
def test_comparison_calls_cannot_guess_or_choose_an_unrequested_date(day):
    with pytest.raises(ValueError, match="comparison dates"):
        target(COMPARE).validate({"name": "dragon_tiger_list", "args": {"trade_date": day}})


@pytest.mark.parametrize("start,end", [
    ("2026-09-01", "2026-09-30"), ("2026-09-04", "2026-09-11"),
])
def test_range_allows_full_period_and_subperiods(start, end):
    result = target(RANGE).validate({"name": "rating_backtest", "args": {"start_date": start, "end_date": end}})
    assert (result["start_date"], result["end_date"]) == (start, end)


def test_range_fills_endpoints_but_does_not_guess_a_day_for_a_daily_tool():
    result = target(RANGE).validate({"name": "rating_backtest", "args": {}})
    assert (result["start_date"], result["end_date"]) == ("2026-09-01", "2026-09-30")
    with pytest.raises(ValueError, match="explicit date"):
        target(RANGE).validate({"name": "dragon_tiger_list", "args": {}})


@pytest.mark.parametrize("scope,end", [(SINGLE, "2026-09-18"), (RANGE, "2026-09-11")])
def test_kline_lookback_is_preserved_and_can_precede_the_requested_observation(scope, end):
    result = target(scope).validate({"name": "stock_kline", "args": {
        "symbol": "600001", "end_date": end, "days": 60}})
    assert result["days"] == 60 and result["end_date"] == end


def test_single_asof_preserves_an_earlier_post_limit_anchor():
    result = target(SINGLE).validate({"name": "post_limit_path", "args": {
        "symbol": "600001", "anchor_date": "2026-09-10", "data_as_of": None}})
    assert result["anchor_date"] == "2026-09-10" and result["data_as_of"] == "2026-09-18"


def test_event_date_is_not_mistaken_for_the_following_path_cutoff():
    scope = {**SINGLE, "basis": "event"}
    gateway = target(scope)
    result = gateway.validate({"name": "post_limit_path", "args": {"symbol": "600001"}})
    assert result["anchor_date"] == "2026-09-18"
    assert "data_as_of" not in result
    kline = gateway.validate({"name": "stock_kline", "args": {
        "symbol": "600001", "end_date": "2026-09-28", "days": 10}})
    assert kline["end_date"] == "2026-09-28" and kline["days"] == 10


@pytest.mark.parametrize("scope,anchor", [
    ({**COMPARE, "basis": "event"}, "2026-09-21"),
    ({**RANGE, "basis": "event"}, "2026-09-11"),
])
def test_event_comparison_and_range_keep_each_anchor_separate_from_observation(scope, anchor):
    result = target(scope).validate({"name": "post_limit_path", "args": {
        "symbol": "600001", "anchor_date": anchor, "data_as_of": "2026-10-05"}})
    assert result["anchor_date"] == anchor and result["data_as_of"] == "2026-10-05"


def test_explicit_event_observation_cutoff_is_enforced_without_guessing_trading_days():
    gateway = target({**SINGLE, "basis": "event", "observation_cutoff": "2026-09-30"})
    result = gateway.validate({"name": "post_limit_path", "args": {"symbol": "600001", "data_as_of": None}})
    assert result["anchor_date"] == "2026-09-18" and result["data_as_of"] == "2026-09-30"
    with pytest.raises(ValueError, match="conflicts"):
        gateway.validate({"name": "post_limit_path", "args": {"symbol": "600001", "data_as_of": "2026-09-18"}})
    kline = gateway.validate({"name": "stock_kline", "args": {"symbol": "600001", "days": 10}})
    assert kline["end_date"] == "2026-09-30"


def test_tool_without_event_filter_cannot_claim_to_filter_requested_cohort():
    with pytest.raises(ValueError, match="cannot constrain"):
        target({**SINGLE, "basis": "event"}).validate({"name": "post_limit_statistics", "args": {}})


def test_event_range_review_can_query_subcohorts():
    result = target({**RANGE, "basis": "event"}).validate({"name": "rating_backtest", "args": {
        "start_date": "2026-09-04", "end_date": "2026-09-11"}})
    assert result["start_date"] == "2026-09-04" and result["end_date"] == "2026-09-11"


@pytest.mark.parametrize("args", [
    {"start_date": "2026-08-31", "end_date": "2026-09-30"},
    {"start_date": "2026-09-01", "end_date": "2026-10-01"},
    {"start_date": "2026-09-21", "end_date": "2026-09-18"},
])
def test_invalid_or_out_of_range_windows_are_rejected(args):
    with pytest.raises(ValueError):
        target(RANGE).validate({"name": "rating_backtest", "args": args})


@pytest.mark.parametrize("field", ["data_as_of", "trade_date"])
def test_current_tool_result_date_must_match_asof(field, monkeypatch):
    monkeypatch.setattr("app.agents.react_runtime.tools.current_query_reference_date", lambda: date(2026, 9, 18))
    gateway = target(SINGLE, hot_stock_ranking=lambda **_: ToolResult(
        name="hot_stock_ranking", input={}, output={field: "2026-09-17", "items": []}, summary="synthetic stale result"))
    args = gateway.validate({"name": "hot_stock_ranking", "args": {}})
    with pytest.raises(ValueError, match="requires 2026-09-18"):
        gateway.execute("hot_stock_ranking", args)


def test_mixed_current_news_and_previous_close_keep_distinct_dates(monkeypatch):
    monkeypatch.setattr("app.agents.react_runtime.tools.current_query_reference_date", lambda: date(2026, 9, 18))
    payload = {"data_as_of": "2026-09-17", "news": {"fetched_at": "2026-09-18T08:00:00Z"}}
    gateway = target(SINGLE, stock_activity=lambda **_: ToolResult(
        name="stock_activity", input={}, output=payload, summary="previous close plus current news"))
    args = gateway.validate({"name": "stock_activity", "args": {"symbol": "600001"}})
    assert args["requested_as_of"] == "2026-09-18"
    assert gateway.execute("stock_activity", args)[1] == payload


def test_rating_event_date_is_not_confused_with_prediction_data_cutoff():
    payload = {"trade_date": "2026-09-18", "data_as_of": "2026-09-17", "candidates": []}
    gateway = target(SINGLE, first_board_ratings=lambda **_: ToolResult(
        name="first_board_ratings", input={}, output=payload, summary="dated prediction fixture"))
    args = gateway.validate({"name": "first_board_ratings", "args": {}})
    result = gateway.execute("first_board_ratings", args)[1]
    assert result["trade_date"] == "2026-09-18" and result["data_as_of"] == "2026-09-17"


def test_sector_snapshot_alias_still_checks_the_actual_observation_day():
    gateway = target(SINGLE, sector_performance=lambda **_: ToolResult(
        name="sector_performance", input={}, output={"data_as_of": "2026-09-17", "top_sectors": []}, summary="stale sector snapshot"))
    args = gateway.validate({"name": "sector_performance", "args": {}})
    with pytest.raises(ValueError, match="requires 2026-09-18"):
        gateway.execute("sector_performance", args)


def test_unspecified_scope_keeps_normal_tool_defaults():
    assert target({"mode": "unspecified"}).validate({"name": "stock_kline", "args": {"symbol": "600001"}}) == {"symbol": "600001"}


@pytest.mark.parametrize("required", [None, date(2026, 9, 18)])
def test_checkpoint_preserves_only_the_single_required_observation_date(required):
    request = AgentChatRequest(session_id="date-checkpoint", message="继续研究。")
    registry = target(SINGLE).registry
    original = Run(request, registry, object(), [], None)
    original.gateway.required_date = required
    snapshots = []
    original.control = SimpleNamespace(save=snapshots.append)
    original.save({"messages": [], "done": False}, "agent")
    assert "date_scope" not in snapshots[0]["runtime"]
    assert snapshots[0]["runtime"]["required_date"] == (required.isoformat() if required else None)
    restored = Run(request, registry, object(), [], None)
    restored.control = SimpleNamespace(row={"checkpoint_json": json.dumps(snapshots[0])})
    assert restored.restore()["resume_node"] == "agent"
    assert restored.gateway.required_date == required


@pytest.mark.parametrize("scope,expected", [
    (SINGLE, date(2026, 9, 18)), (COMPARE, None), (RANGE, None),
    ({**SINGLE, "basis": "event", "observation_cutoff": "2026-09-30"}, None),
])
def test_old_checkpoint_scope_does_not_restore_multidate_or_event_enforcement(scope, expected):
    request = AgentChatRequest(session_id="legacy-date", message="继续研究。")
    registry = target(scope).registry
    snapshot = {"runtime": {"date_scope": scope}, "state": {"messages": [], "resume_node": "agent"}}
    restored = Run(request, registry, object(), [], None)
    restored.control = SimpleNamespace(row={"checkpoint_json": json.dumps(snapshot)})
    restored.restore()
    assert restored.gateway.required_date == expected
    assert restored.gateway.date_scope.mode == ("dates" if expected else "unspecified")


@pytest.mark.parametrize("scope", [
    {"mode": "dates", "dates": []},
    {"mode": "dates", "dates": ["2026-09-18", "2026-09-18"]},
    {"mode": "range", "start_date": "2026-09-18"},
    {"mode": "range", "start_date": "2026-09-18", "end_date": "2026-09-17"},
    {"mode": "unspecified", "dates": ["2026-09-18"]},
    {**SINGLE, "basis": "event", "observation_cutoff": "2026-09-17"},
    {**SINGLE, "observation_cutoff": "2026-09-30"},
])
def test_contradictory_semantic_scopes_fail_closed(scope):
    with pytest.raises(ValueError):
        reviewed("查询", scope)
