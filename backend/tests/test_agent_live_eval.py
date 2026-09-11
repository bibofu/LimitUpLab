from pathlib import Path

from app.agents.chat_live_eval import evaluate_live_trial
from app.agents.chat_live_eval_runner import DATASET_PATH, load_live_eval_dataset
from app.models import AgentChatResponse, AgentToolOutcome, AgentToolPolicyAudit, AgentToolTrace


def test_live_dataset_has_exact_high_value_distribution() -> None:
    dataset = load_live_eval_dataset(DATASET_PATH)
    assert len(dataset.cases) == 36
    assert sum(case.category == "replan" for case in dataset.cases) == 8
    assert sum(len(case.turns) > 1 for case in dataset.cases) == 6
    assert all(case.expected.max_tool_calls <= 8 for case in dataset.cases)


def test_argument_dependency_uses_observed_source_values() -> None:
    case = next(
        case for case in load_live_eval_dataset().cases
        if case.case_id == "LIVE-REPLAN-001"
    )
    response = _response(
        [
            AgentToolTrace(
                name="llm_tool_planner",
                input={"capabilities": ["first_board_rating", "stock_trend"]},
                summary="plan",
            ),
            _trace(
                "first_board_ratings",
                {},
                {"top_candidates": [{"symbol": "300750"}, {"symbol": "600000"}]},
            ),
            _trace("stock_kline", {"symbol": "300750"}, {"symbol": "300750"}),
        ],
        answer="300750 最近5日走势已检查。",
    )
    result = evaluate_live_trial(
        case, [response], llm_usage={"call_count": 2}, latency_ms=10
    )
    assert not any("argument dependency failed" in item for item in result["failure_reasons"])

    response.tool_results[-1].input["symbol"] = "000001"
    failed = evaluate_live_trial(
        case, [response], llm_usage={"call_count": 2}, latency_ms=10
    )
    assert any("argument dependency failed" in item for item in failed["failure_reasons"])


def test_runtime_condition_requires_fallback_only_when_observed() -> None:
    case = next(
        case for case in load_live_eval_dataset().cases
        if case.case_id == "LIVE-REPLAN-004"
    )
    response = _response(
        [
            AgentToolTrace(
                name="llm_tool_planner",
                input={"capabilities": ["stock_news"]},
                summary="plan",
            ),
            _trace("stock_news", {"symbol": "300750"}, {}, state="empty"),
        ],
        answer="没有查到相关新闻。",
    )
    result = evaluate_live_trial(
        case, [response], llm_usage={"call_count": 2}, latency_ms=10
    )
    assert "conditional tools missing: ['stock_activity', 'stock_kline']" in result["failure_reasons"]


def _trace(name: str, arguments: dict, output: dict, state: str = "ok") -> AgentToolTrace:
    return AgentToolTrace(
        name=name,
        input=arguments,
        output=output,
        summary=name,
        status="error" if state == "error" else "success",
        result=AgentToolOutcome(status=state, payload=output),
    )


def _response(traces: list[AgentToolTrace], *, answer: str) -> AgentChatResponse:
    tools = [trace.name for trace in traces if trace.name != "llm_tool_planner"]
    return AgentChatResponse(
        session_id="live-eval-test",
        intent="test",
        answer=answer,
        tool_calls=["llm_tool_planner", *tools],
        tool_results=traces,
        tool_policy=AgentToolPolicyAudit(final_tool_calls=tools),
        generated_by="test",
    )
