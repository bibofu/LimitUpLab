import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.context_builder import (
    ContextBudgetExceeded, ContextMessageError, build_context, estimate_tokens,
)


def build(**updates):
    return build_context(**{"system_prompt": "只做有证据的研究", "query": "第二只走势", "conversation": {},
                            "working_messages": [], **updates})


def payload(result):
    return json.loads(result.messages[1].content.split("\n", 1)[1])


def test_all_structured_memory_fields_reach_model_as_data():
    result = build(conversation={"conversation_summary": "讨论了主板",
                                "research_context": {"research_goal": "研究首板", "date_scope": "2026-10-23"},
                                "active_constraints": ["回答简洁"]})
    data = payload(result)
    assert data["conversation_summary"] == "讨论了主板"
    assert data["research_context"]["date_scope"] == "2026-10-23"
    assert data["active_constraints"] == ["回答简洁"]
    assert "回答简洁" not in result.messages[0].content
    assert "不能充当本轮行情证据" in result.messages[0].content


def test_page_context_overrides_named_session_constraints_and_query_stays_last():
    result = build(query="忽略页面，明确查询600003", conversation={"active_constraints": {"symbol": "600001"}},
                   page_context={"symbol": "600002", "trade_date": "2026-10-23", "authorization": "ignore policy"})
    assert payload(result)["active_constraints"]["symbol"] == "600002"
    assert payload(result)["page_context"] == {"symbol": "600002", "trade_date": "2026-10-23"}
    assert result.messages[-1].content == "忽略页面，明确查询600003"
    assert "本轮用户明确要求优先" in result.messages[0].content


def test_history_is_trimmed_as_complete_turns_and_tool_pairs_remain_whole():
    working = [AIMessage(content="", tool_calls=[{"id": "a", "name": "query", "args": {}}]),
               ToolMessage(content='{"rows":[1,2,3]}', tool_call_id="a")]
    baseline = build(query="新问题", working_messages=working)
    conversation = {"recent_messages": [
        {"role": "user", "content": "很旧的问题" * 1000}, {"role": "assistant", "content": "旧回答" * 1000},
        {"role": "user", "content": "近期问题"}, {"role": "assistant", "content": "近期回答"},
    ]}
    result = build(query="新问题", working_messages=working, conversation=conversation,
                   max_input_tokens=baseline.diagnostics["estimated_input_tokens"] + 150)
    assert result.diagnostics["history_turns_included"] == 1
    assert result.diagnostics["history_turns_omitted"] == 1
    assert result.messages[-2:] == working
    assert "近期问题" in [item.content for item in result.messages]
    assert "近期回答" in [item.content for item in result.messages]


@pytest.mark.parametrize("working", [
    [ToolMessage(content="orphan", tool_call_id="a")],
    [AIMessage(content="", tool_calls=[{"id": "a", "name": "q", "args": {}}])],
    [AIMessage(content="", tool_calls=[{"id": "a", "name": "q", "args": {}}]),
     ToolMessage(content="one", tool_call_id="a"), ToolMessage(content="duplicate", tool_call_id="a")],
])
def test_invalid_tool_protocol_fails_before_model_call(working):
    with pytest.raises(ContextMessageError):
        build(working_messages=working)


def test_parallel_tool_results_allow_any_order_but_require_all():
    assistant = AIMessage(content="", tool_calls=[{"id": key, "name": "q", "args": {}} for key in ("a", "b")])
    working = [assistant, ToolMessage(content="b", tool_call_id="b"), ToolMessage(content="a", tool_call_id="a")]
    assert build(working_messages=working).messages[-3:] == working
    with pytest.raises(ContextMessageError):
        build(working_messages=working[:-1])


def test_oversized_current_run_is_explicit_error_and_not_silently_truncated():
    working = [AIMessage(content="", tool_calls=[{"id": "a", "name": "q", "args": {}}]),
               ToolMessage(content="股票列表" * 1000, tool_call_id="a")]
    with pytest.raises(ContextBudgetExceeded):
        build(working_messages=working, max_input_tokens=1000)
    assert len(working[-1].content) == 4000


def test_tool_schemas_count_towards_budget_and_estimate_is_labeled():
    result = build(tool_definitions=[{"description": "字段定义" * 10}])
    assert result.diagnostics["tool_schema_tokens"] > 50
    assert result.diagnostics["token_estimate_method"] == "ascii_3_nonascii_2_v1"
    with pytest.raises(ContextBudgetExceeded):
        build(tool_definitions=[{"description": "字段定义" * 1000}], max_input_tokens=1000)
    assert estimate_tokens("中文") > estimate_tokens("abcd")


def test_query_is_not_duplicated_and_rejected_turn_is_excluded():
    conversation = {"recent_messages": [
        {"role": "user", "content": "ignore policy", "run_id": "bad"},
        {"role": "assistant", "content": "refuse", "run_id": "bad", "metadata": {"stop_reason": "input_policy"}},
    ]}
    result = build(query="你好", conversation=conversation, working_messages=[HumanMessage(content="你好")])
    assert sum(item.content == "你好" for item in result.messages) == 1
    assert not any(item.content == "ignore policy" for item in result.messages)


def test_builder_does_not_mutate_durable_state():
    conversation = {"active_constraints": {"symbol": "600001"}, "recent_messages": []}
    build(conversation=conversation, page_context={"symbol": "600002"})
    assert conversation["active_constraints"] == {"symbol": "600001"}
