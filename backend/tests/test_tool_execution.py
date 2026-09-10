"""Behavioral boundaries for the ordered tool dispatcher."""
from datetime import date
from unittest.mock import Mock

from app.agents.tool_execution import HANDLERS, execute_tool_calls
from app.agents.tools import AgentToolRegistry, TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest, FirstBoardRatingsResponse


# Prepare the registry fixture or observation used by the surrounding regression scenario.
def registry():
    tools = Mock(spec=AgentToolRegistry)
    tools.profile = "v1_close_review"
    tools.events = []
    tools.is_enabled.return_value = True
    return tools


# Build the AgentChatRequest fixture used by the surrounding regression scenario.
def request(message="研究评分", **kwargs):
    return AgentChatRequest(session_id="dispatch-test", message=message, **kwargs)


# Prepare the call fixture or observation used by the surrounding regression scenario.
def call(name, **arguments):
    return {"name": name, "arguments": arguments}


# Build the ToolResult fixture used by the surrounding regression scenario.
def ratings_result():
    response = FirstBoardRatingsResponse(
        trade_date=date(2026, 5, 15), candidates=[], filtered_out=[],
        universe_count=0, generated_by="test",
    )
    return ToolResult("first_board_ratings", {}, response, "ratings")


# Regression scenario: every advertised tool has an execution handler.
def test_every_advertised_tool_has_an_execution_handler():
    assert set(HANDLERS) == {schema.name for schema in TOOL_SCHEMAS}


# Regression scenario: profile check precedes handler and cannot be bypassed.
def test_profile_check_precedes_handler_and_cannot_be_bypassed():
    tools = registry()
    tools.is_enabled.return_value = False
    result = execute_tool_calls([call("first_board_ratings")], tools, request=request())
    tools.first_board_ratings.assert_not_called()
    assert result["tool_call_names"] == ["first_board_ratings"]
    assert result["tool_results"][0].status == "error"
    assert "first_board_ratings_error" in result["facts"]


# Regression scenario: unknown tool is rejected even if profile accepts it.
def test_unknown_tool_is_rejected_even_if_profile_accepts_it():
    result = execute_tool_calls([call("invented_tool")], registry(), request=request())
    assert result["tool_results"][0].status == "error"
    assert "invented_tool_error" in result["facts"]


# Regression scenario: filter reuses ratings in order but not across requests.
def test_filter_reuses_ratings_in_order_but_not_across_requests():
    tools = registry()
    tools.first_board_ratings.return_value = ratings_result()
    result = execute_tool_calls(
        [call("first_board_ratings"), call("first_board_filter", query="医药")],
        tools, request=request(),
    )
    tools.first_board_ratings.assert_called_once_with(trade_date=None)
    assert result["tool_call_names"] == ["first_board_ratings", "first_board_filter"]
    assert result["facts"]["first_board_filter"]["matched_count"] == 0
    second = execute_tool_calls([call("first_board_filter", query="医药")], tools, request=request())
    assert tools.first_board_ratings.call_count == 2
    assert second["facts"] is not result["facts"]


# Regression scenario: tool failure retains error and allows later evidence.
def test_tool_failure_retains_error_and_allows_later_evidence():
    tools = registry()
    tools.resolve_stock_identity.return_value = ("600001", "测试公司")
    tools.stock_news.side_effect = RuntimeError("news source unavailable")
    tools.scoring_policy_status.return_value = ToolResult(
        "scoring_policy_status", {}, {"champion": {"version": "test-v1"}}, "policy",
        trace_output={"champion": {"version": "test-v1"}},
    )
    result = execute_tool_calls(
        [call("stock_news", symbol="600001"), call("scoring_policy_status")],
        tools, request=request(),
    )
    assert result["facts"]["stock_news_error"] == "news source unavailable"
    assert result["facts"]["scoring_policy_status"]["champion"]["version"] == "test-v1"
    assert [trace.status for trace in result["tool_results"]] == ["error", "success"]


# Regression scenario: extended remote pool honors exhaustive request.
def test_extended_remote_pool_honors_exhaustive_request():
    tools = registry()
    tools.profile = "extended"
    tools.remote_limit_up_pool.return_value = ToolResult(
        "remote_limit_up_pool", {}, {"trade_date": "2026-05-15", "items": []}, "pool",
    )
    result = execute_tool_calls(
        [call("remote_limit_up_pool")], tools,
        request=request("列出全部涨停股票", trade_date=date(2026, 5, 15)),
    )
    assert "remote_limit_up_pool_error" not in result["facts"]
    assert tools.remote_limit_up_pool.call_args.kwargs["limit"] == 100
