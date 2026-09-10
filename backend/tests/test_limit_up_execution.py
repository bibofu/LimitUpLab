"""The same event query must mean the same thing on every execution path."""
from datetime import date
from unittest.mock import Mock

import pytest

from app.agents.chat import _answer_limit_up_query
from app.agents.query_contract import QUERY_CONTRACT_VERSION
from app.agents.query_contract_eval import (
    QueryContractEvalCase,
    query_contract_eval_report,
    run_query_contract_eval_suite,
)
from app.agents.tool_execution import execute_tool_calls
from app.agents.tool_policy import AgentToolPolicyEngine
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest
from app.services.sample_data import SAMPLE_EVENTS


# Build the AgentToolRegistry fixture used by the surrounding regression scenario.
def event_registry():
    rows = [
        ("600001", "农业甲", "2026-08-31", "农业+机械"),
        ("600002", "农业乙", "2026-09-07", "农业"),
        ("600002", "农业乙", "2026-09-08", "农业+机械"),
        ("600003", "软件甲", "2026-09-09", "软件"),
    ]
    events = [
        SAMPLE_EVENTS[0].model_copy(update={
            "symbol": symbol, "name": name,
            "trade_date": date.fromisoformat(day), "concept": concept,
            "industry": "农业" if symbol != "600003" else "软件",
            "closed_limit": True,
        })
        for symbol, name, day, concept in rows
    ]
    return AgentToolRegistry(
        events, first_board_repository=Mock(), hithink_collector=Mock(),
    )


# Prepare the empty execution fixture or observation used by the surrounding regression scenario.
def empty_execution():
    return {"facts": {}, "tool_results": [], "tool_call_names": [], "references": []}


# Regression scenario: repair and dispatch preserve window grouping and evidence.
@pytest.mark.parametrize("message,days,group,count,unique,trade_date", [
    ("近期农业板块涨停过的股票有哪些", 7, None, 3, 2, None),
    ("近10个交易日农业板块涨停过的股票有哪些", 10, None, 3, 2, None),
    ("近期农业板块涨停过的股票有哪些", 7, None, 3, 2, date(2026, 9, 8)),
    ("今天农业板块涨停的股票有哪些", 1, None, 0, 0, None),
    ("近期哪些板块涨停的股票比较多", 7, "concept", 4, 3, None),
    ("近期哪些行业的涨停股票比较多", 7, "industry", 4, 3, None),
])
def test_repair_and_dispatch_preserve_window_grouping_and_evidence(
    message, days, group, count, unique, trade_date,
):
    tools = event_registry()
    request = AgentChatRequest(session_id="query-parity", message=message, trade_date=trade_date)
    direct = execute_tool_calls(
        [{"name": "limit_up_events", "arguments": {}}], tools, request=request,
    )
    repaired = empty_execution()
    names = AgentToolPolicyEngine(tools).reconcile(request=request, execution=repaired)

    assert names == ["limit_up_events"]
    facts = repaired["facts"]["limit_up_events"]
    assert facts == direct["facts"]["limit_up_events"]
    assert facts["recent_trade_days"] == days
    assert facts["group_by"] == group
    assert facts["matched_count"] == count
    assert facts["unique_stock_count"] == unique
    trace = repaired["tool_results"][0]
    assert trace.input == direct["tool_results"][0].input
    assert trace.input["recent_trade_days"] == trace.input["query_contract"]["recent_trade_days"]
    assert trace.output["policy_repair"]["rule"]
    assert repaired["references"] == direct["references"]
    if group:
        agriculture = next(item for item in facts["sector_summary"] if item["sector_name"] == "农业")
        assert agriculture["unique_stock_count"] == 2
        assert agriculture["limit_up_event_count"] == 3
    else:
        assert all(item["trade_date"] <= "2026-09-08" for item in facts["events"])


# Regression scenario: legacy fallback uses full cross day evidence.
def test_legacy_fallback_uses_full_cross_day_evidence():
    response = _answer_limit_up_query(
        AgentChatRequest(session_id="legacy", message="近期农业板块涨停过的股票有哪些"),
        event_registry(), None, None,
    )
    assert "农业甲(600001)" in response.answer
    assert response.answer.count("农业乙(600002)") == 1
    assert "期间收盘涨停 2 次" in response.answer
    assert response.tool_results[0].input["recent_trade_days"] == 7


# Regression scenario: policy keeps tool failure and profile boundaries.
def test_policy_keeps_tool_failure_and_profile_boundaries():
    tools = event_registry()
    tools.limit_up_events = Mock(side_effect=RuntimeError("event source unavailable"))
    request = AgentChatRequest(session_id="failure", message="近期农业板块涨停过的股票有哪些")
    failed = empty_execution()
    AgentToolPolicyEngine(tools).reconcile(request=request, execution=failed)
    assert "limit_up_events" not in failed["facts"]
    assert failed["tool_results"][0].status == "error"
    assert "event source unavailable" in failed["tool_results"][0].error
    tools.limit_up_events.reset_mock()
    tools.is_enabled = Mock(return_value=False)
    disabled = empty_execution()
    assert AgentToolPolicyEngine(tools).reconcile(request=request, execution=disabled) == []
    tools.limit_up_events.assert_not_called()


# Regression scenario: eval report uses actual contract version even for empty suite.
@pytest.mark.parametrize("cases", [[], [QueryContractEvalCase("version", "今天涨停股票有哪些", {})]])
def test_eval_report_uses_actual_contract_version_even_for_empty_suite(cases):
    report = query_contract_eval_report(run_query_contract_eval_suite(cases))
    assert report["version"] == QUERY_CONTRACT_VERSION
    assert all(item["actual"]["version"] == report["version"] for item in report["results"])
