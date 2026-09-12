"""The same event query must mean the same thing on every execution path."""
from datetime import date
from unittest.mock import Mock

import pytest

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








# Regression scenario: eval report uses actual contract version even for empty suite.
@pytest.mark.parametrize("cases", [[], [QueryContractEvalCase("version", "今天涨停股票有哪些", {})]])
def test_eval_report_uses_actual_contract_version_even_for_empty_suite(cases):
    report = query_contract_eval_report(run_query_contract_eval_suite(cases))
    assert report["version"] == QUERY_CONTRACT_VERSION
    assert all(item["actual"]["version"] == report["version"] for item in report["results"])
